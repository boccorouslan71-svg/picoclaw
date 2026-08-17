# GETTING_STARTED — PicoClaw + Agnès AI + Composio sur Hugging Face Spaces

Guide de bout en bout : build local, clés API, connexion des comptes sociaux, déploiement HF Spaces, maintien en éveil, limitations.

> Ce guide décrit **le Lot A** (déploiement + Agnès AI via le provider OpenAI-compatible existant). Les lots suivants — bridge Composio natif (Lot B), protocole `agnes` natif (Lot C), persistance Make.com (Lot D) — ajoutent des sections marquées « à venir ».

---

## 1. Build local (à valider avant tout déploiement)

Prérequis : **Go 1.25+** (le `go.mod` déclare `go 1.25.12`), `make`, `git`.

```bash
git clone https://github.com/boccorouslan71-svg/picoclaw.git
cd picoclaw
make deps      # go mod download
make build     # binaire dans ./build/picoclaw
make test      # tests unitaires
```

Le binaire lit sa config dans `$PICOCLAW_HOME/config.json` (défaut `~/.picoclaw/config.json`). Premier lancement guidé :

```bash
./build/picoclaw onboard      # crée la config + le workspace
./build/picoclaw gateway      # démarre la gateway (channels + /health)
```

---

## 2. Obtenir et configurer les clés

Copier `.env.example` en `.env` et remplir. **Aucune clé ne doit finir dans un fichier committé.**

### 2.1 Agnès AI

- Base URL : `https://apihub.agnes-ai.com`
- Auth : header `Authorization: Bearer <clé>`
- API **compatible OpenAI** — vérifié en conditions réelles :

```bash
curl https://apihub.agnes-ai.com/v1/models -H "Authorization: Bearer $AGNES_API_KEY"
```

Modèles retournés par le compte : `agnes-2.5-flash`, `agnes-2.5-pro`, `agnes-2.5-pro-alpha`, `agnes-2.0-flash`, `agnes-image-2.0-flash`, `agnes-image-2.1-flash`, `agnes-video-v2.0`.

Confirmé également : `tool_calls` (function calling) et le streaming SSE fonctionnent. Particularité : les réponses portent un champ `reasoning_content` en plus de `content` (le texte visible peut être vide si `max_tokens` est consommé par le raisonnement — prévoir `max_tokens` ≥ 1024).

Dans `config.json`, Agnès AI se branche **sans modification du code** en réutilisant le provider OpenAI-compatible de PicoClaw :

```json
{
  "model_name": "agnes-flash",
  "model": "litellm/agnes-2.5-flash",
  "api_keys": ["<AGNES_API_KEY>"],
  "api_base": "https://apihub.agnes-ai.com/v1"
}
```

Le préfixe natif `agnes/...` arrivera avec le Lot C ; il ne changera que le nom du protocole, pas le comportement.

### 2.2 Composio

- Base API : `https://backend.composio.dev/api/v3.1`
- Auth : header **`x-api-key`** (et non `Authorization`)
- Clé à créer depuis le dashboard Composio, puis `COMPOSIO_API_KEY` dans `.env`
- `COMPOSIO_USER_ID` : identifiant libre et stable qui rattache les comptes connectés (ex. `rouslan-picoclaw`)

### 2.3 Telegram

1. Parler à [@BotFather](https://t.me/BotFather) → `/newbot` → récupérer le token → `TELEGRAM_BOT_TOKEN`.
2. Récupérer ton user id numérique via [@userinfobot](https://t.me/userinfobot) → `TELEGRAM_ALLOWED_USER_ID`.

> Laisser `allow_from` vide sur un Space public expose le bot à n'importe qui. Toujours renseigner ton user id.

---

## 3. Connecter les comptes sociaux (Twitter/X, LinkedIn, Notion, Gmail)

**Aucune étape préalable dans le dashboard Composio.** Le flux est déclenché par l'usage :

1. Tu demandes une action au bot Telegram (« publie ce tweet »).
2. Si aucun compte n'est connecté pour ce toolkit sous ton `user_id`, l'API Composio répond `HTTP 404` avec `code: 1810` / `slug: ActionExecute_ConnectedAccountNotFound`.
3. Le bridge traite ce cas comme un **onboarding, pas comme une erreur** : il appelle le meta-tool de connexion et récupère un **Connect Link** hébergé par Composio.
4. Le lien est renvoyé au LLM comme résultat de tool call, avec pour instruction de le transmettre à l'utilisateur → il s'affiche dans le chat Telegram.
5. Tu cliques, tu t'authentifies chez le fournisseur, Composio stocke le token. Toutes les actions suivantes sur ce toolkit passent sans re-demander.

> Écart constaté vs. la spécification initiale : le message d'erreur de l'API v3.1 pointe vers **`COMPOSIO_INITIATE_CONNECTION`** (et l'endpoint `/api/v3/connected_accounts`), là où la doc des meta-tools documente `COMPOSIO_MANAGE_CONNECTIONS`. Le bridge (Lot B) tentera `COMPOSIO_MANAGE_CONNECTIONS` puis retombera sur `COMPOSIO_INITIATE_CONNECTION`. Détail dans `docs/AUDIT.md`.

Filtrage des toolkits : Composio expose plus de 1000 outils. La whitelist de la config (`toolkits: ["TWITTER","LINKEDIN","NOTION","GMAIL"]`) est indispensable pour ne pas saturer le contexte du LLM.

---

## 4. Déployer sur Hugging Face Spaces

### 4.1 Créer le Space

1. huggingface.co → **New Space**.
2. SDK : **Docker** → template *Blank*. Hardware : **CPU basic (gratuit)**.
3. Visibilité : **Private** recommandé (le Space embarque un bot connecté à tes comptes).

### 4.2 Le README du Space

HF lit la configuration du Space dans le frontmatter YAML du `README.md` **à la racine**. Le port doit correspondre à celui exposé par l'image :

```yaml
---
title: PicoClaw
emoji: 🦞
colorFrom: indigo
colorTo: purple
sdk: docker
app_port: 7860
pinned: false
---
```

### 4.3 Pousser le code

```bash
git remote add space https://huggingface.co/spaces/<ton-compte>/<nom-du-space>
git push space main
```

Le `Dockerfile` à la racine du dépôt est celui utilisé par HF : Go 1.25, build via `make build`, exécution en uid 1000, écoute sur `0.0.0.0:7860`, `/health` exposé.

### 4.4 Déclarer les secrets

Settings → **Variables and secrets** → un secret par ligne de `.env.example` (au minimum `AGNES_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOWED_USER_ID`, et `COMPOSIO_API_KEY` + `COMPOSIO_USER_ID` dès le Lot B).

Au démarrage, `docker/hf-entrypoint.sh` génère `$PICOCLAW_HOME/config.json` à partir de `config/config.hf.template.json` en y injectant ces variables (`envsubst`). Les clés ne sont donc **jamais** dans une couche de l'image ni dans le dépôt. Pour forcer la régénération après un changement de secret : variable `PICOCLAW_HF_FORCE_RENDER=1`.

---

## 5. Garder le Space éveillé (pinger)

Un Space gratuit se met en veille après inactivité. L'endpoint de santé existe déjà dans PicoClaw : `GET /health` (avec `/ready` et `/reload`).

1. Créer un compte UptimeRobot (ou équivalent : Better Uptime, cron-job.org).
2. Nouveau monitor **HTTP(s)**, URL `https://<compte>-<space>.hf.space/health`, intervalle 5 min.

> À dire clairement : ça réduit les mises en veille, ça ne les supprime pas. Le pinging reste soumis aux quotas d'usage HF, et un Space privé exige un token d'accès dans la requête — ce n'est pas garanti à 100 %.

---

## 6. Limitations connues

- **Persistance non garantie (plan gratuit)** — `/data` est éphémère : tout redéploiement, sortie de veille ou maintenance HF réinitialise le contenu (historique de conversation, mémoire de session, auth store). Le stockage persistant HF est une option payante. La config statique, elle, est versionnée dans Git et donc toujours reconstruite au boot. Pour l'état dynamique, voir le Lot D (journal externe via webhook Make.com → Google Sheets) — à documenter ici quand il sera livré. Ce n'est pas une solution magique : c'est un journal + un rechargement best-effort.
- **Quotas Agnès AI** — quotas par clé côté fournisseur ; en cas de 429 le provider PicoClaw applique déjà retry/backoff (`max_llm_retries`, `llm_retry_backoff_secs`) et peut basculer sur un autre modèle du `model_list`.
- **Quotas Composio** — 2 000 à 10 000 requêtes/minute selon le plan ; les erreurs de permission ou de rate limit sont remontées au LLM comme résultats d'outil structurés, jamais en crash.
- **HF Spaces CPU Basic** — 2 vCPU / 16 Go, pas de GPU, veille après inactivité, build limité en durée. Le build Go complet de PicoClaw (≈300 dépendances) est long au premier déploiement.
- **Un seul conteneur** — pas de scaling horizontal ; les sessions vivent dans le process.
