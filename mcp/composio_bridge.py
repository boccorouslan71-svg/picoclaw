#!/usr/bin/env python3
"""Pont MCP (stdio) vers l'API Composio v3.

Expose un petit nombre d'outils TOUJOURS visibles pour l'agent, qui donnent
accès à l'intégralité du catalogue Composio (1200+ toolkits) :

  composio_search_tools     recherche un outil dans tout le catalogue
  composio_get_tool_schema  paramètres attendus par un outil
  composio_execute_tool     exécute n'importe quel outil par son identifiant
  composio_connect_app      génère le lien d'autorisation d'une application
  composio_list_connections liste les comptes déjà connectés

Aucune dépendance externe : stdlib uniquement (json + urllib).
Configuration par variables d'environnement :
  COMPOSIO_API_KEY  (requis)
  COMPOSIO_USER_ID  (défaut: "default")
"""

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

API = "https://backend.composio.dev/api/v3"
KEY = os.environ.get("COMPOSIO_API_KEY", "").strip()
USER = os.environ.get("COMPOSIO_USER_ID", "default").strip() or "default"
TIMEOUT = float(os.environ.get("COMPOSIO_TIMEOUT", "60"))


def log(msg):
    sys.stderr.write(f"[composio-bridge] {msg}\n")
    sys.stderr.flush()


class ApiError(Exception):
    pass


def api(method, path, payload=None, query=None):
    if not KEY:
        raise ApiError("COMPOSIO_API_KEY absente de l'environnement du serveur MCP.")
    url = f"{API}{path}"
    if query:
        url += "?" + urllib.parse.urlencode({k: v for k, v in query.items() if v is not None})
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        headers={"x-api-key": KEY, "Content-Type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            body = resp.read().decode()
        return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode()[:600]
        raise ApiError(f"HTTP {exc.code} sur {method} {path} : {detail}") from exc
    except urllib.error.URLError as exc:
        raise ApiError(f"Réseau indisponible vers Composio : {exc.reason}") from exc


# --------------------------------------------------------------------------- #
# Recherche d'outils
# --------------------------------------------------------------------------- #

# Le moteur de recherche Composio n'indexe que l'anglais : ces équivalences
# évitent qu'une requête formulée en français renvoie zéro résultat.
FR_EN = {
    "creer": "create", "créer": "create", "cree": "create", "crée": "create",
    "ajouter": "add", "envoyer": "send", "envoie": "send", "envoi": "send",
    "lire": "read", "lister": "list", "liste": "list", "afficher": "list",
    "supprimer": "delete", "effacer": "delete", "modifier": "update",
    "mettre": "update", "jour": "update", "chercher": "search",
    "recherche": "search", "trouver": "find", "obtenir": "get",
    "message": "message", "page": "page", "fichier": "file", "dossier": "folder",
    "tache": "task", "tâche": "task", "ticket": "issue", "probleme": "issue",
    "problème": "issue", "depot": "repository", "dépôt": "repository",
    "courriel": "email", "mail": "email", "calendrier": "calendar",
    "evenement": "event", "événement": "event", "reunion": "meeting",
    "réunion": "meeting", "commentaire": "comment", "tableau": "board",
    "feuille": "sheet", "ligne": "row", "colonne": "column", "canal": "channel",
    "salon": "channel", "utilisateur": "user", "contact": "contact",
    "facture": "invoice", "paiement": "payment", "client": "customer",
    "base": "database", "donnees": "data", "données": "data",
    "publier": "post", "publication": "post", "telecharger": "upload",
    "télécharger": "upload", "notes": "note", "note": "note",
}
STOP_FR = {"un", "une", "le", "la", "les", "de", "des", "du", "dans", "sur",
           "a", "à", "au", "aux", "et", "ou", "pour", "avec", "mon", "ma",
           "mes", "nouveau", "nouvelle", "vers", "il", "elle", "je", "que"}


def _normalize_query(query):
    """Traduit grossièrement une requête française vers des mots-clés anglais."""
    words = [w.strip(".,;:!?«»\"'()").lower() for w in (query or "").split()]
    out, changed = [], False
    for w in words:
        if not w or w in STOP_FR:
            changed = changed or bool(w)
            continue
        if w in FR_EN:
            out.append(FR_EN[w])
            changed = True
        else:
            out.append(w)
    normalized = " ".join(out)
    return normalized, bool(changed and normalized and normalized != (query or "").strip().lower())


def _fetch_tools(search, limit, toolkit):
    data = api("GET", "/tools", query={
        "search": search or None,
        "limit": limit,
        "toolkit_slug": toolkit or None,
    })
    return data.get("items") or []


def t_search_tools(query, limit=10, toolkit_slug=None):
    limit = max(1, min(int(limit or 10), 30))
    toolkit = (toolkit_slug or "").strip().lower() or None
    attempts = [(query, "requête d'origine")]
    normalized, changed = _normalize_query(query)
    if changed:
        attempts.append((normalized, f"requête traduite en anglais (« {normalized} »)"))
    items, used = [], attempts[0][1]
    for term, label in attempts:
        items = _fetch_tools(term, limit, toolkit)
        if items:
            used = label
            break
    if not items and toolkit:
        items = _fetch_tools(None, limit, toolkit)
        used = f"catalogue complet de l'application {toolkit}"
    if not items:
        return (f"Aucun outil trouvé pour « {query} ». Réessayer avec des mots-clés anglais "
                "(ex. « create issue », « send message ») ou préciser toolkit_slug "
                "(ex. « notion », « slack », « discordbot »).")
    lines = [f"{len(items)} outil(s) trouvé(s) — {used} :"]
    for it in items:
        slug = it.get("slug", "?")
        tk = (it.get("toolkit") or {}).get("slug", "?")
        desc = (it.get("description") or "").strip().replace("\n", " ")
        if len(desc) > 160:
            desc = desc[:157] + "..."
        lines.append(f"- {slug} [{tk}] : {desc}")
    lines.append("\nUtiliser composio_get_tool_schema pour les paramètres, "
                 "puis composio_execute_tool pour exécuter.")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Autres outils
# --------------------------------------------------------------------------- #

def t_get_tool_schema(tool_slug):
    slug = (tool_slug or "").strip().upper()
    data = api("GET", f"/tools/{urllib.parse.quote(slug)}")
    params = data.get("input_parameters") or {}
    props = params.get("properties") or {}
    required = params.get("required") or []
    lines = [f"Outil {slug} — {(data.get('description') or '').strip()[:300]}", "", "Paramètres :"]
    if not props:
        lines.append("- (aucun)")
    for name, spec in props.items():
        typ = spec.get("type", "?")
        req = " (requis)" if name in required else ""
        desc = (spec.get("description") or "").strip().replace("\n", " ")[:120]
        lines.append(f"- {name} : {typ}{req} — {desc}")
    return "\n".join(lines)


def t_execute_tool(tool_slug, arguments=None):
    slug = (tool_slug or "").strip().upper()
    args = arguments or {}
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            raise ApiError("Le paramètre arguments doit être un objet JSON.")
    data = api("POST", f"/tools/execute/{urllib.parse.quote(slug)}",
               payload={"user_id": USER, "arguments": args})
    if data.get("successful") is False:
        err = str(data.get("error") or "")
        hint = ""
        if "ConnectedAccountNotFound" in err or "connected account" in err.lower():
            hint = ("\nAucun compte connecté pour cette application. "
                    "Appeler composio_connect_app avec le nom de l'application "
                    "pour obtenir le lien d'autorisation, puis réessayer.")
        return f"Échec de {slug} : {err}{hint}"
    out = json.dumps(data.get("data"), ensure_ascii=False, indent=2)
    if len(out) > 12000:
        out = out[:12000] + "\n... (résultat tronqué)"
    return f"{slug} exécuté avec succès.\n{out}"


def _find_or_create_auth_config(toolkit):
    existing = api("GET", "/auth_configs", query={"toolkit_slug": toolkit, "limit": 20})
    for item in (existing.get("items") or []):
        if (item.get("toolkit") or {}).get("slug", "").lower() == toolkit:
            return item["id"], False
    created = api("POST", "/auth_configs", payload={
        "toolkit": {"slug": toolkit},
        "auth_config": {"type": "use_composio_managed_auth"},
    })
    cfg = created.get("auth_config") or created
    return cfg["id"], True


def _extract_link(data):
    for path in (
        ("redirect_url",), ("redirectUrl",), ("link",), ("url",),
        ("connection_data", "val", "redirectUrl"),
        ("connected_account", "redirect_url"),
    ):
        cur = data
        for key in path:
            cur = cur.get(key) if isinstance(cur, dict) else None
            if cur is None:
                break
        if isinstance(cur, str) and cur.startswith("http"):
            return cur
    return None


def t_connect_app(toolkit_slug):
    toolkit = (toolkit_slug or "").strip().lower()
    if not toolkit:
        raise ApiError("Indiquer le nom de l'application, par exemple « notion ».")
    auth_id, created = _find_or_create_auth_config(toolkit)
    data = api("POST", "/connected_accounts/link", payload={
        "auth_config_id": auth_id,
        "user_id": USER,
    })
    link = _extract_link(data)
    status = (data.get("status") or "").upper()
    if not link:
        if status in ("ACTIVE", "CONNECTED"):
            return f"{toolkit} est déjà connecté pour cet utilisateur — aucun lien nécessaire."
        return (f"Configuration prête pour {toolkit} mais aucun lien renvoyé par Composio. "
                f"Réponse : {json.dumps(data, ensure_ascii=False)[:400]}")
    note = " (nouvelle configuration créée)" if created else ""
    return (f"Lien d'autorisation pour {toolkit}{note} :\n{link}\n\n"
            "RÈGLE DE TRANSMISSION, à respecter mot pour mot dans la réponse à "
            "l'utilisateur : recopier l'URL ci-dessus telle quelle, seule sur sa "
            "propre ligne, sans gras, sans astérisques, sans backticks, sans "
            "crochets Markdown et sans ponctuation collée avant ou après. Tout "
            "caractère ajouté à l'URL est interprété comme faisant partie du jeton, "
            "et Composio rejette alors la page d'autorisation.\n"
            "Préciser aussi que le lien n'est valable qu'environ dix minutes et "
            "qu'il faut l'ouvrir immédiatement, autoriser l'accès, puis réessayer "
            "l'action souhaitée.")


def t_list_connections():
    data = api("GET", "/connected_accounts", query={"user_ids": USER, "limit": 50})
    items = data.get("items") or []
    if not items:
        return ("Aucun compte connecté pour le moment. "
                "Utiliser composio_connect_app pour en autoriser un.")
    lines = [f"{len(items)} compte(s) connecté(s) :"]
    for it in items:
        tk = (it.get("toolkit") or {}).get("slug") or "?"
        lines.append(f"- {tk} : {it.get('status', '?')}")
    return "\n".join(lines)


TOOLS = [
    {
        "name": "composio_search_tools",
        "description": ("Cherche un outil dans TOUT le catalogue Composio (1200+ applications : "
                        "Notion, Slack, Discord, Airtable, Linear, Jira, Stripe, GitHub, Gmail, "
                        "Google Drive, Sheets...). À utiliser dès qu'une action externe est demandée, "
                        "avant d'affirmer qu'un outil n'existe pas. Les requêtes en anglais donnent "
                        "les meilleurs résultats ; le français est traduit automatiquement."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Action recherchée, ex. « create page » ou « créer une page »."},
                "limit": {"type": "integer", "description": "Nombre de résultats (1-30, défaut 10)."},
                "toolkit_slug": {"type": "string", "description": "Filtrer sur une application, ex. « notion »."},
            },
            "required": ["query"],
        },
        "fn": lambda a: t_search_tools(a.get("query"), a.get("limit", 10), a.get("toolkit_slug")),
    },
    {
        "name": "composio_get_tool_schema",
        "description": "Donne les paramètres attendus par un outil Composio, à partir de son identifiant.",
        "inputSchema": {
            "type": "object",
            "properties": {"tool_slug": {"type": "string", "description": "Ex. GITHUB_CREATE_AN_ISSUE."}},
            "required": ["tool_slug"],
        },
        "fn": lambda a: t_get_tool_schema(a.get("tool_slug")),
    },
    {
        "name": "composio_execute_tool",
        "description": ("Exécute n'importe quel outil du catalogue Composio par son identifiant, "
                        "avec ses paramètres. Vérifier le schéma avant si nécessaire."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "tool_slug": {"type": "string", "description": "Identifiant de l'outil."},
                "arguments": {"type": "object", "description": "Paramètres de l'outil."},
            },
            "required": ["tool_slug"],
        },
        "fn": lambda a: t_execute_tool(a.get("tool_slug"), a.get("arguments")),
    },
    {
        "name": "composio_connect_app",
        "description": ("Génère le lien d'autorisation d'une application Composio et le renvoie à "
                        "l'utilisateur. À utiliser quand une exécution échoue faute de compte connecté, "
                        "ou quand l'utilisateur demande à connecter une application. Le lien renvoyé "
                        "doit être recopié nu, seul sur sa ligne, sans gras ni astérisques ni backticks : "
                        "un caractère collé à l'URL invalide le jeton d'autorisation."),
        "inputSchema": {
            "type": "object",
            "properties": {"toolkit_slug": {"type": "string", "description": "Ex. « notion », « slack »."}},
            "required": ["toolkit_slug"],
        },
        "fn": lambda a: t_connect_app(a.get("toolkit_slug")),
    },
    {
        "name": "composio_list_connections",
        "description": "Liste les applications déjà connectées pour cet utilisateur et leur statut.",
        "inputSchema": {"type": "object", "properties": {}},
        "fn": lambda a: t_list_connections(),
    },
]

TOOLS_BY_NAME = {t["name"]: t for t in TOOLS}
SERVER_INFO = {"name": "composio-bridge", "version": "1.0.0"}


def handle(req):
    method = req.get("method")
    rid = req.get("id")
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": rid, "result": {
            "protocolVersion": req.get("params", {}).get("protocolVersion", "2024-11-05"),
            "capabilities": {"tools": {}},
            "serverInfo": SERVER_INFO,
        }}
    if method and method.startswith("notifications/"):
        return None
    if method == "ping":
        return {"jsonrpc": "2.0", "id": rid, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": rid, "result": {
            "tools": [{k: t[k] for k in ("name", "description", "inputSchema")} for t in TOOLS]
        }}
    if method == "tools/call":
        params = req.get("params") or {}
        name = params.get("name")
        args = params.get("arguments") or {}
        tool = TOOLS_BY_NAME.get(name)
        if not tool:
            return {"jsonrpc": "2.0", "id": rid,
                    "error": {"code": -32601, "message": f"Outil inconnu : {name}"}}
        try:
            text = tool["fn"](args)
            return {"jsonrpc": "2.0", "id": rid,
                    "result": {"content": [{"type": "text", "text": text}], "isError": False}}
        except ApiError as exc:
            log(f"erreur outil {name}: {exc}")
            return {"jsonrpc": "2.0", "id": rid,
                    "result": {"content": [{"type": "text", "text": f"Erreur : {exc}"}], "isError": True}}
        except Exception as exc:  # noqa: BLE001 - remonter l'erreur sans tuer le serveur
            log(f"exception outil {name}: {type(exc).__name__} {exc}")
            return {"jsonrpc": "2.0", "id": rid, "result": {
                "content": [{"type": "text", "text": f"Erreur interne ({type(exc).__name__}) : {exc}"}],
                "isError": True}}
    if rid is None:
        return None
    return {"jsonrpc": "2.0", "id": rid,
            "error": {"code": -32601, "message": f"Méthode non supportée : {method}"}}


def main():
    log(f"démarrage, utilisateur={USER}, clé={'présente' if KEY else 'ABSENTE'}")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            log(f"ligne JSON invalide ignorée : {line[:120]}")
            continue
        resp = handle(req)
        if resp is not None:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()
    log("fin du flux d'entrée, arrêt.")


if __name__ == "__main__":
    main()
