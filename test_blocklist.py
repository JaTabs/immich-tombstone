import base64
import hashlib
import importlib.util
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).with_name('immich_tombstone.py')
AID = '12345678-1234-4234-8234-123456789abc'
OLD = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
OWNER = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'
DATA = b'original de prueba, no una foto real'
CHECKSUM = base64.b64encode(hashlib.sha1(DATA).digest()).decode()
REMOTE = f'/data/library/{OWNER}/prueba.jpg'


class Seguridad(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.base = self.root / 'state'
        self.base.mkdir()
        self.media = self.root / 'media'
        self.file = self.media / 'library' / OWNER / 'prueba.jpg'
        self.file.parent.mkdir(parents=True)
        self.file.write_bytes(DATA)
        (self.base / 'config.json').write_text(json.dumps({
            'api_url': 'http://127.0.0.1:2283', 'api_key': 'clave-de-prueba',
            'media_root': str(self.media),
        }))
        spec = importlib.util.spec_from_file_location('blocklist_test', SCRIPT)
        self.m = importlib.util.module_from_spec(spec)
        original_read = Path.read_text
        def read(path, *args, **kwargs):
            if path == SCRIPT.parent / 'config.json':
                return original_read(self.base / 'config.json')
            return original_read(path, *args, **kwargs)
        with patch.object(Path, 'read_text', read):
            spec.loader.exec_module(self.m)
        for name, value in {
            'BASE_DIR': self.base, 'MEDIA': self.media,
            'BLOCKLIST': self.base / 'blocklist.tsv',
            'TOMBSTONES': self.base / 'tombstones.tsv',
            'STATE': self.base / 'state.json', 'LOG': self.base / 'blocklist.log',
            'DRY_RUN': False, 'OWNER_ID': OWNER, 'MODE': 'tombstone',
        }.items():
            setattr(self.m, name, value)
        self.m.log = lambda message: None
        self.m.notify = lambda message: None
        self.real_audit_legacy = getattr(self.m, 'audit_legacy', None)
        self.real_upload_check = getattr(self.m, 'upload_check', None)
        self.real_verify_owner = getattr(self.m, 'verify_owner', None)
        self.m.verify_owner = lambda: None
        self.m.audit_legacy = lambda state: state.update(scope_verified=True)
        self.m.upload_check = lambda aid, checksum: (
            {'action': 'reject', 'reason': 'duplicate', 'assetId': aid, 'id': aid}
            if self.file.exists() else {'action': 'accept', 'id': aid}
        )
        self.asset = {
            'id': AID, 'checksum': CHECKSUM, 'ownerId': OWNER,
            'originalFileName': 'prueba.jpg', 'originalPath': REMOTE,
            'visibility': 'archive', 'isTrashed': False, 'libraryId': None,
            'isOffline': False, 'isEdited': False, 'livePhotoVideoId': None,
            'updatedAt': '2026-10-17T20:00:00Z',
        }
        self.m.api = lambda method, path, body=None: dict(self.asset)
        self.blocked = {CHECKSUM: (AID, '2026-10-17', 'prueba.jpg')}
        self.m.load_blocklist = lambda: dict(self.blocked)
        self.m.load_tombstones = lambda: {}

    def test_rechaza_rutas_fuera_de_data(self):
        for value in ('/etc/passwd', '/data/../fuera', '/data/library/../../fuera', '', '/data/thumbs/miniatura.jpg'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.m.local_path(value)

    def test_no_sigue_symlink_en_original(self):
        outside = self.root / 'no-tocar'
        outside.write_bytes(DATA)
        self.file.unlink()
        self.file.symlink_to(outside)
        with self.assertRaises((ValueError, OSError)):
            self.m.wipe_files({AID: REMOTE})
        self.assertEqual(outside.read_bytes(), DATA)

    def test_no_sigue_symlink_en_directorio(self):
        directory = self.file.parent
        self.file.unlink()
        directory.rmdir()
        outside = self.root / 'fuera'
        outside.mkdir()
        victim = outside / 'prueba.jpg'
        victim.write_bytes(DATA)
        directory.symlink_to(outside, target_is_directory=True)
        with self.assertRaises((ValueError, OSError)):
            self.m.wipe_files({AID: REMOTE})
        self.assertEqual(victim.read_bytes(), DATA)

    def test_no_trunca_hardlink(self):
        os.link(self.file, self.root / 'segunda-copia')
        with self.assertRaises((ValueError, OSError)):
            self.m.wipe_files({AID: REMOTE})
        self.assertEqual(self.file.read_bytes(), DATA)

    def test_no_trunca_asset_desarchivado(self):
        self.asset['visibility'] = 'timeline'
        self.m.wipe_files({AID: REMOTE})
        self.assertEqual(self.file.read_bytes(), DATA)

    def test_no_trunca_asset_en_papelera(self):
        self.asset['isTrashed'] = True
        self.m.wipe_files({AID: REMOTE})
        self.assertEqual(self.file.read_bytes(), DATA)

    def test_no_trunca_asset_de_biblioteca_externa(self):
        self.asset['libraryId'] = OLD
        self.m.wipe_files({AID: REMOTE})
        self.assertEqual(self.file.read_bytes(), DATA)

    def test_no_trunca_si_cambio_ruta_en_api(self):
        self.asset['originalPath'] = REMOTE + '.movida'
        self.m.wipe_files({AID: REMOTE})
        self.assertEqual(self.file.read_bytes(), DATA)

    def test_no_trunca_si_falta_visibility(self):
        del self.asset['visibility']
        with self.assertRaises(ValueError):
            self.m.wipe_files({AID: REMOTE})
        self.assertEqual(self.file.read_bytes(), DATA)

    def test_no_trunca_checksum_incorrecto(self):
        self.file.write_bytes(b'contenido diferente y valioso')
        with self.assertRaises(ValueError):
            self.m.wipe_files({AID: REMOTE})
        self.assertEqual(self.file.read_bytes(), b'contenido diferente y valioso')

    def test_lapida_valida_trunca_y_conserva_preview(self):
        preview = self.media / 'thumbs' / 'preview.jpg'
        preview.parent.mkdir()
        preview.write_bytes(b'preview')
        freed = self.m.wipe_files({AID: REMOTE})
        self.assertEqual(freed, len(DATA))
        self.assertEqual(self.file.stat().st_size, 0)
        self.assertEqual(preview.read_bytes(), b'preview')

    def test_api_caida_no_toca_original(self):
        def unavailable(*args):
            raise TimeoutError('API no disponible')
        self.m.api = unavailable
        with self.assertRaises(TimeoutError):
            self.m.wipe_files({AID: REMOTE})
        self.assertEqual(self.file.read_bytes(), DATA)

    def test_paginacion_repetida_se_aborta(self):
        pages = iter([
            {'assets': {'items': [self.asset], 'nextPage': '1'}},
            {'assets': {'items': [], 'nextPage': None}},
        ])
        self.m.api = lambda *args: next(pages)
        with self.assertRaises(ValueError):
            self.m.search_assets({})

    def test_no_captura_resultados_no_trashed(self):
        self.asset['isTrashed'] = False
        self.m.search_assets = lambda filters: [dict(self.asset)] if 'trashedAfter' in filters else []
        self.blocked.clear()
        with self.assertRaises(ValueError):
            self.m.main()
        self.assertFalse(self.m.STATE.exists())

    def test_dry_run_no_escribe_estado_ni_media(self):
        self.m.DRY_RUN = True
        self.m.search_assets = lambda filters: [dict(self.asset)]
        self.asset['isTrashed'] = True
        before = {p.name: p.read_bytes() for p in self.base.iterdir()}
        self.m.main()
        self.assertEqual({p.name: p.read_bytes() for p in self.base.iterdir()}, before)
        self.assertEqual(self.file.read_bytes(), DATA)

    def test_no_borra_original_no_vacio_al_desarchivar(self):
        self.asset['visibility'] = 'timeline'
        self.m.load_tombstones = lambda: {AID: (datetime.now(timezone.utc).isoformat(), REMOTE)}
        self.m.search_assets = lambda filters: [] if 'trashedAfter' in filters else [dict(self.asset)]
        def request(method, path, body=None):
            if method == 'DELETE':
                self.fail('DELETE eliminaría un original no vacío')
            return dict(self.asset)
        self.m.api = request
        self.m.main()
        self.assertEqual(self.file.read_bytes(), DATA)

    def test_estado_durable_antes_de_mutar_api(self):
        self.asset['visibility'] = 'timeline'
        self.blocked[CHECKSUM] = (OLD, '2026-10-17', 'prueba.jpg')
        self.m.search_assets = lambda filters: [] if 'trashedAfter' in filters else [dict(self.asset)]
        def request(method, path, body=None):
            if method == 'PUT':
                self.assertTrue(self.m.STATE.exists(), 'Falta intención durable antes de archivar')
                disk = json.loads(self.m.STATE.read_text())
                self.assertEqual(disk['pending'][AID]['kind'], 'archive')
                raise TimeoutError('Respuesta perdida tras PUT')
            return dict(self.asset)
        self.m.api = request
        with self.assertRaises(TimeoutError):
            self.m.main()
        self.assertEqual(self.file.read_bytes(), DATA)

    def test_checksum_se_captura_antes_de_fallo_posterior(self):
        self.blocked.clear()
        self.asset['isTrashed'] = True
        def search(filters):
            if 'trashedAfter' in filters:
                return [dict(self.asset)]
            raise TimeoutError('Fallo en segunda consulta')
        self.m.search_assets = search
        with self.assertRaises(TimeoutError):
            self.m.main()
        self.assertTrue(self.m.STATE.exists(), 'Se perdió la captura por un fallo posterior')
        self.assertIn(CHECKSUM, json.loads(self.m.STATE.read_text())['blocked'])

    def test_estado_corrupto_no_reinicia_blocklist(self):
        self.m.STATE.write_text('{no es JSON')
        self.m.search_assets = lambda filters: self.fail('No se debe consultar API con estado corrupto')
        with self.assertRaises(ValueError):
            self.m.main()

    def test_rechaza_timestamp_futuro(self):
        self.m.STATE.write_text(json.dumps({'last_run': '2099-01-01T00:00:00+00:00'}))
        self.m.search_assets = lambda filters: []
        with self.assertRaises(ValueError):
            self.m.main()

    def test_una_ejecucion_no_solapa_otra(self):
        import fcntl
        lock = self.base / '.run.lock'
        with lock.open('w') as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.m.main = lambda: self.fail('Se ha permitido una segunda ejecución')
            self.assertEqual(self.m.run(), 0)

    def test_archivado_completo_persistido_y_truncado(self):
        self.asset['visibility'] = 'timeline'
        self.blocked[CHECKSUM] = (OLD, '2026-10-17', 'prueba.jpg')
        self.m.search_assets = lambda filters: [] if 'trashedAfter' in filters else [dict(self.asset)]
        def request(method, path, body=None):
            if method == 'PUT':
                self.asset['visibility'] = 'archive'
                return None
            return dict(self.asset)
        self.m.api = request
        self.m.main()
        disk = json.loads(self.m.STATE.read_text())
        self.assertEqual(disk['blocked'][CHECKSUM][0], AID)
        self.assertIn(AID, disk['tombs'])
        self.assertEqual(disk['pending'], {})
        self.assertEqual(self.file.stat().st_size, 0)
        self.assertEqual(self.m.STATE.stat().st_mode & 0o777, 0o600)

    def test_resuelve_put_exitoso_con_respuesta_perdida(self):
        self.asset['visibility'] = 'timeline'
        self.blocked[CHECKSUM] = (OLD, '2026-10-17', 'prueba.jpg')
        self.m.search_assets = lambda filters: [] if 'trashedAfter' in filters else [dict(self.asset)]
        def request(method, path, body=None):
            if method == 'PUT':
                self.asset['visibility'] = 'archive'
                raise TimeoutError('Servidor aplicó PUT; conexión cortada')
            return dict(self.asset)
        self.m.api = request
        with self.assertRaises(TimeoutError):
            self.m.main()
        self.assertEqual(self.file.read_bytes(), DATA)
        self.m.main()
        disk = json.loads(self.m.STATE.read_text())
        self.assertEqual(disk['pending'], {})
        self.assertEqual(disk['blocked'][CHECKSUM][0], AID)
        self.assertEqual(self.file.stat().st_size, 0)

    def test_put_fallido_conserva_contenido_y_no_reintenta_archivar(self):
        self.asset['visibility'] = 'timeline'
        self.blocked[CHECKSUM] = (OLD, '2026-10-17', 'prueba.jpg')
        self.m.search_assets = lambda filters: [] if 'trashedAfter' in filters else [dict(self.asset)]
        def request(method, path, body=None):
            if method == 'PUT':
                raise TimeoutError('No se sabe si se aplicó')
            return dict(self.asset)
        self.m.api = request
        with self.assertRaises(TimeoutError):
            self.m.main()
        self.m.main()
        disk = json.loads(self.m.STATE.read_text())
        self.assertEqual(disk['pending'], {})
        self.assertNotIn(CHECKSUM, disk['blocked'])
        self.assertEqual(self.file.read_bytes(), DATA)

    def test_no_avanza_cursor_tras_fallo_en_truncado(self):
        previous = '2026-10-17T00:00:00+00:00'
        self.m.STATE.write_text(json.dumps({'last_run': previous}))
        self.m.load_tombstones = lambda: {AID: (datetime.now(timezone.utc).isoformat(), REMOTE)}
        self.m.search_assets = lambda filters: []
        self.file.write_bytes(b'no coincide con checksum')
        with self.assertRaises(ValueError):
            self.m.main()
        self.assertEqual(json.loads(self.m.STATE.read_text())['last_run'], previous)

    def test_no_borra_lapida_rellena_durante_hash(self):
        calls = 0
        def request(method, path, body=None):
            nonlocal calls
            calls += 1
            if calls == 2:
                self.asset['visibility'] = 'timeline'
            return dict(self.asset)
        self.m.api = request
        self.m.wipe_files({AID: REMOTE})
        self.assertEqual(self.file.read_bytes(), DATA)

    def test_encoded_path_exacto_sin_tocar_prefijos_vecinos(self):
        encoded = self.media / 'encoded-video' / OWNER / '12' / '34'
        encoded.mkdir(parents=True)
        expected = encoded / (AID + '.mp4')
        neighbor = encoded / (AID + '-otra-copia.mp4')
        expected.write_bytes(b'video derivado')
        neighbor.write_bytes(b'no tocar')
        self.m.wipe_files({AID: REMOTE})
        self.assertEqual(expected.stat().st_size, 0)
        self.assertEqual(neighbor.read_bytes(), b'no tocar')

    def test_atomicidad_snapshot_si_falla_replace(self):
        self.m.STATE.write_text('{"estado":"anterior"}')
        with patch.object(self.m.os, 'replace', side_effect=OSError('disco')):
            with self.assertRaises(OSError):
                self.m.save_state({'estado': 'nuevo'})
        self.assertEqual(json.loads(self.m.STATE.read_text()), {'estado': 'anterior'})
        self.assertEqual(list(self.base.glob('.state-*')), [])

    def test_restaura_id_original_aunque_exista_otra_lapida(self):
        self.asset['visibility'] = 'timeline'
        self.asset['id'] = OLD
        self.m.STATE.write_text(json.dumps({
            'schema': 2, 'blocked': {CHECKSUM: [AID, '2026-10-17', 'prueba.jpg']},
            'tombs': {AID: [datetime.now(timezone.utc).isoformat(), REMOTE]},
            'pending': {}, 'origins': {CHECKSUM: [OLD]},
        }))
        self.m.search_assets = lambda filters: [] if 'trashedAfter' in filters else [dict(self.asset)]
        def request(method, path, body=None):
            if method != 'GET':
                self.fail('Una restauración no debe volver a archivarse')
            return dict(self.asset)
        self.m.api = request
        self.m.main()
        self.assertNotIn(CHECKSUM, json.loads(self.m.STATE.read_text())['blocked'])
        self.assertEqual(self.file.read_bytes(), DATA)

    def test_reconciliacion_diaria_reintenta_lapidas_de_mas_de_48h(self):
        self.m.STATE.write_text(json.dumps({
            'schema': 2, 'blocked': self.blocked,
            'tombs': {AID: ['2026-07-01T00:00:00+00:00', REMOTE]}, 'pending': {},
        }))
        self.m.search_assets = lambda filters: []
        self.m.main()
        self.assertEqual(self.file.stat().st_size, 0)

    def test_no_borra_si_original_se_rellena_durante_revalidacion(self):
        self.file.write_bytes(b'')
        self.asset['visibility'] = 'timeline'
        self.m.load_tombstones = lambda: {AID: (datetime.now(timezone.utc).isoformat(), REMOTE)}
        self.m.search_assets = lambda filters: [] if 'trashedAfter' in filters else [dict(self.asset)]
        calls = 0
        def request(method, path, body=None):
            nonlocal calls
            if method == 'DELETE':
                self.fail('El original ya no está vacío')
            calls += 1
            if calls == 2:
                self.file.write_bytes(DATA)
            return dict(self.asset)
        self.m.api = request
        self.m.main()
        self.assertEqual(self.file.read_bytes(), DATA)

    def test_recuperacion_pendiente_reintenta_delete_solo_si_sigue_vacio(self):
        import urllib.error
        self.file.write_bytes(b'')
        self.asset['visibility'] = 'timeline'
        self.m.STATE.write_text(json.dumps({
            'schema': 2, 'blocked': self.blocked,
            'tombs': {AID: [datetime.now(timezone.utc).isoformat(), REMOTE]},
            'pending': {AID: {'kind': 'revive', 'checksum': CHECKSUM, 'path': REMOTE}},
        }))
        self.m.search_assets = lambda filters: []
        deleted = False
        def request(method, path, body=None):
            nonlocal deleted
            if method == 'DELETE':
                deleted = True
                self.file.unlink()
                return None
            if deleted:
                raise urllib.error.HTTPError('http://local', 404, 'No existe', {}, None)
            return dict(self.asset)
        self.m.api = request
        self.m.main()
        self.assertFalse(self.file.exists())
        disk = json.loads(self.m.STATE.read_text())
        self.assertEqual(disk['pending'], {})
        self.assertNotIn(CHECKSUM, disk['blocked'])

    def test_redireccion_no_filtra_api_key_a_otro_destino(self):
        import http.server
        import threading
        received = []
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(handler):
                if handler.path == '/redirect':
                    handler.send_response(302)
                    handler.send_header('Location', '/sink')
                    handler.end_headers()
                else:
                    received.append(handler.headers.get('x-api-key'))
                    handler.send_response(200)
                    handler.end_headers()
                    handler.wfile.write(b'{}')
            def log_message(handler, *args):
                pass
        server = http.server.HTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            self.m.API = f'http://127.0.0.1:{server.server_port}'
            # Deshacer el doble de API: esta prueba recorre HTTP real.
            original = importlib.util.spec_from_file_location('http_test', SCRIPT)
            module = importlib.util.module_from_spec(original)
            read = Path.read_text
            def configured(path, *args, **kwargs):
                if path == SCRIPT.parent / 'config.json':
                    return read(self.base / 'config.json')
                return read(path, *args, **kwargs)
            with patch.object(Path, 'read_text', configured):
                original.loader.exec_module(module)
            module.API = self.m.API
            with self.assertRaises(ValueError):
                module.api('GET', '/redirect')
            self.assertEqual(received, [])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_400_ambiguo_no_confirma_ausencia_ni_con_busqueda_vacia(self):
        import io
        import urllib.error
        def request(method, path, body=None):
            if method == 'GET':
                raise urllib.error.HTTPError('http://local', 400, 'Bad Request', {}, io.BytesIO(b'{"message":"Not found or no asset.read access"}'))
            return {'assets': {'items': [], 'nextPage': None}}
        self.m.api = request
        with self.assertRaises(urllib.error.HTTPError):
            self.m.get_asset(AID)

    def test_error_400_de_permisos_no_se_interpreta_como_ausencia(self):
        import io
        import urllib.error
        def request(method, path, body=None):
            if method == 'GET':
                raise urllib.error.HTTPError('http://local', 400, 'Bad Request', {}, io.BytesIO(b'{"message":"Not found or no asset.read access"}'))
            return {'assets': {'items': [dict(self.asset)], 'nextPage': None}}
        self.m.api = request
        with self.assertRaises(urllib.error.HTTPError):
            self.m.get_asset(AID)

    def test_delete_asincrono_no_se_reenvia_si_ya_fue_aceptado(self):
        self.file.write_bytes(b'')
        self.asset['visibility'] = 'timeline'
        self.m.STATE.write_text(json.dumps({
            'schema': 2, 'blocked': self.blocked,
            'tombs': {AID: [datetime.now(timezone.utc).isoformat(), REMOTE]},
            'pending': {AID: {'kind': 'revive', 'checksum': CHECKSUM, 'path': REMOTE}},
        }))
        self.m.search_assets = lambda filters: []
        deletes = []
        def request(method, path, body=None):
            if method == 'DELETE':
                deletes.append(body)
                return None
            return dict(self.asset)
        self.m.api = request
        self.m.upload_check = lambda aid, checksum: {'action': 'reject', 'reason': 'duplicate', 'assetId': aid, 'id': aid}
        self.m.main()
        self.m.main()
        self.assertEqual(len(deletes), 1)
        self.assertTrue(json.loads(self.m.STATE.read_text())['pending'][AID]['sent'])
        self.m.upload_check = lambda aid, checksum: {'action': 'accept', 'id': aid}
        self.m.main()
        disk = json.loads(self.m.STATE.read_text())
        self.assertNotIn(AID, disk['pending'])
        self.assertNotIn(CHECKSUM, disk['blocked'])

    def test_no_borra_live_photo_con_video_vinculado(self):
        self.file.write_bytes(b'')
        self.asset['visibility'] = 'timeline'
        self.asset['livePhotoVideoId'] = OLD
        self.m.load_tombstones = lambda: {AID: (datetime.now(timezone.utc).isoformat(), REMOTE)}
        self.m.search_assets = lambda filters: [] if 'trashedAfter' in filters else [dict(self.asset)]
        def request(method, path, body=None):
            if method == 'DELETE':
                self.fail('DELETE también podría borrar el vídeo vinculado no vacío')
            return dict(self.asset)
        self.m.api = request
        with self.assertRaises(ValueError):
            self.m.main()

    def test_no_captura_papelera_de_partner(self):
        self.blocked.clear()
        self.asset.update(isTrashed=True, ownerId=OLD)
        self.m.search_assets = lambda filters: [dict(self.asset)] if 'trashedAfter' in filters else []
        self.m.main()
        disk = json.loads(self.m.STATE.read_text())
        self.assertEqual(disk['blocked'], {})
        self.assertEqual(disk['origins'], {})

    def test_no_captura_papelera_de_biblioteca_externa(self):
        self.blocked.clear()
        self.asset.update(isTrashed=True, libraryId=OLD)
        self.m.search_assets = lambda filters: [dict(self.asset)] if 'trashedAfter' in filters else []
        self.m.main()
        self.assertEqual(json.loads(self.m.STATE.read_text())['blocked'], {})

    def test_no_trunca_asset_de_otro_propietario(self):
        self.asset['ownerId'] = OLD
        self.m.wipe_files({AID: REMOTE})
        self.assertEqual(self.file.read_bytes(), DATA)

    def test_no_cambia_propietario_de_estado_existente(self):
        self.m.STATE.write_text(json.dumps({
            'schema': 2, 'owner_id': OLD, 'blocked': {}, 'tombs': {}, 'pending': {},
        }))
        self.m.search_assets = lambda filters: self.fail('Estado de otra persona')
        with self.assertRaises(ValueError):
            self.m.main()

    def test_migracion_no_confia_en_checksum_legacy_de_partner(self):
        self.assertTrue(callable(self.real_audit_legacy), 'Falta auditar el scope heredado')
        self.asset['ownerId'] = OLD
        state = {'blocked': dict(self.blocked), 'tombs': {}, 'origins': {}, 'pending': {}}
        self.real_audit_legacy(state)
        self.assertEqual(state['blocked'], {})
        self.assertIn(CHECKSUM, state['quarantine']['blocked'])

    def test_migracion_cuarentena_id_legacy_inaccesible_sin_perder_registro(self):
        self.assertTrue(callable(self.real_audit_legacy), 'Falta auditar el scope heredado')
        import urllib.error
        def request(*args):
            raise urllib.error.HTTPError('http://local',400,'Bad Request',{},None)
        self.m.api = request
        state = {'blocked': dict(self.blocked), 'tombs': {}, 'origins': {}, 'pending': {}}
        self.real_audit_legacy(state)
        self.assertEqual(state['blocked'], {})
        self.assertEqual(state['quarantine']['blocked'][CHECKSUM], list(self.blocked[CHECKSUM]))

    def test_migracion_conserva_checksum_interno_propio_verificado(self):
        self.assertTrue(callable(self.real_audit_legacy), 'Falta auditar el scope heredado')
        state = {'blocked': dict(self.blocked), 'tombs': {AID:['2026-10-17T00:00:00+00:00',REMOTE]}, 'origins': {}, 'pending': {}}
        self.real_audit_legacy(state)
        self.assertIn(CHECKSUM, state['blocked'])
        self.assertIn(AID, state['tombs'])
        self.assertTrue(state['scope_verified'])

    def test_upload_check_no_usa_otro_usuario_para_confirmar_ausencia(self):
        self.assertTrue(callable(self.real_upload_check), 'Falta preflight con scope verificado')
        self.m.api = lambda *args: {'id': OLD}
        if self.real_verify_owner:
            self.m.verify_owner = self.real_verify_owner
        with self.assertRaises(ValueError):
            self.real_upload_check(AID, CHECKSUM)

    def test_upload_check_valida_respuesta_del_mismo_id(self):
        self.assertTrue(callable(self.real_upload_check), 'Falta preflight con scope verificado')
        self.m.api = lambda method,path,body=None: {'id': OWNER} if method=='GET' else {'results':[{'id': OLD, 'action':'accept'}]}
        with self.assertRaises(ValueError):
            self.real_upload_check(AID,CHECKSUM)

    def test_respuesta_delete_perdida_no_cancela_recuperacion_en_papelera(self):
        self.file.write_bytes(b'')
        self.asset.update(visibility='timeline', isTrashed=True)
        state = {'schema':2, 'owner_id':OWNER, 'scope_verified':True,
                 'blocked':dict(self.blocked), 'tombs':{AID:[datetime.now(timezone.utc).isoformat(),REMOTE]},
                 'pending':{AID:{'kind':'revive','checksum':CHECKSUM,'path':REMOTE}}}
        self.m.STATE.write_text(json.dumps(state))
        self.m.search_assets = lambda filters: []
        self.m.main()
        disk = json.loads(self.m.STATE.read_text())
        self.assertIn(AID,disk['pending'])
        self.assertIn(CHECKSUM,disk['blocked'])
        self.m.upload_check = lambda aid,c: {'id':aid,'action':'accept'}
        self.m.main()
        disk = json.loads(self.m.STATE.read_text())
        self.assertNotIn(AID,disk['pending'])
        self.assertNotIn(CHECKSUM,disk['blocked'])

    def test_recuperacion_pendiente_no_archiva_otra_copia_del_checksum(self):
        other = dict(self.asset, id=OLD, visibility='timeline')
        state = {'schema':2, 'owner_id':OWNER, 'scope_verified':True,
                 'blocked':dict(self.blocked), 'tombs':{AID:[datetime.now(timezone.utc).isoformat(),REMOTE]},
                 'pending':{AID:{'kind':'revive','checksum':CHECKSUM,'path':REMOTE,'sent':True}}}
        self.m.STATE.write_text(json.dumps(state))
        self.m.search_assets = lambda filters: [] if 'trashedAfter' in filters else [other]
        self.m.upload_check = lambda aid,c:{'id':aid,'action':'reject','reason':'duplicate','assetId':aid}
        def request(method,path,body=None):
            if method!='GET':self.fail('Una recuperación pendiente no debe crear otra lápida')
            return dict(self.asset)
        self.m.api=request
        self.m.main()
        self.assertEqual(self.file.read_bytes(),DATA)

    def test_main_no_audita_ni_trunca_con_api_key_de_otra_persona(self):
        self.assertTrue(callable(self.real_verify_owner), 'Falta verificar identidad al inicio')
        self.m.verify_owner = self.real_verify_owner
        self.m.api = lambda *args: {'id': OLD}
        self.m.audit_legacy = lambda state: self.fail('No se puede auditar con otra identidad')
        with self.assertRaises(ValueError):
            self.m.main()
        self.assertEqual(self.file.read_bytes(), DATA)
        self.assertFalse(self.m.STATE.exists())


if __name__ == '__main__':
    unittest.main()
