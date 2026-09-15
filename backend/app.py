"""
Backend du portfolio de God-Louange Thombet-Kende.

Fournit :
- GET  /api/cv-data          -> sert les données du CV (data/cv-data.json) au frontend
- POST /api/chat             -> chatbot recruteur, répond à partir des données du CV via l'API Gemini (gratuite)
- POST /api/admin/upload-cv  -> (protégé) reçoit un nouveau CV en PDF, extrait le texte,
                                 le fait restructurer par Gemini au format cv-data.json, et sauvegarde

Le frontend (GitHub Pages, statique) appelle ce backend en cross-origin (CORS).
Utilise l'API Google Gemini (tier gratuit, pas de carte bancaire requise) via google-genai.
"""
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify, request
from flask_cors import CORS
from google import genai
from pypdf import PdfReader

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_PATH = BASE_DIR / "data" / "cv-data.json"

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
CHAT_MODEL = os.environ.get("CHAT_MODEL", "gemini-3.6-flash")
# Modèle de secours utilisé si CHAT_MODEL est surchargé (erreur 503) — un modèle "lite"
# est généralement moins soumis à la forte demande du tier gratuit.
CHAT_MODEL_FALLBACK = os.environ.get("CHAT_MODEL_FALLBACK", "gemini-2.5-flash-lite")
# Origines autorisées à appeler ce backend (ton site GitHub Pages + dev local)
ALLOWED_ORIGINS = [
    o.strip()
    for o in os.environ.get(
        "ALLOWED_ORIGINS",
        "https://godlouange.github.io,http://localhost:5500,http://127.0.0.1:5500,http://localhost:8000,http://127.0.0.1:8000",
    ).split(",")
    if o.strip()
]

app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": ALLOWED_ORIGINS}})

_client = None


def get_client():
    """Crée le client Gemini à la demande (pour ne pas planter au démarrage si la clé manque)."""
    global _client
    if _client is None:
        if not GEMINI_API_KEY:
            raise RuntimeError("GEMINI_API_KEY manquante côté serveur.")
        _client = genai.Client(api_key=GEMINI_API_KEY)
    return _client


def _is_retryable(exc: Exception) -> bool:
    """503 (surcharge temporaire) et 429 (quota atteint) valent la peine d'être retentés."""
    msg = str(exc)
    return "503" in msg or "UNAVAILABLE" in msg or "429" in msg or "RESOURCE_EXHAUSTED" in msg


def _speed_config_for(model: str) -> dict:
    """Désactive/réduit le 'raisonnement' interne du modèle pour minimiser la latence :
    inutile pour un chatbot de FAQ basé sur un profil fixe. Le nom du paramètre dépend
    de la génération du modèle (Gemini 3 : thinking_level, Gemini 2.5 : thinking_budget)."""
    if "gemini-3" in model:
        return {"thinking_config": {"thinking_level": "minimal"}}
    if "flash" in model and "lite" not in model:
        return {"thinking_config": {"thinking_budget": 0}}
    return {}


def generate_with_retry(client, contents, config, max_retries=2):
    """Appelle Gemini avec 2 tentatives sur le modèle principal (backoff court), puis
    bascule sur le modèle de secours si le modèle principal reste surchargé."""
    last_exc = None
    for model in (CHAT_MODEL, CHAT_MODEL_FALLBACK):
        model_config = {**config, **_speed_config_for(model)}
        for attempt in range(max_retries):
            try:
                return client.models.generate_content(model=model, contents=contents, config=model_config)
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if not _is_retryable(exc):
                    raise
                time.sleep(0.6 * (attempt + 1))
        # le modèle principal a échoué max_retries fois -> on essaie le modèle de secours
    raise last_exc


def load_cv_data() -> dict:
    if not DATA_PATH.exists():
        return {}
    with open(DATA_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_cv_data(data: dict) -> None:
    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def build_system_prompt(cv: dict) -> str:
    """Construit le system prompt du chatbot à partir des données du CV, à jour à chaque requête."""
    return f"""Tu es l'assistant virtuel du portfolio de {cv.get('identity', {}).get('full_name', 'ce candidat')}.
Tu réponds, en français, aux questions de recruteurs ou de visiteurs sur son profil professionnel,
lors d'un salon de l'alternance ou en ligne.

Règles :
- Réponds UNIQUEMENT à partir des informations ci-dessous (son CV / profil). N'invente rien.
- Si une question sort du cadre professionnel (infos personnelles sensibles, sujets hors profil), réponds
  poliment que tu ne peux pas répondre à ça et recentre sur le profil professionnel.
- Sois concis, chaleureux et professionnel — des réponses de 2 à 5 phrases, pas de longs pavés.
- Tu peux mettre en avant sa disponibilité pour une alternance et l'inviter à le contacter par email ou téléphone.
- Si on te demande un contact, donne : {cv.get('identity', {}).get('email', '')} / {cv.get('identity', {}).get('phone', '')}.

Voici les données à jour de son profil (format JSON) :
{json.dumps(cv, ensure_ascii=False, indent=2)}
"""


@app.get("/api/health")
def health():
    return jsonify({"status": "ok"})


@app.get("/api/cv-data")
def get_cv_data():
    return jsonify(load_cv_data())


@app.post("/api/chat")
def chat():
    body = request.get_json(silent=True) or {}
    message = (body.get("message") or "").strip()
    history = body.get("history") or []  # liste de {role, content}

    if not message:
        return jsonify({"error": "message manquant"}), 400
    if len(message) > 2000:
        return jsonify({"error": "message trop long"}), 400

    cv = load_cv_data()
    system_prompt = build_system_prompt(cv)

    # On ne garde que les derniers échanges pour rester léger, et on valide le format
    safe_history = [
        {"role": m.get("role"), "content": str(m.get("content", ""))[:2000]}
        for m in history[-10:]
        if m.get("role") in ("user", "assistant") and m.get("content")
    ]

    # Gemini utilise "model" au lieu de "assistant" pour le rôle du bot
    contents = []
    for m in safe_history:
        role = "model" if m["role"] == "assistant" else "user"
        contents.append({"role": role, "parts": [{"text": m["content"]}]})
    contents.append({"role": "user", "parts": [{"text": message}]})

    try:
        client = get_client()
        response = generate_with_retry(
            client, contents,
            config={"system_instruction": system_prompt, "max_output_tokens": 500},
        )
        reply = (response.text or "").strip()
        if not reply:
            raise RuntimeError("réponse vide du modèle")
    except Exception as exc:  # noqa: BLE001 - on veut renvoyer une erreur lisible au frontend
        return jsonify({"error": f"Le chatbot est momentanément indisponible, réessaie dans quelques secondes ({exc})"}), 502

    return jsonify({"reply": reply})


@app.post("/api/admin/upload-cv")
def upload_cv():
    """Reçoit un nouveau CV en PDF, en extrait le texte, et demande à Gemini de le
    restructurer selon le schéma de data/cv-data.json. Protégé par mot de passe admin."""
    if not ADMIN_PASSWORD:
        return jsonify({"error": "ADMIN_PASSWORD non configuré côté serveur"}), 500

    provided = request.headers.get("X-Admin-Password", "")
    if provided != ADMIN_PASSWORD:
        return jsonify({"error": "Mot de passe admin invalide"}), 401

    if "file" not in request.files:
        return jsonify({"error": "Aucun fichier reçu (champ 'file' attendu)"}), 400

    pdf_file = request.files["file"]
    try:
        reader = PdfReader(pdf_file.stream)
        raw_text = "\n".join(page.extract_text() or "" for page in reader.pages).strip()
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"Impossible de lire le PDF ({exc})"}), 400

    if not raw_text:
        return jsonify({"error": "Aucun texte extrait du PDF (scan image non supporté)"}), 400

    current = load_cv_data()
    schema_example = json.dumps(current, ensure_ascii=False, indent=2)

    extraction_prompt = f"""Voici le texte brut extrait d'un nouveau CV :
---
{raw_text}
---

Voici le schéma JSON actuellement utilisé par le portfolio (structure à respecter exactement,
mêmes clés, même format) :
---
{schema_example}
---

Génère UNIQUEMENT le JSON mis à jour (mêmes clés que le schéma), avec les informations du nouveau
CV. Garde les champs qui ne sont plus présents dans le nouveau CV inchangés si tu n'es pas sûr,
mais priorise le nouveau contenu pour compétences, expériences et formations. Ne mets aucun texte
avant ou après le JSON, pas de balises markdown."""

    try:
        client = get_client()
        response = generate_with_retry(
            client,
            contents=[{"role": "user", "parts": [{"text": extraction_prompt}]}],
            config={"max_output_tokens": 4000, "response_mime_type": "application/json"},
        )
        raw_json = (response.text or "").strip()
        if raw_json.startswith("```"):
            raw_json = raw_json.strip("`")
            if raw_json.lower().startswith("json"):
                raw_json = raw_json[4:]
        new_data = json.loads(raw_json)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"Échec de l'extraction/restructuration ({exc})"}), 502

    new_data["updated_at"] = datetime.now(timezone.utc).isoformat()
    new_data["source"] = "pdf-upload"
    save_cv_data(new_data)

    return jsonify({"status": "ok", "data": new_data})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=os.environ.get("FLASK_DEBUG") == "1")
