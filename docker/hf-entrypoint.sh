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

mkdir -p "${CONFIG_DIR}/workspace"

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

echo "[hf-entrypoint] démarrage : picoclaw gateway sur ${PICOCLAW_GATEWAY_HOST}:${PICOCLAW_GATEWAY_PORT}"
exec picoclaw gateway "$@"
