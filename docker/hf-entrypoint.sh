#!/bin/sh
# ============================================================
# Entrypoint Hugging Face Spaces.
#
# Rôle : rendre le config.json à partir du template + des secrets HF
# (les secrets HF n'existent que comme variables d'environnement, et les clés
# api_keys[] du model_list ne sont pas surchargeables par variable d'env dans
# PicoClaw), puis lancer la gateway.
#
# Aucune clé n'est écrite dans l'image : la substitution a lieu au runtime,
# dans /data (éphémère), jamais dans une couche Docker.
# ============================================================
set -e

CONFIG_DIR="${PICOCLAW_HOME:-/data/.picoclaw}"
TEMPLATE="${PICOCLAW_CONFIG_TEMPLATE:-/app/config.hf.template.json}"

# Le workspace n'est plus code en dur dans le template : il derive de PICOCLAW_HOME
# pour que la meme image tourne sur HF (/data), Render (/data) ou en local (/tmp).
: "${PICOCLAW_WORKSPACE:=${CONFIG_DIR}/workspace}"
export PICOCLAW_WORKSPACE
mkdir -p "${CONFIG_DIR}" "${PICOCLAW_WORKSPACE}"

if [ -z "${AGNES_API_KEY}" ]; then
    echo "[hf-entrypoint] ATTENTION : AGNES_API_KEY est vide — le provider LLM échouera." >&2
fi
if [ -z "${TELEGRAM_BOT_TOKEN}" ]; then
    echo "[hf-entrypoint] ATTENTION : TELEGRAM_BOT_TOKEN est vide — le channel Telegram ne démarrera pas." >&2
fi

# Valeur de repli pour que le template reste du JSON valide même si la variable
# n'est pas définie (allow_from vide = aucun filtre d'utilisateur).
: "${TELEGRAM_ALLOWED_USER_ID:=}"
export TELEGRAM_ALLOWED_USER_ID

# Ne jamais écraser un config.json déjà présent ET modifié à la main
# (utile si le stockage persistant HF est activé).
if [ -f "${CONFIG_DIR}/config.json" ] && [ "${PICOCLAW_HF_FORCE_RENDER}" != "1" ]; then
    echo "[hf-entrypoint] config.json existant conservé (PICOCLAW_HF_FORCE_RENDER=1 pour le régénérer)."
else
    envsubst < "${TEMPLATE}" > "${CONFIG_DIR}/config.json"
    chmod 600 "${CONFIG_DIR}/config.json"
    echo "[hf-entrypoint] config.json généré depuis le template."
fi

# PID résiduel d'un conteneur tué (OOM, redéploiement) : bloquerait le démarrage.
rm -f "${CONFIG_DIR}/.picoclaw.pid"

# ------------------------------------------------------------
# Port d'ecoute : Render (et la plupart des PaaS) imposent $PORT.
# Hugging Face attend 7860. On respecte PORT quand il existe.
# ------------------------------------------------------------
if [ -n "${PORT}" ]; then
    PICOCLAW_GATEWAY_PORT="${PORT}"
fi
: "${PICOCLAW_GATEWAY_PORT:=7860}"
: "${PICOCLAW_GATEWAY_HOST:=0.0.0.0}"
export PICOCLAW_GATEWAY_PORT PICOCLAW_GATEWAY_HOST

# ------------------------------------------------------------
# Reveil actif : sur une offre gratuite Render, le service s'endort
# apres ~15 min sans trafic entrant. Le long polling Telegram est du
# trafic sortant et ne compte pas. On s'appelle donc soi-meme via
# l'URL publique fournie par Render, ce qui est bien du trafic entrant.
# Aucun service tiers requis.
# ------------------------------------------------------------
: "${PICOCLAW_KEEPALIVE_URL:=${RENDER_EXTERNAL_URL}}"
: "${PICOCLAW_KEEPALIVE_INTERVAL:=300}"
if [ -n "${PICOCLAW_KEEPALIVE_URL}" ]; then
    echo "[hf-entrypoint] reveil actif : ${PICOCLAW_KEEPALIVE_URL}/health toutes les ${PICOCLAW_KEEPALIVE_INTERVAL}s"
    (
        sleep 20
        while true; do
            code=$(curl -s -o /dev/null -m 20 -w '%{http_code}' "${PICOCLAW_KEEPALIVE_URL}/health" || echo "000")
            echo "[keepalive] ${PICOCLAW_KEEPALIVE_URL}/health -> ${code}"
            sleep "${PICOCLAW_KEEPALIVE_INTERVAL}"
        done
    ) &
else
    echo "[hf-entrypoint] reveil actif desactive (ni PICOCLAW_KEEPALIVE_URL ni RENDER_EXTERNAL_URL)."
fi

echo "[hf-entrypoint] démarrage : picoclaw gateway sur ${PICOCLAW_GATEWAY_HOST}:${PICOCLAW_GATEWAY_PORT}"
exec picoclaw gateway "$@"
