#!/usr/bin/env python3
"""Blocklist de Immich. --dry-run consulta la API, sin escribir estado ni medios.

Cron: cada minuto, con flock interno. state.json v2 es la fuente de verdad;
los TSV anteriores se auditan al importar y quedan como evidencia histórica,
no como estado actualizado para hacer rollback.
Los originales de las lápidas se destruyen intencionadamente: mantener backups.
"""
import argparse
import base64
import fcntl
import hashlib
import json
import os
import stat
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

BASE_DIR = Path(__file__).resolve().parent
CONFIG = {}
API = KEY = ""
OWNER_ID = None
NTFY = None
MODE = "archive"
MEDIA = Path("/data")
MEDIA_PREFIX = "/data"
BLOCKLIST = BASE_DIR / "blocklist.tsv"
TOMBSTONES = BASE_DIR / "tombstones.tsv"
STATE = BASE_DIR / "state.json"
LOG = BASE_DIR / "tombstone.log"
DRY_RUN = False
REQUESTS = 0


def configure(config_path):
    """Una carpeta privada de estado por usuario; nunca leer secretos al importar."""
    global CONFIG, BASE_DIR, API, KEY, OWNER_ID, NTFY, MODE, MEDIA, MEDIA_PREFIX
    global BLOCKLIST, TOMBSTONES, STATE, LOG, REQUESTS
    path = Path(config_path).expanduser().absolute()
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_uid != os.geteuid():
        raise ValueError("El config debe ser un archivo regular propio y privado: chmod 600")
    if path.parent.stat().st_mode & 0o022 or path.parent.stat().st_uid != os.geteuid():
        raise ValueError("La carpeta de estado no debe ser escribible por otras personas")
    config = json.loads(path.read_text())
    url = config["api_url"].rstrip("/")
    parsed = urllib.parse.urlsplit(url)
    if (
        parsed.scheme not in ("http", "https") or not parsed.hostname
        or parsed.username is not None or parsed.password is not None
        or parsed.query or parsed.fragment or any(c in url for c in "\r\n\x00")
    ):
        raise ValueError("api_url debe ser una URL HTTP(S) sin credenciales, query ni fragmento")
    mode = config.get("mode", "archive")
    if mode not in ("archive", "tombstone"):
        raise ValueError("mode debe ser archive o tombstone; delete ya no se admite porque provoca re-subidas")
    if mode == "tombstone" and config.get("allow_truncate") is not True:
        raise ValueError("tombstone destruye contenido: requiere allow_truncate=true y backups")
    root = Path(config.get("media_root") or "/data").expanduser()
    if mode == "tombstone" and (not root.is_absolute() or not root.is_dir()):
        raise ValueError("media_root debe ser una carpeta absoluta accesible en este host")
    prefix = config.get("media_prefix", "/data").rstrip("/")
    if not prefix.startswith("/") or any(part in ("", ".", "..") for part in prefix[1:].split("/")):
        raise ValueError("media_prefix debe ser un prefijo absoluto sin traversal")
    key = config.get("api_key")
    if not isinstance(key, str) or not key or not key.isascii() or any(ord(c) < 33 or ord(c) > 126 for c in key):
        raise ValueError("api_key debe ser texto ASCII sin espacios ni caracteres de control")
    CONFIG, BASE_DIR = config, path.parent
    API, KEY = url, config["api_key"]
    MODE, MEDIA, MEDIA_PREFIX = mode, root, prefix
    NTFY = config.get("ntfy_url")
    OWNER_ID = config.get("owner_id") or api("GET", "/api/users/me")["id"]
    if str(UUID(OWNER_ID)) != OWNER_ID:
        raise ValueError("owner_id no es un UUID canónico")
    BLOCKLIST, TOMBSTONES = BASE_DIR / "blocklist.tsv", BASE_DIR / "tombstones.tsv"
    STATE, LOG = BASE_DIR / "state.json", BASE_DIR / "tombstone.log"
    REQUESTS = 0


def log(msg):
    # Los nombres vienen de clientes: no permitir inyección de líneas en el log.
    line = f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {msg}"
    line = line.replace("\r", "\\r").replace("\n", "\\n")
    print(line, flush=True)
    if not DRY_RUN:
        with LOG.open("a") as f:
            f.write(line + "\n")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("Redirección de API rechazada: proteger la API key")


OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())


def api(method, path, body=None):
    global REQUESTS
    if DRY_RUN and method not in ("GET", "POST"):
        raise ValueError("Mutación de API bloqueada en dry-run")
    if DRY_RUN and method == "POST" and path not in ("/api/search/metadata", "/api/assets/bulk-upload-check"):
        raise ValueError("POST no permitido en dry-run")
    req = urllib.request.Request(
        f"{API}{path}",
        data=json.dumps(body).encode() if body is not None else None,
        headers={"x-api-key": KEY, "Content-Type": "application/json"},
        method=method,
    )
    REQUESTS += 1
    with OPENER.open(req, timeout=30) as r:
        raw = r.read()
        return json.loads(raw) if raw else None


def asset_identity(a):
    required = {"id", "ownerId", "checksum", "isTrashed", "visibility", "originalPath", "libraryId", "isOffline"}
    if not isinstance(a, dict) or not required.issubset(a):
        raise ValueError("Faltan campos obligatorios de la API")
    for key in ("id", "ownerId"):
        if str(UUID(a[key])) != a[key]:
            raise ValueError(f"UUID de asset no canónico: {key}")
    if len(base64.b64decode(a["checksum"], validate=True)) != 20:
        raise ValueError("Checksum de asset no válido")
    if type(a["isTrashed"]) is not bool or a["visibility"] not in (
        "timeline", "archive", "hidden", "locked",
    ):
        raise ValueError("Contrato de API inesperado")
    return a


def search_assets(filters):
    """Leer todas las páginas antes de hacer mutaciones que alteren la búsqueda."""
    items, page, seen = [], 1, set()
    while True:
        d = api("POST", "/api/search/metadata", {**filters, "size": 250, "page": page})
        batch = d["assets"]["items"]
        if not isinstance(batch, list):
            raise ValueError("Respuesta de búsqueda no válida")
        for a in batch:
            asset_identity(a)
            if a["id"] not in seen:
                items.append(a)
                seen.add(a["id"])
        next_page = d["assets"].get("nextPage")
        if next_page is None:
            break
        following = int(next_page)
        if following <= page or not batch or following > 10000:
            raise ValueError("Paginación no progresiva o excesiva")
        page = following
    return items


def load_blocklist():
    entries = {}
    if BLOCKLIST.exists():
        for line in BLOCKLIST.read_text().splitlines():
            if line.strip():
                checksum, aid, date, name = line.split("\t", 3)
                entries[checksum] = [aid, date, name]
    return entries


def load_tombstones():
    entries = {}
    if TOMBSTONES.exists():
        for line in TOMBSTONES.read_text().splitlines():
            if line.strip():
                aid, ts, path = line.split("\t", 2)
                entries[aid] = [ts, path]
    return entries


def timestamp(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Fecha sin zona horaria")
    if parsed > datetime.now(timezone.utc) + timedelta(minutes=5):
        raise ValueError("Fecha futura: revisar reloj o estado")
    return parsed


def load_state():
    if not OWNER_ID or str(UUID(OWNER_ID)) != OWNER_ID:
        raise ValueError("Fijar owner_id en config.json al propietario de la API key")
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    if not isinstance(state, dict):
        raise ValueError("Estado no válido")
    if state.get("owner_id", OWNER_ID) != OWNER_ID:
        raise ValueError("El estado pertenece a otro usuario; no reutilizarlo con otra API key")
    state["owner_id"] = OWNER_ID
    if state.get("mode", MODE) != MODE:
        raise ValueError("No cambiar mode sobre un estado existente; migrar o usar otra carpeta")
    state["mode"] = MODE
    if "schema" not in state:
        state.update(schema=2, blocked=load_blocklist(), tombs=load_tombstones(), pending={})
    if state["schema"] != 2:
        raise ValueError("Versión de estado desconocida")
    state.setdefault("origins", {})
    for name in ("blocked", "tombs", "pending", "origins"):
        if not isinstance(state[name], dict):
            raise ValueError(f"Estado no válido: {name}")
    for checksum, entry in state["blocked"].items():
        if len(base64.b64decode(checksum, validate=True)) != 20 or len(entry) != 3:
            raise ValueError("Blocklist corrupta")
        UUID(entry[0])
        if entry[0] not in state["tombs"]:
            ids = state["origins"].setdefault(checksum, [])
            if entry[0] not in ids:
                ids.append(entry[0])
    for checksum, ids in state["origins"].items():
        if len(base64.b64decode(checksum, validate=True)) != 20 or not isinstance(ids, list):
            raise ValueError("Historial de restauraciones corrupto")
        for aid in ids:
            UUID(aid)
    for aid, (ts, path) in state["tombs"].items():
        UUID(aid)
        timestamp(ts)
        if MODE == "tombstone":
            local_path(path)
        elif not isinstance(path, str):
            raise ValueError("Ruta de asset no válida")
    for name in ("last_run", "last_full_scan"):
        if state.get(name):
            timestamp(state[name])
    return state


def save_state(state):
    """Snapshot único y durable: no mezclar generaciones de TSV y cursor."""
    if DRY_RUN:
        return
    fd, name = tempfile.mkstemp(prefix=".state-", dir=BASE_DIR)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(state, f, ensure_ascii=False, separators=(",", ":"))
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, STATE)
        directory = os.open(BASE_DIR, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def local_path(original_path):
    """Solo originales gestionados por Immich; nunca thumbs ni rutas externas."""
    prefix = MEDIA_PREFIX + "/"
    if not isinstance(original_path, str) or not original_path.startswith(prefix):
        raise ValueError("Ruta fuera de media_prefix")
    parts = original_path[len(prefix):].split("/")
    if len(parts) < 3 or parts[0] not in ("library", "upload") or any(
        part in ("", ".", "..") or "\x00" in part for part in parts
    ):
        raise ValueError("Ruta de original no permitida")
    return MEDIA.joinpath(*parts)


@contextmanager
def media_file(path, writable=False):
    """Resolver por descriptores: no seguir symlinks ni truncar hardlinks."""
    relative = path.relative_to(MEDIA)
    directory = os.open(MEDIA, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    fd = None
    try:
        for part in relative.parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        flags = os.O_RDWR if writable else os.O_RDONLY
        fd = os.open(relative.name, flags | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError("El medio no es un archivo regular con un único enlace")
        yield fd
    finally:
        if fd is not None:
            os.close(fd)
        os.close(directory)


def get_asset(aid):
    UUID(aid)
    try:
        a = asset_identity(api("GET", f"/api/assets/{aid}"))
    except urllib.error.HTTPError as e:
        e.close()
        if e.code == 404:
            return None
        # Un 400 puede ser ausencia, locked o permisos: no inferir un borrado.
        raise
    if a["id"] != aid:
        raise ValueError("La API devolvió otro asset")
    return a


def audit_legacy(state):
    """No atribuir a este usuario checksums legacy sin procedencia demostrable."""
    if state.get("scope_verified") is True:
        return
    if state["pending"]:
        raise ValueError("Resolver intenciones heredadas antes de verificar su propietario")
    quarantine = state.setdefault("quarantine", {"blocked": {}, "tombs": {}})
    verified, origins = {}, {}
    for checksum, entry in state["blocked"].items():
        try:
            a = get_asset(entry[0])
        except urllib.error.HTTPError as e:
            if e.code != 400:
                raise
            a = None  # Ausente o inaccesible: cuarentena, no inferir procedencia.
        if a and a["ownerId"] == OWNER_ID and a["libraryId"] is None and a["checksum"] == checksum:
            verified[checksum] = entry
            if entry[0] not in state["tombs"]:
                origins[checksum] = [entry[0]]
        else:
            quarantine["blocked"][checksum] = list(entry)
    allowed_ids = {entry[0] for entry in verified.values()}
    tombs = {}
    for aid, record in state["tombs"].items():
        if aid in allowed_ids:
            tombs[aid] = record
        else:
            quarantine["tombs"][aid] = record
    state.update(blocked=verified, origins=origins, tombs=tombs, scope_verified=True)
    save_state(state)
    log(f"AUDIT verificados={len(verified)} cuarentena={len(quarantine['blocked'])}")


def verify_owner():
    if api("GET", "/api/users/me")["id"] != OWNER_ID:
        raise ValueError("La API key ya no pertenece al propietario fijado")


def upload_check(aid, checksum):
    """Preflight de solo lectura, con el mismo usuario que originó el estado."""
    verify_owner()
    response = api("POST", "/api/assets/bulk-upload-check", {"assets": [{"id": aid, "checksum": checksum}]})
    results = response["results"]
    if len(results) != 1 or results[0]["id"] != aid or results[0]["action"] not in ("accept", "reject"):
        raise ValueError("Preflight no válido")
    result = results[0]
    if result["action"] == "reject":
        if result.get("reason") != "duplicate":
            raise ValueError("Rechazo de subida no reconocido")
        UUID(result["assetId"])
    return result


def revival_complete(aid, checksum):
    result = upload_check(aid, checksum)
    if result["action"] == "accept":
        return True
    if result["assetId"] == aid:
        return False
    # Si la copia ya llegó, no convertirla otra vez en lápida mientras cerramos
    # la recuperación. Una copia externa/locked/inaccesible no confirma nada.
    restored = get_asset(result["assetId"])
    if not restored or not matches(restored, restored["id"], restored["originalPath"], checksum) or restored["visibility"] != "timeline":
        return False
    with media_file(local_path(restored["originalPath"])) as fd:
        return os.fstat(fd).st_size > 0


def matches(a, aid, path, checksum):
    return bool(
        a and a["ownerId"] == OWNER_ID and a["id"] == aid and a["originalPath"] == path
        and a["checksum"] == checksum and a["isTrashed"] is False
        and "libraryId" in a and a["libraryId"] is None
        and a.get("isOffline") is False
    )


def wipe_files(assets, blocked=None):
    """Revalidar cada lápida; hashear originales nuevos y no seguir enlaces."""
    if MODE != "tombstone":
        return 0
    if blocked is None:
        blocked = load_blocklist()
    checksums = {entry[0]: c for c, entry in blocked.items()}
    freed = 0
    for aid, path in assets.items():
        checksum = checksums.get(aid)
        if not checksum:
            continue
        a = get_asset(aid)
        if not matches(a, aid, path, checksum) or a["visibility"] != "archive":
            continue
        original = local_path(path)
        targets = [(original, True)]
        # Evitar el glob por todos los usuarios y los matches por prefijo de ID.
        encoded = MEDIA / "encoded-video" / a["ownerId"] / aid[:2] / aid[2:4]
        targets.extend((encoded / (aid + suffix), False) for suffix in (".mp4", ".webm"))
        for target, is_original in targets:
            try:
                with media_file(target, writable=not DRY_RUN) as fd:
                    before = os.fstat(fd)
                    if not before.st_size:
                        continue
                    if is_original:
                        digest = hashlib.sha1()
                        while chunk := os.read(fd, 1024 * 1024):
                            digest.update(chunk)
                        if base64.b64encode(digest.digest()).decode() != checksum:
                            raise ValueError("Checksum del fichero no coincide; truncado cancelado")
                    # Revalidar también después de un hash potencialmente lento.
                    live = get_asset(aid)
                    if not matches(live, aid, path, checksum) or live["visibility"] != "archive":
                        break
                    after = os.fstat(fd)
                    if (before.st_size, before.st_mtime_ns, before.st_ctime_ns, before.st_nlink) != (
                        after.st_size, after.st_mtime_ns, after.st_ctime_ns, after.st_nlink,
                    ) or after.st_nlink != 1:
                        raise ValueError("El fichero cambió durante la comprobación")
                    if DRY_RUN:
                        log(f"DRY_RUN truncado_previsto={before.st_size}bytes asset={aid}")
                        continue
                    os.ftruncate(fd, 0)
                    os.fsync(fd)
                    freed += before.st_size
            except FileNotFoundError:
                continue
    return freed


def notify(msg):
    if not NTFY or DRY_RUN:
        return
    try:
        with urllib.request.urlopen(
            urllib.request.Request(NTFY, data=msg.encode(), method="POST"), timeout=10,
        ):
            pass
    except Exception as e:
        log(f"WARN ntfy: {type(e).__name__}")


def finish_pending(state, aid):
    """Resolver intención durable sin repetir a ciegas un PUT o un DELETE."""
    pending = state["pending"][aid]
    checksum = pending["checksum"]
    if pending["kind"] == "revive" and MODE == "archive":
        a = get_asset(aid)
        if not matches(a, aid, pending["path"], checksum) or a["visibility"] != "timeline":
            return
        state["blocked"].pop(checksum, None)
        state["tombs"].pop(aid, None)
        del state["pending"][aid]
        save_state(state)
        return
    if pending["kind"] == "revive":
        if revival_complete(aid, checksum):
            state["blocked"].pop(checksum, None)
            state["tombs"].pop(aid, None)
            del state["pending"][aid]
            save_state(state)
            return
        if pending.get("sent") is True:
            return  # DELETE aceptado, pero el checksum todavía impide subir.
    a = get_asset(aid)
    if pending["kind"] == "archive":
        if matches(a, aid, pending["path"], checksum) and a["visibility"] == "archive":
            entry = state["blocked"][checksum]
            state["blocked"][checksum] = [aid, entry[1], entry[2]]
            state["tombs"][aid] = [pending["ts"], pending["path"]]
        else:
            # PUT fallido o usuario deshizo la acción: preferir conservar el medio.
            state["blocked"].pop(checksum, None)
            log(f"WARN intención de archivo cancelada sin truncar: {aid}")
    elif pending["kind"] == "revive":
        if a is None or a["isTrashed"]:
            return  # Respuesta perdida o nueva papelera: conservar intención.
        if a is not None:
            if not matches(a, aid, pending["path"], checksum) or a["visibility"] != "timeline":
                del state["pending"][aid]
                save_state(state)
                return
            with media_file(local_path(pending["path"])) as fd:
                if os.fstat(fd).st_size == 0:
                    if a.get("livePhotoVideoId") or a.get("isEdited") is not False or "livePhotoVideoId" not in a:
                        raise ValueError(f"Recuperación manual requerida: Live Photo o asset editado {aid}")
                    live = get_asset(aid)
                    if not matches(live, aid, pending["path"], checksum) or live["visibility"] != "timeline":
                        del state["pending"][aid]
                        save_state(state)
                        return
                    if live.get("livePhotoVideoId") or live.get("isEdited") is not False or "livePhotoVideoId" not in live:
                        raise ValueError(f"Recuperación manual requerida: Live Photo o asset editado {aid}")
                    # El original pudo recuperar contenido durante la llamada GET.
                    if os.fstat(fd).st_size == 0:
                        api("DELETE", "/api/assets", {"ids": [aid], "force": True})
                        pending["sent"] = True
                        save_state(state)
                        if not revival_complete(aid, checksum):
                            return
        state["blocked"].pop(checksum, None)
        state["tombs"].pop(aid, None)
    else:
        raise ValueError("Intención desconocida")
    del state["pending"][aid]
    save_state(state)


def main():
    started = time.monotonic()
    state = load_state()
    verify_owner()
    audit_legacy(state)
    blocked, tombs = state["blocked"], state["tombs"]
    now = datetime.now(timezone.utc)
    # Capturar y persistir antes del resto de consultas: una purga no debe perderlo.
    added = 0
    history_changed = False
    for a in search_assets({"trashedAfter": "2000-01-01T00:00:00Z"}):
        asset_identity(a)
        if a["ownerId"] != OWNER_ID or a["libraryId"] is not None:
            continue
        if a["isTrashed"] is not True:
            raise ValueError("La API ignoró el filtro de papelera; ejecución cancelada")
        quarantined = state.get("quarantine", {}).get("blocked", {})
        if a["checksum"] in quarantined:
            del quarantined[a["checksum"]]
            history_changed = True
        ids = state["origins"].setdefault(a["checksum"], [])
        if a["id"] not in ids:
            ids.append(a["id"])
            history_changed = True
        if a["checksum"] not in blocked:
            blocked[a["checksum"]] = [a["id"], now.date().isoformat(), a["originalFileName"]]
            added += 1
    if added or history_changed:
        save_state(state)
    for aid in list(state["pending"]):
        if not DRY_RUN:
            finish_pending(state, aid)
    # Solape corto + reconciliación diaria, en vez de repetir todo el último día.
    full = not state.get("last_full_scan") or timestamp(state["last_full_scan"]) < now - timedelta(days=1)
    filters = {}
    if state.get("last_run") and not full:
        filters["updatedAfter"] = (timestamp(state["last_run"]) - timedelta(minutes=5)).isoformat()
    candidates = search_assets(filters)
    entombed = restored = revived = planned = 0
    recovering = {p["checksum"] for p in state["pending"].values() if p["kind"] == "revive"}
    for candidate in candidates:
        asset_identity(candidate)
        entry = blocked.get(candidate["checksum"])
        if (
            not entry or candidate["ownerId"] != OWNER_ID or candidate["libraryId"] is not None
            or candidate["id"] in state["pending"] or candidate["checksum"] in recovering
            or candidate["isTrashed"] or candidate["visibility"] != "timeline"
        ):
            continue
        a = get_asset(candidate["id"])
        if not matches(a, candidate["id"], candidate["originalPath"], candidate["checksum"]) or a["visibility"] != "timeline":
            continue
        aid, checksum, path = a["id"], a["checksum"], a["originalPath"]
        if DRY_RUN:
            planned += 1
            continue
        if aid == entry[0] or aid in state["origins"].get(checksum, []):
            if aid in tombs:
                recovering.add(checksum)
                state["pending"][aid] = {"kind": "revive", "checksum": checksum, "path": path}
                save_state(state)
                finish_pending(state, aid)
                revived += int(aid not in tombs)
            else:
                del blocked[checksum]
                save_state(state)
                restored += 1
        else:
            if MODE == "tombstone":
                local_path(path)
            state["pending"][aid] = {"kind": "archive", "checksum": checksum, "path": path, "ts": now.isoformat()}
            save_state(state)
            api("PUT", "/api/assets", {"ids": [aid], "visibility": "archive"})
            finish_pending(state, aid)
            entombed += int(aid in tombs)
    # Pasar la misma generación del estado al truncador, no volver a leer TSV viejos.
    recent = {
        aid: path for aid, (ts, path) in tombs.items()
        if full or timestamp(ts) > now - timedelta(hours=48)
    }
    freed = wipe_files(recent, blocked={c: entry for c, entry in blocked.items() if c not in recovering})
    state["last_run"] = now.isoformat()
    if full:
        state["last_full_scan"] = now.isoformat()
    save_state(state)
    if entombed or revived:
        notify(f"Immich: lápidas={entombed}, recuperaciones={revived}, liberado={freed // 1048576} MB")
    log(
        f"{'DRY_RUN' if DRY_RUN else 'OK'} bloqueados={len(blocked)} nuevos={added}"
        f" lápidas={entombed} liberado={freed // 1048576}MB restauradas={restored}"
        f" revividas={revived} previstas={planned} consultas={REQUESTS}"
        f" duración={time.monotonic() - started:.3f}s"
    )



def run():
    os.umask(0o077)
    flags = os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW
    fd = os.open(BASE_DIR / ".run.lock", flags, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("SKIP: ya hay una ejecución activa", flush=True)
            return 0
        main()
        return 0
    finally:
        os.close(fd)


def cli(argv=None):
    global DRY_RUN
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", nargs="?", type=Path, default=Path(__file__).resolve().parent / "config.json")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    DRY_RUN = args.dry_run
    configured = False
    try:
        configure(args.config)
        configured = True
        return run()
    except Exception as e:
        # No volcar respuestas HTTP, valores de config ni URLs con credenciales.
        detail = str(e) if isinstance(e, ValueError) else f"HTTP {e.code}" if isinstance(e, urllib.error.HTTPError) else "revisar API, estado y medios"
        message = f"ERROR {type(e).__name__}: {detail}; ejecución cancelada"
        if configured:
            log(message)
        else:
            print(message, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(cli())
