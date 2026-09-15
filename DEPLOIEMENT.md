# Déploiement du portfolio avec chatbot IA

Le site (`index.html`) reste hébergé gratuitement sur **GitHub Pages**, comme aujourd'hui.
Le chatbot et la mise à jour automatique des compétences ont besoin d'un petit serveur
(`backend/`) car GitHub Pages ne peut pas exécuter de code Python ni cacher de clé API.
Ce serveur tourne sur **Render** (gratuit).

## 1. Obtenir une clé API Gemini (gratuite, sans carte bancaire)

1. Va sur https://aistudio.google.com/app/apikey et connecte-toi avec un compte Google.
2. Clique sur **Create API key** (ou **Get API key**), choisis ou crée un projet Google Cloud si demandé.
3. Copie la clé générée — garde-la de côté, tu en auras besoin à l'étape 2.
4. Le tier gratuit ne demande pas de carte bancaire et suffit largement à l'usage d'un chatbot de
   portfolio (quelques dizaines de messages par jour). Les limites exactes (nombre de requêtes par
   minute/jour) sont visibles sur https://aistudio.google.com/rate-limit — si un jour tu les dépasses,
   le chatbot renverra juste un message d'erreur temporaire, rien de grave.

## 2. Déployer le backend sur Render

1. Pousse d'abord ce projet sur GitHub (voir section 4 plus bas si ce n'est pas encore fait).
2. Va sur https://render.com, crée un compte (tu peux te connecter avec GitHub directement).
3. Clique sur **New → Web Service**, choisis le repo `GodLouange.github.io`.
4. Render devrait détecter le fichier `render.yaml` à la racine et proposer la config automatiquement
   (nom : `godlouange-portfolio-backend`, dossier racine : `backend`). Si ce n'est pas automatique,
   configure manuellement :
   - **Root Directory** : `backend`
   - **Build Command** : `pip install -r requirements.txt`
   - **Start Command** : `gunicorn app:app`
   - **Plan** : Free
5. Dans l'onglet **Environment**, ajoute ces variables :
   - `GEMINI_API_KEY` = ta clé Gemini (étape 1)
   - `ADMIN_PASSWORD` = un mot de passe fort que toi seul connais (sert à protéger l'upload de CV)
   - `ALLOWED_ORIGINS` = `https://godlouange.github.io`
6. Clique sur **Create Web Service**. Le premier déploiement prend 2-3 minutes.
7. Une fois déployé, Render te donne une URL du type
   `https://godlouange-portfolio-backend.onrender.com` — note-la, c'est ton `BACKEND_URL`.

⚠️ Sur le plan gratuit, le serveur "s'endort" après 15 minutes d'inactivité et met ~30-50
secondes à se réveiller au premier message. Pour un salon d'alternance, pense à ouvrir le site
et envoyer un premier message test quelques minutes avant que les visiteurs arrivent, pour que
le serveur soit "réveillé".

## 3. Connecter le frontend au backend

Dans `index.html`, cherche cette ligne (proche de la fin du fichier, dans le bloc `<script>`) :

```js
const BACKEND_URL = (localStorage.getItem('portfolio_backend_url') || '').replace(/\/$/, '')
    || 'https://REMPLACE-PAR-TON-URL-RENDER.onrender.com';
```

Remplace `https://REMPLACE-PAR-TON-URL-RENDER.onrender.com` par l'URL Render obtenue à l'étape 2.6,
puis commit/push ce changement (voir section 4).

## 4. Mettre en ligne (commit + push)

Depuis le dossier du projet :

```bash
git add .
git commit -m "Portfolio pro : chatbot IA + compétences dynamiques"
git push origin main
```

Le site est automatiquement republié par GitHub Pages en 1-2 minutes.

## 5. Mettre à jour le CV depuis le site (compétences auto-actualisées)

1. Ouvre `https://godlouange.github.io/admin.html` (page non listée dans le menu, usage perso).
2. Renseigne :
   - **URL du backend** : ton URL Render (ex. `https://godlouange-portfolio-backend.onrender.com`)
   - **Mot de passe admin** : celui défini dans `ADMIN_PASSWORD` sur Render
   - **Fichier CV** : le nouveau PDF
3. Clique sur **Envoyer et mettre à jour**. Gemini relit le PDF et met à jour
   `data/cv-data.json` sur le serveur. La section **Compétences** du site se met à jour
   automatiquement au prochain chargement (pas besoin de republier le site).

⚠️ Attention : sur Render, le système de fichiers du plan gratuit n'est **pas persistant** entre
les redéploiements du service (il l'est tant que le service ne redémarre pas, mais un redeploy
ou une mise en veille prolongée peut réinitialiser le fichier). Pour un usage ponctuel avant un
salon, c'est très bien. Si tu veux que les mises à jour de CV soient permanentes à long terme,
dis-le-moi : on pourra brancher un petit stockage externe (ex. une base gratuite comme Supabase
ou un simple commit automatique sur GitHub) pour que `cv-data.json` survive aux redéploiements.

## 6. Tester en local avant de déployer (optionnel)

```bash
cd backend
cp .env.example .env      # puis édite .env avec ta vraie clé et ton mot de passe
source ../env/bin/activate  # l'environnement virtuel existe déjà dans le projet
pip install -r requirements.txt
python app.py
```

Le backend tourne alors sur `http://localhost:5000`. Dans `index.html`, tu peux temporairement
mettre `const BACKEND_URL = 'http://localhost:5000';` et ouvrir le site avec un petit serveur local
(`python -m http.server 8000`) pour tester le chatbot avant de déployer.
