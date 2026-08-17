# AUDIT — extension de PicoClaw (Composio + Agnès AI + HF Spaces)

Document vivant : écarts entre la spécification d'origine (`INSTRUCTIONS.md`) et la réalité du dépôt / des API. Mis à jour à chaque lot.

- Dépôt audité : `sipeed/picoclaw`, branche `main`, cloné le 18/08/2026
- Fork de travail : `boccorouslan71-svg/picoclaw`
- État : **Lot A livré** (déploiement HF + Agnès AI par provider OpenAI-compatible). Lots B/C/D à venir.

---

## 1. Architecture réelle vs. supposée

| Élément | Supposé | Réel |
|---|---|---|
| Racine du code | `internal/...` | **`pkg/...`** (`cmd/picoclaw/internal/` pour la CLI seule) |
| Providers LLM | un package par vendor sous `internal/provider/` | **`pkg/providers/`** : dispatch par **protocole** dans un `switch` de `pkg/providers/factory_provider.go` |
| Contrat provider | `Chat`, `Stream`, `ListModels` | **`pkg/providers/types.go`** → `LLMProvider { Chat(ctx, messages, tools, model, options) (*LLMResponse, error); GetDefaultModel() string }`. Streaming via interfaces **optionnelles** (`StreamingProvider`, `StreamingEventProvider`). Pas de `ListModels`. |
| Registre d'outils | à créer | **`pkg/tools/registry.go`** → `ToolRegistry` (`Register`, `RegisterHidden`, `Execute`, `ToProviderDefs`, `SetAllowlist`, TTL de promotion) |
| MCP | `internal/mcp/` | **`pkg/mcp/`**, branché à la boucle par **`pkg/agent/agent_mcp.go`** |
| Telegram | à créer en Phase 4 | **déjà livré** : `pkg/channels/telegram/` (telego v1.10.0), commandes, MarkdownV2/HTML, tests |
| `/health` | à ajouter | **déjà livré** : `pkg/health/server.go` (`/health`, `/ready`, `/reload`), monté par `pkg/gateway/gateway.go` |
| Docker | Dockerfile à écrire | `docker/` existait déjà (`Dockerfile`, `Dockerfile.full`, `entrypoint.sh`, compose) |
| Secrets | `.security.yml` | **`pkg/config/security.go`** + `pkg/config/envkeys.go` + `pkg/credential/` |
| Version Go | 1.23-alpine | **`go 1.25.12`** — le Dockerfile de la spec ne compile pas |
| Clé de config des channels | `channels` | **`channel_list`** (`{enabled, type, allow_from, settings{...}}`) |
| Commande de lancement | `serve --config ...` | **`picoclaw gateway`** ; config via `PICOCLAW_CONFIG` / `PICOCLAW_HOME` |
| Port par défaut | — | 18790 (surchargé à 7860 par `PICOCLAW_GATEWAY_PORT` pour HF) |

**Conséquence majeure sur la Phase 2** : les vendors OpenAI-compatibles (groq, openrouter, litellm, lmstudio, zhipu, nvidia, venice…) partagent **un seul** provider, `pkg/providers/openai_compat`, et ne sont que des `case` du switch. Créer `internal/provider/agnes/{client,provider,types}.go` serait exactement le « système parallèle » que la spec interdit. Agnès AI se branche donc par configuration, et le Lot C se limitera à enregistrer le protocole `agnes` + sa base URL par défaut.

---

## 2. API Agnès AI — vérifiée en conditions réelles

Testé directement contre `https://apihub.agnes-ai.com` avec la clé du projet :

| Point | Résultat |
|---|---|
| Auth | `Authorization: Bearer <clé>` — conforme |
| `GET /v1/models` | `200`, format OpenAI (`{data:[{id,object,created,owned_by}],object:"list"}`) |
| `POST /v1/chat/completions` | `200`, format OpenAI (`choices[].message`, `usage`) |
| Modèles réels | `agnes-2.5-flash`, `agnes-2.5-pro`, `agnes-2.5-pro-alpha`, `agnes-2.0-flash`, `agnes-image-2.0-flash`, `agnes-image-2.1-flash`, `agnes-video-v2.0` |
| Function calling | **supporté** — `finish_reason: "tool_calls"`, `message.tool_calls[].function.{name,arguments}` au format OpenAI |
| Streaming | **supporté** — SSE `data: {...}` avec `object: "chat.completion.chunk"` et `choices[].delta` |
| Erreur d'auth | `401` avec `{"error":{"message":"...","type":"AgnesAI_error","code":""}}` — le champ `code` est vide, la classification doit se faire sur le statut HTTP, pas sur `code` |

**Écarts à retenir** :
1. Les réponses portent un champ non standard **`reasoning_content`** (dans `message` et dans les `delta` du stream). Le budget `max_tokens` est consommé par ce raisonnement : avec `max_tokens: 16`, `content` revient vide et `finish_reason: "length"`. → prévoir `max_tokens` ≥ 1024, et décider au Lot C si `reasoning_content` est mappé sur le canal « thinking » de PicoClaw ou ignoré.
2. `provider_specific_fields` et `metadata.weight_version` sont présents en plus du schéma OpenAI — ignorables.
3. La spec donnait `DefaultChatModel = "agnes-2.5-flash"` : confirmé existant.

---

## 3. API Composio — vérifiée en conditions réelles

| Point | Résultat |
|---|---|
| Base URL | **`https://backend.composio.dev/api/v3.1`** |
| Auth | header **`x-api-key`** (pas `Authorization`) ; `x-org-api-key` pour le niveau organisation |
| Découverte | `GET /tools?toolkit_slug=<SLUG>&limit=N&toolkit_versions=latest` → `{items:[{slug,name,description,input_parameters,output_parameters,version,toolkit}]}` |
| Toolkits | `GET /toolkits?limit=N` → `{items:[{name,slug,auth_schemes,meta{tools_count,...}}]}` |
| Exécution | `POST /tools/execute/{TOOL_SLUG}` body `{user_id, arguments, toolkit_versions}` |
| Compte non connecté | `HTTP 404`, `{"error":{"code":1810,"slug":"ActionExecute_ConnectedAccountNotFound","message":"No connected account found for user ID <uid> for toolkit <tk>","suggested_fix":"...COMPOSIO_INITIATE_CONNECTION"}}` |

**Écarts à retenir** :
1. La spec annonçait le meta-tool `COMPOSIO_MANAGE_CONNECTIONS` ; l'API v3.1 renvoie en `suggested_fix` **`COMPOSIO_INITIATE_CONNECTION`** et l'endpoint `/api/v3/connected_accounts`. Les deux existent dans l'écosystème Composio. → le Lot B détectera `code == 1810` / `slug == "ActionExecute_ConnectedAccountNotFound"` (signal fiable et documenté) puis tentera `COMPOSIO_MANAGE_CONNECTIONS`, avec repli sur `COMPOSIO_INITIATE_CONNECTION`.
2. `toolkit_versions` est **obligatoire** pour l'exécution manuelle (`latest` ou une version datée type `20260815_00`) — absent de la spec.
3. Les slugs sont en `SCREAMING_SNAKE_CASE` `{TOOLKIT}_{ACTION}`, mais le champ `toolkit.slug` est en minuscules (`github`) : ne pas confondre lors du filtrage par whitelist.
4. Point d'ancrage retenu côté PicoClaw : `pkg/tools/composio/`, enregistrement via `ToolRegistry.Register`, whitelist mappée sur `SetAllowlist` — ce qui règle nativement l'exigence « ne pas exposer 1000+ outils au LLM ».

---

## 4. Lot A — ce qui a été livré

| Fichier | Rôle |
|---|---|
| `Dockerfile` (racine) | Image HF Spaces : Go 1.25, `make build`, uid 1000, `0.0.0.0:7860`, healthcheck sur `/health` |
| `docker/hf-entrypoint.sh` | Rend `config.json` depuis le template via `envsubst` (les secrets HF ne sont que des variables d'env), nettoie le PID résiduel, lance `picoclaw gateway` |
| `config/config.hf.template.json` | Config complète : Agnès AI en `litellm/` + `api_base`, channel Telegram activé, workspace sous `/data` |
| `.env.example` | Toutes les variables (LLM, Composio, Telegram, Make, runtime) |
| `docs/GETTING_STARTED.md` | Build local, clés, flux Connect Link, déploiement HF, pinger, limitations |

**Pourquoi un template + `envsubst`** : dans PicoClaw, les `api_keys[]` du `model_list` et le `token` d'un channel ne sont pas surchargeables par variable d'environnement (les tags `env:"PICOCLAW_..."` ne couvrent pas les éléments de tableau). Sur HF Spaces, les secrets n'existent que comme variables d'env. Le rendu au runtime est donc le seul moyen de respecter la règle « jamais de secret en dur » sans patcher le loader de config.

---

## 5. Reste à faire

| Lot | Contenu | Dépendance |
|---|---|---|
| **B** | `pkg/tools/composio/` : `client.go`, `registry.go`, `executor.go`, `types.go`, tests `httptest` ; whitelist de toolkits ; flux Connect Link ; clé `tool_providers.composio` dans le schéma de config | aucune (API vérifiée) |
| **C** | protocole natif `agnes` dans `factory_provider.go` + base URL par défaut + décision sur `reasoning_content` ; tests | aucune |
| **D** | `pkg/persistence/` : `StateSync`, `WebhookSync` (best-effort, non bloquant), `LoadState` au boot ; scénario Make.com documenté | URL du webhook Make |
| **E** | `go build ./...` + `make test` sur l'ensemble, puis création du Space HF et premier déploiement | nom du Space HF |
