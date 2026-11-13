"""Actualizar ArteSana desde su repositorio de GitHub, sin necesitar git.

Adaptado de RiplayWEB (selfupdate.py):
- Compara cada archivo local con el de GitHub usando la misma huella que git (SHA-1 del
  "blob"): sabe exactamente qué cambió, sin depender de números de versión.
- Descarga el commit como .zip, verifica la huella de cada archivo y que los .py no tengan
  errores de sintaxis, guarda copia de lo que reemplaza y recién ahí escribe. Si algo falla
  al escribir, vuelve todo como estaba.
- Nunca toca los datos del usuario (carpeta instance, artesana.env, .venv).
- «Volver a la versión anterior» restaura la última copia (se guardan las últimas 3).
- Repos privados: token de GitHub de solo lectura en artesana.env (GITHUB_TOKEN).
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

UA = "ArteSana-actualizador/2.0"
TEXT_EXT = {".py", ".js", ".css", ".html", ".md", ".txt", ".bat", ".sh", ".svg", ".webmanifest",
            ".json", ".yml", ".yaml", ".ejemplo", ".cfg", ".ini", ".toml"}
SKIP_PREFIXES = (".git/", ".github/")
PROTECTED_NAMES = {"artesana.env"}
PROTECTED_PREFIXES = ("instance/", ".venv/", "venv/", "__pycache__/")
ARCHIVOS_CLAVE = ("app.py", "servicio.py")
MAX_ZIP = 60 * 1024 * 1024
KEEP_BACKUPS = 3


def _api():
    return os.environ.get("ARTESANA_GITHUB_API", "https://api.github.com").rstrip("/")


class UpdateError(Exception):
    """Error con mensaje listo para mostrar. `needs_token` indica que falta o falla el token."""

    def __init__(self, message: str, needs_token: bool = False):
        super().__init__(message)
        self.needs_token = needs_token


# ------------------------------------------------------------------ GitHub

def _request(url: str, token: str | None, accept: str = "application/vnd.github+json", limit: int = 5_000_000) -> bytes:
    headers = {"User-Agent": UA, "Accept": accept, "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = resp.read(limit + 1)
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            raise UpdateError("GitHub rechazó el token (GITHUB_TOKEN): puede estar vencido o mal copiado.", needs_token=True)
        if exc.code == 404:
            raise UpdateError("No se encontró el repositorio. Si es privado, cargá GITHUB_TOKEN en artesana.env; "
                              "si no, revisá REPOSITORIO y RAMA.", needs_token=not token)
        if exc.code == 403:
            reset = exc.headers.get("X-RateLimit-Reset")
            if exc.headers.get("X-RateLimit-Remaining") == "0" and reset:
                minutos = max(1, int((int(reset) - time.time()) / 60) + 1)
                raise UpdateError(f"GitHub limitó las consultas por un rato. Se vuelve a intentar en unos {minutos} minutos.")
            raise UpdateError("GitHub no permite el acceso: el token necesita permiso de lectura de "
                              "«Contents» sobre este repositorio.", needs_token=True)
        raise UpdateError(f"GitHub respondió con un error ({exc.code}). Se vuelve a intentar más tarde.")
    except urllib.error.URLError as exc:
        raise UpdateError(f"No se pudo conectar con GitHub. Revisá tu internet. ({exc.reason})")
    except (TimeoutError, OSError) as exc:
        raise UpdateError(f"No se pudo conectar con GitHub. Revisá tu internet. ({exc})")
    if len(data) > limit:
        raise UpdateError("La descarga de GitHub es más grande de lo esperado; se canceló por seguridad.")
    return data


def _json(url: str, token: str | None):
    return json.loads(_request(url, token).decode("utf-8"))


def valid_repo(repo: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9-]{1,39}/[A-Za-z0-9._-]{1,100}", repo or ""))


def valid_branch(branch: str) -> bool:
    return bool(re.fullmatch(r"[\w./-]{1,100}", branch or "")) and ".." not in branch


# ------------------------------------------------------------------ archivos

def wanted(path: str) -> bool:
    """Archivos del repo que son parte de la app (y que se pueden escribir sin riesgo)."""
    if not path or path.startswith("/") or "\\" in path or ".." in path.split("/"):
        return False
    if path.startswith(SKIP_PREFIXES) or path.startswith(PROTECTED_PREFIXES):
        return False
    if path in PROTECTED_NAMES or "/__pycache__/" in path:
        return False
    return True


def _is_text(path: str) -> bool:
    p = Path(path)
    return p.suffix.lower() in TEXT_EXT or p.name.startswith(".")


def blob_sha(data: bytes, path: str) -> str:
    """Huella de git del archivo. Los de texto se comparan con saltos de línea LF, como los guarda git."""
    if _is_text(path):
        data = data.replace(b"\r\n", b"\n")
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def local_changes(app_dir: Path, files: dict[str, str]) -> list[str]:
    changed = []
    for path, sha in sorted(files.items()):
        local = app_dir / path
        try:
            if not local.is_file() or blob_sha(local.read_bytes(), path) != sha:
                changed.append(path)
        except OSError:
            changed.append(path)
    return changed


# ------------------------------------------------------------------ estado

def _first_line(text: str) -> str:
    return (text or "").strip().splitlines()[0][:200] if (text or "").strip() else ""


def remote_state(repo: str, branch: str, token: str | None) -> dict:
    if not valid_repo(repo):
        raise UpdateError("REPOSITORIO no es válido (formato: usuario/repositorio).")
    if not valid_branch(branch):
        raise UpdateError("RAMA no es válida.")
    commit = _json(f"{_api()}/repos/{repo}/commits/{urllib.parse.quote(branch, safe='')}", token)
    sha = commit["sha"]
    tree = _json(f"{_api()}/repos/{repo}/git/trees/{sha}?recursive=1", token)
    if tree.get("truncated"):
        raise UpdateError("El repositorio es demasiado grande para actualizar desde acá.")
    files = {e["path"]: e["sha"] for e in tree.get("tree", []) if e.get("type") == "blob" and wanted(e["path"])}
    if any(clave not in files for clave in ARCHIVOS_CLAVE):
        raise UpdateError("Ese repositorio no parece ser ArteSana (le faltan archivos principales).")
    info = commit.get("commit") or {}
    return {
        "sha": sha,
        "message": _first_line(info.get("message")),
        "date": ((info.get("committer") or {}).get("date")) or ((info.get("author") or {}).get("date")),
        "files": files,
    }


def recent_commits(repo: str, branch: str, token: str | None, since_sha: str | None, head_sha: str) -> list[dict]:
    """Novedades: los cambios desde la versión instalada (o los últimos, si no se sabe cuál es)."""
    try:
        if since_sha and since_sha != head_sha:
            data = _json(f"{_api()}/repos/{repo}/compare/{since_sha}...{head_sha}", token)
            commits = list(reversed(data.get("commits") or []))[:30]
        else:
            commits = _json(f"{_api()}/repos/{repo}/commits?sha={urllib.parse.quote(branch, safe='')}&per_page=5", token)
    except UpdateError:
        return []
    return [{"sha": c["sha"][:7], "message": _first_line((c.get("commit") or {}).get("message")),
             "date": ((c.get("commit") or {}).get("committer") or {}).get("date")} for c in commits]


def status(app_dir: Path, repo: str, branch: str, token: str | None, installed: str | None) -> dict:
    state = remote_state(repo, branch, token)
    changed = local_changes(app_dir, state["files"])
    up_to_date = not changed
    commits = [] if up_to_date and installed == state["sha"] else recent_commits(repo, branch, token, installed, state["sha"])
    return {
        "latest": {"sha": state["sha"], "short": state["sha"][:7], "message": state["message"], "date": state["date"]},
        "installed": installed, "upToDate": up_to_date, "changed": changed, "commits": commits,
    }


# ------------------------------------------------------------------ aplicar

def _backup_root(data_dir: Path) -> Path:
    return data_dir / "respaldos_programa"


def list_backups(data_dir: Path) -> list[Path]:
    root = _backup_root(data_dir)
    if not root.is_dir():
        return []
    return sorted((p for p in root.iterdir() if (p / "manifest.json").is_file()), key=lambda p: p.name)


def apply(app_dir: Path, data_dir: Path, repo: str, branch: str, token: str | None, installed: str | None,
          progress=lambda stage: None) -> dict:
    progress("checking")
    state = remote_state(repo, branch, token)
    changed = local_changes(app_dir, state["files"])
    if not changed:
        return {"updated": [], "sha": state["sha"], "message": state["message"], "date": state["date"],
                "requirementsChanged": False, "backup": None}

    progress("downloading")
    raw = _request(f"{_api()}/repos/{repo}/zipball/{state['sha']}", token, limit=MAX_ZIP)
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile:
        raise UpdateError("La descarga de GitHub vino dañada. Se vuelve a intentar más tarde.")
    names = [n for n in zf.namelist() if not n.endswith("/")]
    if not names:
        raise UpdateError("La descarga de GitHub vino vacía.")
    prefix = names[0].split("/", 1)[0] + "/"

    progress("verifying")
    new_content: dict[str, bytes] = {}
    for path in changed:
        try:
            data = zf.read(prefix + path)
        except KeyError:
            raise UpdateError(f"Falta {path} en la descarga de GitHub. No se cambió nada.")
        if blob_sha(data, path) != state["files"][path]:
            raise UpdateError(f"{path} no coincide con GitHub (la descarga puede estar dañada). No se cambió nada.")
        if path.endswith(".py"):
            try:
                compile(data, path, "exec")
            except SyntaxError as exc:
                raise UpdateError(f"La versión nueva de {path} tiene un error (línea {exc.lineno}). No se cambió nada.")
        if path.lower().endswith(".bat"):  # cmd.exe necesita saltos de línea de Windows
            data = data.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
        new_content[path] = data

    progress("backup")
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = _backup_root(data_dir) / stamp
    n = 2
    while backup.exists():
        backup = _backup_root(data_dir) / f"{stamp}-{n}"
        n += 1
    backup.mkdir(parents=True)
    created = []
    for path in changed:
        local = app_dir / path
        if local.is_file():
            target = backup / "files" / path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(local, target)
        else:
            created.append(path)
    manifest = {"from": installed, "to": state["sha"], "at": time.time(), "changed": changed, "created": created}
    (backup / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    progress("installing")
    written = []
    path = ""
    try:
        for path, data in new_content.items():
            dest = app_dir / path
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_name(dest.name + ".tmp-update")
            tmp.write_bytes(data)
            os.replace(tmp, dest)
            written.append(path)
    except OSError as exc:
        _restore(app_dir, backup, written)  # deja todo como estaba
        shutil.rmtree(backup, ignore_errors=True)
        raise UpdateError(f"No se pudo escribir {path} ({exc}). Se volvió a la versión anterior; "
                          f"si es la planilla, cerrala en Excel.")

    for old in list_backups(data_dir)[:-KEEP_BACKUPS]:
        shutil.rmtree(old, ignore_errors=True)
    return {
        "updated": changed, "sha": state["sha"], "message": state["message"], "date": state["date"],
        "backup": backup.name, "requirementsChanged": any(p.startswith("requirements") for p in changed),
    }


def _restore(app_dir: Path, backup: Path, paths: list[str]) -> None:
    manifest = json.loads((backup / "manifest.json").read_text(encoding="utf-8"))
    for path in paths:
        src = backup / "files" / path
        dest = app_dir / path
        if path in manifest.get("created", []):
            dest.unlink(missing_ok=True)
        elif src.is_file():
            shutil.copy2(src, dest)


def rollback(app_dir: Path, data_dir: Path) -> dict:
    backups = list_backups(data_dir)
    if not backups:
        raise UpdateError("No hay una versión anterior guardada.")
    backup = backups[-1]
    manifest = json.loads((backup / "manifest.json").read_text(encoding="utf-8"))
    paths = [p for p in manifest.get("changed", []) if wanted(p)]
    _restore(app_dir, backup, paths)
    shutil.rmtree(backup, ignore_errors=True)
    return {"restored": paths, "sha": manifest.get("from"),
            "requirementsChanged": any(p.startswith("requirements") for p in paths)}
