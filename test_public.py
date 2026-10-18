"""Pruebas de portabilidad y configuración, sin fotos ni credenciales reales."""
import importlib.util
import json
import subprocess
import sys
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location('fixtures', Path(__file__).with_name('test_blocklist.py'))
fixtures = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixtures)


class PublicTests(unittest.TestCase):
    setUp = fixtures.Seguridad.setUp

    def config(self, **changes):
        path = self.base / 'config.json'
        config = json.loads(path.read_text())
        config.update(owner_id=fixtures.OWNER, mode='archive')
        config.update(changes)
        path.write_text(json.dumps(config))
        path.chmod(0o600)
        return path

    def test_help_no_necesita_config_ni_red(self):
        result = subprocess.run([sys.executable, '-I', str(fixtures.SCRIPT), '--help'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('--dry-run', result.stdout)
        self.assertNotIn('Traceback', result.stderr)

    def test_config_externa_define_directorio_de_estado(self):
        self.assertTrue(callable(getattr(self.m, 'configure', None)))
        self.m.configure(self.config())
        self.assertEqual(self.m.BASE_DIR, self.base)
        self.assertEqual(self.m.STATE, self.base / 'state.json')
        self.assertEqual(self.m.MODE, 'archive')

    def test_config_rechaza_secreto_legible_por_otros(self):
        self.assertTrue(callable(getattr(self.m, 'configure', None)))
        path = self.config()
        path.chmod(0o644)
        with self.assertRaises(ValueError):
            self.m.configure(path)

    def test_config_tombstone_requiere_consentimiento_explicito(self):
        self.assertTrue(callable(getattr(self.m, 'configure', None)))
        with self.assertRaises(ValueError):
            self.m.configure(self.config(mode='tombstone', allow_truncate=False))

    def test_config_rechaza_modo_delete_que_provoca_bucle(self):
        self.assertTrue(callable(getattr(self.m, 'configure', None)))
        with self.assertRaises(ValueError):
            self.m.configure(self.config(mode='delete'))

    def test_config_rechaza_url_con_credenciales_o_query(self):
        self.assertTrue(callable(getattr(self.m, 'configure', None)))
        for url in ('file:///tmp/api', 'https://user:secret@example.invalid', 'https://example.invalid?key=secret'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                self.m.configure(self.config(api_url=url))

    def test_prefijo_personalizado_respeta_limite_de_directorio(self):
        self.m.MEDIA_PREFIX = '/custom/photos'
        self.assertEqual(self.m.local_path('/custom/photos/library/user/original.jpg'), self.media / 'library/user/original.jpg')
        for value in ('/custom/photos-other/library/user/original.jpg', '/custom/photos/../library/user/original.jpg'):
            with self.assertRaises(ValueError):
                self.m.local_path(value)

    def test_archive_no_accede_a_ficheros_al_archivar(self):
        self.m.MODE = 'archive'
        self.m.MEDIA = self.root / 'no-montado'
        self.asset.update(visibility='timeline', originalPath='/unrelated/container/library/user/photo.jpg')
        self.blocked[fixtures.CHECKSUM] = (fixtures.OLD, '2026-10-17', 'prueba.jpg')
        self.m.search_assets = lambda filters: [] if 'trashedAfter' in filters else [dict(self.asset)]
        def api(method, path, body=None):
            if method == 'PUT':
                self.asset['visibility'] = 'archive'
                return None
            if method != 'GET':
                self.fail('archive no debe borrar assets')
            return dict(self.asset)
        self.m.api = api
        self.m.main()
        self.assertEqual(self.file.read_bytes(), fixtures.DATA)
        self.assertIn(fixtures.AID, json.loads(self.m.STATE.read_text())['tombs'])

    def test_archive_desarchiva_sin_fs_ni_delete(self):
        self.m.MODE = 'archive'
        self.asset.update(visibility='timeline', originalPath='/unrelated/library/user/photo.jpg')
        self.m.load_tombstones = lambda: {fixtures.AID: ['2026-10-17T00:00:00+00:00', self.asset['originalPath']]}
        self.m.search_assets = lambda filters: [] if 'trashedAfter' in filters else [dict(self.asset)]
        def api(method, path, body=None):
            if method != 'GET':
                self.fail('Una copia archivada no necesita DELETE para recuperarse')
            return dict(self.asset)
        self.m.api = api
        self.m.main()
        self.assertNotIn(fixtures.CHECKSUM, json.loads(self.m.STATE.read_text())['blocked'])
        self.assertEqual(self.file.read_bytes(), fixtures.DATA)

    def test_estado_no_cambia_silenciosamente_de_modo(self):
        self.m.STATE.write_text(json.dumps({'schema':2,'mode':'archive','blocked':{},'tombs':{},'pending':{}}))
        self.m.MODE = 'tombstone'
        with self.assertRaises(ValueError):
            self.m.load_state()

    def test_ejemplo_no_activa_truncados_por_defecto(self):
        config = json.loads(Path(__file__).with_name('config.example.json').read_text())
        self.assertEqual(config['mode'], 'archive')
        self.assertIs(config['allow_truncate'], False)

    def test_config_rechaza_clave_con_controles_sin_revelarla(self):
        secret='DUMMY_PRIVATE_KEY\nInjected-Header: bad'
        with self.assertRaises(ValueError) as error:
            self.m.configure(self.config(api_key=secret))
        self.assertNotIn('DUMMY_PRIVATE_KEY',str(error.exception))

    def test_config_rechaza_archivos_de_otro_usuario(self):
        from unittest.mock import patch
        import os
        path=self.config()
        with patch.object(self.m.os,'geteuid',return_value=os.geteuid()+1):
            with self.assertRaises(ValueError):
                self.m.configure(path)

    def test_config_rechaza_directorios_como_archivo(self):
        directory=self.base/'not-a-file'
        directory.mkdir(mode=0o700)
        directory.chmod(0o600)
        with self.assertRaises(ValueError):
            self.m.configure(directory)


if __name__ == '__main__':
    unittest.main()
