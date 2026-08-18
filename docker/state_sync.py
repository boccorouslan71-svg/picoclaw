#!/usr/bin/env python3
"""
state_sync.py — persistance externe de l'état PicoClaw sur un dépôt GitHub privé.

Pourquoi : l'offre gratuite de l'hébergeur n'a pas de disque persistant. Tout ce
que PicoClaw écrit dans son workspace (au premier chef les tâches planifiées,
`cron/jobs.json`) disparaît au moindre redéploiement ou redémarrage.

Principe : le dépôt privé sert de disque. Deux modes.
  --restore : une passe, AVANT le démarrage de la gateway. Rapatrie l'état.
  --watch   : démon. Surveille les fichiers suivis et republie à chaque
              changement, plus une dernière fois sur SIGTERM/SIGINT.

Aucune dépendance externe : uniquement la bibliothèque standard.

Variables d'environnement
  GITHUB_TOKEN            (requis) jeton avec accès en écriture au dépôt d'état
  PICOCLAW_STATE_REPO     (requis) « propriétaire/dépôt »
  PICOCLAW_STATE_BRANCH   branche cible (défaut : main)
  PICOCLAW_WORKSPACE      racine du workspace PicoClaw
  PICOCLAW_STATE_PATHS    chemins suivis, séparés par des virgules, relatifs au
                          workspace. Un chemin terminé par « / » est un dossier
                          suivi récursivement. Défaut : cron/jobs.json,state/
  PICOCLAW_STATE_INTERVAL secondes entre deux vérifications (défaut : 20)
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import signal
import sys
import time
import urllib.error
import urllib.request

API = "https://api.github.com"
UA = "picoclaw-state-sync"


def log(msg: str) -> None:
    print(f"[state-sync] {msg}", flush=True)


class Conf:
    """Configuration lue à la frontière : une variable manquante échoue tout de suite."""

    def __init__(self) -> None:
        self.token = os.environ.get("GITHUB_TOKEN", "").strip()
        self.repo = os.environ.get("PICOCLAW_STATE_REPO", "").strip()
        if not self.token or not self.repo or "/" not in self.repo:
            raise SystemExit(
                "[state-sync] désactivé : GITHUB_TOKEN et PICOCLAW_STATE_REPO "
                "(propriétaire/dépôt) sont requis."
            )
        self.branch = os.environ.get("PICOCLAW_STATE_BRANCH", "").strip() or "main"
        home = os.environ.get("PICOCLAW_HOME", "/data/.picoclaw")
        self.workspace = (
            os.environ.get("PICOCLAW_WORKSPACE", "").strip()
            or os.path.join(home, "workspace")
        )
        raw = os.environ.get("PICOCLAW_STATE_PATHS", "").strip()
        self.specs = [p.strip() for p in (raw or "cron/jobs.json,state/").split(",") if p.strip()]
        try:
            self.interval = max(5, int(os.environ.get("PICOCLAW_STATE_INTERVAL", "20")))
        except ValueError:
            self.interval = 20


def call(conf: Conf, method: str, path: str, payload: dict | None = None) -> tuple[int, dict]:
    """Appel API GitHub. Renvoie (code http, corps json). Ne lève pas sur 404/409/422."""
    url = f"{API}{path}"
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {conf.token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", UA)
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            body = resp.read().decode() or "{}"
            return resp.status, json.loads(body)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode() or "{}"
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = {"message": raw[:200]}
        return exc.code, parsed
    except (urllib.error.URLError, TimeoutError) as exc:
        return 0, {"message": f"réseau indisponible : {exc}"}


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# ----------------------------------------------------------------------------
# Restauration
# ----------------------------------------------------------------------------

def remote_listing(conf: Conf, path: str) -> list[dict]:
    """Contenu distant d'un dossier, récursif. Liste vide si absent."""
    code, body = call(conf, "GET", f"/repos/{conf.repo}/contents/{path}?ref={conf.branch}")
    if code == 404:
        return []
    if code != 200 or not isinstance(body, list):
        log(f"listing « {path} » : http {code} — {str(body)[:120]}")
        return []
    files: list[dict] = []
    for item in body:
        if item.get("type") == "file":
            files.append(item)
        elif item.get("type") == "dir":
            files.extend(remote_listing(conf, item["path"]))
    return files


def fetch_file(conf: Conf, path: str) -> bytes | None:
    code, body = call(conf, "GET", f"/repos/{conf.repo}/contents/{path}?ref={conf.branch}")
    if code == 404:
        return None
    if code != 200 or body.get("encoding") != "base64":
        log(f"lecture « {path} » : http {code} — {str(body)[:120]}")
        return None
    return base64.b64decode(body["content"])


def write_local(conf: Conf, rel: str, blob: bytes) -> None:
    dest = os.path.join(conf.workspace, rel)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "wb") as fh:
        fh.write(blob)


def describe_jobs(blob: bytes) -> str:
    """Compte les tâches d'un jobs.json restauré, pour que le journal soit vérifiable."""
    try:
        parsed = json.loads(blob.decode())
    except (json.JSONDecodeError, UnicodeDecodeError):
        return "contenu non JSON"
    if isinstance(parsed, list):
        return f"{len(parsed)} tâche(s)"
    if isinstance(parsed, dict):
        for key in ("jobs", "Jobs", "items"):
            if isinstance(parsed.get(key), list):
                return f"{len(parsed[key])} tâche(s)"
        return f"{len(parsed)} clé(s)"
    return "forme inattendue"


def restore(conf: Conf) -> int:
    restored = 0
    for spec in conf.specs:
        if spec.endswith("/"):
            folder = spec.rstrip("/")
            for item in remote_listing(conf, folder):
                blob = fetch_file(conf, item["path"])
                if blob is not None:
                    write_local(conf, item["path"], blob)
                    restored += 1
                    log(f"restauré : {item['path']} ({len(blob)} octets)")
        else:
            blob = fetch_file(conf, spec)
            if blob is None:
                log(f"aucune sauvegarde distante pour « {spec} » (premier démarrage)")
                continue
            write_local(conf, spec, blob)
            restored += 1
            detail = describe_jobs(blob) if spec.endswith("jobs.json") else f"{len(blob)} octets"
            log(f"restauré : {spec} — {detail}")
    log(f"restauration terminée : {restored} fichier(s) depuis {conf.repo}@{conf.branch}")
    return restored


# ----------------------------------------------------------------------------
# Publication
# ----------------------------------------------------------------------------

def local_files(conf: Conf) -> list[str]:
    """Chemins relatifs actuellement présents localement parmi les chemins suivis."""
    out: list[str] = []
    for spec in conf.specs:
        if spec.endswith("/"):
            root = os.path.join(conf.workspace, spec.rstrip("/"))
            for dirpath, _dirs, names in os.walk(root):
                for name in names:
                    if name.startswith("."):
                        continue
                    full = os.path.join(dirpath, name)
                    out.append(os.path.relpath(full, conf.workspace))
        else:
            if os.path.isfile(os.path.join(conf.workspace, spec)):
                out.append(spec)
    return sorted(set(out))


def push(conf: Conf, rel: str, sha_cache: dict[str, str]) -> bool:
    full = os.path.join(conf.workspace, rel)
    try:
        with open(full, "rb") as fh:
            blob = fh.read()
    except OSError as exc:
        log(f"illisible « {rel} » : {exc}")
        return False

    payload = {
        "message": f"state: {rel} @ {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}",
        "content": base64.b64encode(blob).decode(),
        "branch": conf.branch,
    }
    remote_sha = sha_cache.get(rel)
    if remote_sha is None:
        code, body = call(conf, "GET", f"/repos/{conf.repo}/contents/{rel}?ref={conf.branch}")
        if code == 200:
            remote_sha = body.get("sha")
    if remote_sha:
        payload["sha"] = remote_sha

    code, body = call(conf, "PUT", f"/repos/{conf.repo}/contents/{rel}", payload)
    if code == 409 or (code == 422 and "sha" in str(body).lower()):
        # Le distant a bougé : on relit le sha et on retente une fois.
        code2, body2 = call(conf, "GET", f"/repos/{conf.repo}/contents/{rel}?ref={conf.branch}")
        if code2 == 200:
            payload["sha"] = body2.get("sha")
            code, body = call(conf, "PUT", f"/repos/{conf.repo}/contents/{rel}", payload)
    if code in (200, 201):
        content = body.get("content") or {}
        if content.get("sha"):
            sha_cache[rel] = content["sha"]
        log(f"publié : {rel} ({len(blob)} octets)")
        return True
    log(f"ÉCHEC publication « {rel} » : http {code} — {str(body)[:160]}")
    return False


def watch(conf: Conf) -> None:
    log(
        f"surveillance active — {conf.repo}@{conf.branch}, toutes les {conf.interval}s, "
        f"chemins : {', '.join(conf.specs)}"
    )
    fingerprints: dict[str, str] = {}
    sha_cache: dict[str, str] = {}
    stopping = {"flag": False}

    def on_signal(signum, _frame):
        log(f"signal {signum} reçu — dernière publication avant arrêt")
        stopping["flag"] = True

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)

    while True:
        try:
            for rel in local_files(conf):
                digest = sha256_file(os.path.join(conf.workspace, rel))
                if fingerprints.get(rel) == digest:
                    continue
                if push(conf, rel, sha_cache):
                    fingerprints[rel] = digest
        except Exception as exc:  # le démon ne doit jamais mourir en silence
            log(f"erreur de cycle (on continue) : {type(exc).__name__} — {exc}")
        if stopping["flag"]:
            log("arrêt propre")
            return
        for _ in range(conf.interval):
            time.sleep(1)
            if stopping["flag"]:
                break


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "--watch"
    conf = Conf()
    if mode == "--restore":
        restore(conf)
        return 0
    if mode == "--watch":
        watch(conf)
        return 0
    log(f"mode inconnu « {mode} » — attendu --restore ou --watch")
    return 2


if __name__ == "__main__":
    sys.exit(main())
