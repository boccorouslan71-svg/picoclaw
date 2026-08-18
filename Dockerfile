# ============================================================
# PicoClaw — image Hugging Face Spaces (Docker SDK, CPU Basic)
#
# HF Spaces exige :
#   - un Dockerfile à la racine du repo
#   - une écoute sur le port déclaré dans app_port (ici 7860)
#   - un process qui tourne en uid 1000 (utilisateur non-root)
#
# Base Go 1.25 : go.mod du dépôt déclare `go 1.25.12`.
# ============================================================

# ---- Build stage ----
FROM golang:1.25-alpine AS builder

RUN apk add --no-cache git make

WORKDIR /src

# Cache des dépendances (couche invalidée seulement si go.mod/go.sum changent)
COPY go.mod go.sum ./
RUN go mod download

COPY . .

# `make build` applique les ldflags de version et les build tags du projet
# (goolm,stdjson) — ne pas remplacer par un `go build` nu.
RUN make build

# ---- Runtime stage ----
FROM alpine:3.23

# gettext fournit envsubst, utilisé par l'entrypoint pour injecter les secrets HF
# dans le config.json au démarrage (les secrets HF ne sont que des variables d'env).
RUN apk add --no-cache ca-certificates tzdata curl gettext

COPY --from=builder /src/build/picoclaw /usr/local/bin/picoclaw
COPY docker/hf-entrypoint.sh /hf-entrypoint.sh
COPY config/config.hf.template.json /app/config.hf.template.json
RUN chmod +x /hf-entrypoint.sh

ENV HOME=/home/picoclaw \
    PICOCLAW_HOME=/data/.picoclaw \
    PICOCLAW_GATEWAY_HOST=0.0.0.0 \
    PICOCLAW_GATEWAY_PORT=7860

# HF Spaces exécute le conteneur en uid 1000.
RUN mkdir -p /data /home/picoclaw && chown -R 1000:1000 /data /home/picoclaw /app
USER 1000

# Persistance : /data n'est durable que si le "persistent storage" HF (payant)
# est activé. Sans lui, tout /data est réinitialisé à chaque redéploiement ou
# sortie de veille du Space — voir docs/GETTING_STARTED.md § Limitations.
VOLUME ["/data"]

# HF impose 7860 ; Render fournit $PORT au runtime (voir hf-entrypoint.sh).
EXPOSE 7860

HEALTHCHECK --interval=60s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS "http://localhost:${PORT:-${PICOCLAW_GATEWAY_PORT:-7860}}/health" || exit 1

ENTRYPOINT ["/hf-entrypoint.sh"]
