#!/usr/bin/env python3
"""Instalador de immich-tombstone, sin dependencias Python externas.

Variables: IT_DIR, IT_API_URL, IT_API_KEY, IT_MODE, IT_MEDIA_ROOT,
IT_MEDIA_PREFIX, IT_NTFY_URL, IT_CRON_MINUTES, IT_NO_CRON,
IT_ALLOW_TRUNCATE=1 (consentimiento explícito para tombstone).
"""
import argparse
import fcntl
import getpass
import hashlib
import importlib.util
import json
import os
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

SOURCE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('immich_tombstone_install', SOURCE / 'immich_tombstone.py')
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)


def value(name, message, default=None, secret=False):
    if name in os.environ:
        return os.environ[name]
    if sys.stdin.isatty():
        answer = getpass.getpass(message + ': ') if secret else input(f'{message} [{default or ""}]: ')
        return answer or default
    if default is not None:
        return default
    raise ValueError(f'Falta {name}; definirlo para instalación no interactiva')


def atomic_write(path, data, mode=0o600):
    fd, name = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
            f.flush()
            os.fchmod(f.fileno(), mode)
            os.fsync(f.fileno())
        os.replace(name, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def read_cron(tool):
    if not tool:
        return ''
    result = subprocess.run([tool, '-l'], capture_output=True, text=True,
                            env=dict(os.environ, LC_ALL='C'))
    if result.returncode == 0:
        return result.stdout
    if result.returncode == 1 and result.stderr.startswith('no crontab for '):
        return ''
    raise ValueError('No se pudo leer crontab; no se sobrescribirá')


def cron_line(target, minutes, marker):
    paths = [sys.executable, str(target / 'immich_tombstone.py'), str(target / 'config.json'), str(target / 'cron-errors.log')]
    if any(c in p for p in paths for c in '\r\n%'):
        raise ValueError('Las rutas del cron no pueden contener saltos de línea ni %')
    python, script, config, errors = map(shlex.quote, paths)
    schedule = '* * * * *' if minutes == 1 else f'*/{minutes} * * * *'
    return f'{schedule} umask 077; {python} -I {script} {config} >/dev/null 2>>{errors} {marker}'


def install():
    os.umask(0o077)
    target = Path(value('IT_DIR', 'Directorio de instalación', '/opt/immich-tombstone')).expanduser().absolute()
    no_cron = os.environ.get('IT_NO_CRON') == '1'
    minutes = int(value('IT_CRON_MINUTES', 'Intervalo cron en minutos', '1'))
    if not 1 <= minutes <= 59:
        raise ValueError('El intervalo cron debe estar entre 1 y 59 minutos')
    marker = '# immich-tombstone:' + hashlib.sha256(str(target).encode()).hexdigest()[:12]
    line = cron_line(target, minutes, marker) if not no_cron else None
    if target.is_symlink() or (target.exists() and (not target.is_dir() or target.stat().st_uid != os.geteuid())):
        raise ValueError('El directorio debe ser propio y no ser un enlace simbólico')
    tool = shutil.which('crontab')
    if not no_cron and not tool:
        raise ValueError('Falta crontab; instalar cron o usar IT_NO_CRON=1')
    previous_cron = read_cron(tool)
    if any(not l.lstrip().startswith('#') and (str(target / 'immich_tombstone.py') in l or marker in l)
           for l in previous_cron.splitlines()):
        raise ValueError('Pausar el cron existente y esperar a que termine antes de actualizar')
    config_path = target / 'config.json'
    config = {}
    if config_path.exists() or config_path.is_symlink():
        info = config_path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_uid != os.geteuid():
            raise ValueError('El config existente debe ser propio y privado: chmod 600')
        config = json.loads(config_path.read_text())
    config['api_url'] = value('IT_API_URL', 'URL base de Immich', config.get('api_url'))
    config['api_key'] = value('IT_API_KEY', 'API key', config.get('api_key'), secret=True)
    config['mode'] = value('IT_MODE', 'Modo archive o tombstone', config.get('mode', 'archive'))
    config['allow_truncate'] = config.get('allow_truncate') is True
    if 'IT_ALLOW_TRUNCATE' in os.environ:
        config['allow_truncate'] = os.environ['IT_ALLOW_TRUNCATE'] == '1'
    if config['mode'] == 'tombstone':
        if not config['allow_truncate']:
            consent = value('IT_ALLOW_TRUNCATE', 'Escribir TRUNCATE para aceptar la destrucción irreversible', '')
            config['allow_truncate'] = consent == 'TRUNCATE'
        config['media_root'] = value('IT_MEDIA_ROOT', 'UPLOAD_LOCATION accesible en este host', config.get('media_root'))
        config['media_prefix'] = value('IT_MEDIA_PREFIX', 'Prefijo de ruta dentro del contenedor', config.get('media_prefix', '/data'))
    config['ntfy_url'] = value('IT_NTFY_URL', 'URL ntfy opcional', config.get('ntfy_url', ''))
    # Validar config e identidad antes de reemplazar archivos existentes.
    with tempfile.TemporaryDirectory(prefix='immich-tombstone-setup-') as scratch:
        candidate = Path(scratch) / 'config.json'
        candidate.write_text(json.dumps(config))
        candidate.chmod(0o600)
        core.DRY_RUN = True
        core.configure(candidate)
        core.verify_owner()
        version = core.api('GET', '/api/server/version')
        if version.get('major') != 3:
            raise ValueError('Solo se admite Immich v3; comprobar compatibilidad antes de otras versiones')
        config['owner_id'] = core.OWNER_ID
    if (target / 'state.json').exists():
        state = json.loads((target / 'state.json').read_text())
        if state.get('owner_id', config['owner_id']) != config['owner_id'] or state.get('mode', config['mode']) != config['mode']:
            raise ValueError('El estado existente pertenece a otro propietario o modo')
    target.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = target.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid():
        raise ValueError('El directorio cambió de tipo o propietario durante la instalación')
    target.chmod(0o700)
    lock = os.open(target / '.run.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('Hay una ejecución activa; esperar antes de instalar') from None
        existing = [target / name for name in ('immich_tombstone.py', 'config.json', 'state.json', 'blocklist.tsv', 'tombstones.tsv') if (target / name).exists()]
        if existing:
            backup = target / 'backups' / str(time.time_ns())
            backup.mkdir(parents=True, mode=0o700)
            backup.parent.chmod(0o700)
            for path in existing:
                if path.is_symlink() or not path.is_file():
                    raise ValueError('No se respaldarán enlaces ni archivos especiales')
                atomic_write(backup / path.name, path.read_bytes())
        atomic_write(target / 'immich_tombstone.py', (SOURCE / 'immich_tombstone.py').read_bytes(), 0o700)
        atomic_write(config_path, json.dumps(config, indent=2).encode())
        # El instalador mantiene el flock; validar en este proceso sin duplicarlo.
        core.configure(config_path)
        core.DRY_RUN = True
        core.main()
        if not no_cron:
            if read_cron(tool) != previous_cron:
                raise ValueError('Crontab cambió durante la instalación; no se sobrescribirá')
            kept = [l for l in previous_cron.splitlines() if not l.endswith(marker)]
            updated = '\n'.join(kept + [line]) + '\n'
            subprocess.run([tool, '-'], input=updated, text=True, check=True)
        print(f'Instalado en {target}; modo {config["mode"]}.')
        print('Cron no modificado (IT_NO_CRON=1).' if no_cron else f'Cron activado: intervalo {minutes} minuto(s).')
        print('La validación no ha modificado medios. El cron sí aplicará el modo configurado.')
    finally:
        os.close(lock)


if __name__ == '__main__':
    argparse.ArgumentParser(description=__doc__).parse_args()
    try:
        install()
    except Exception as e:
        # Mensajes de validación propios; nunca imprimir config, claves ni cuerpos HTTP.
        message = str(e) if isinstance(e, ValueError) else type(e).__name__
        print(f'ERROR: {message}. No se activará un cron nuevo.', file=sys.stderr)
        sys.exit(1)
