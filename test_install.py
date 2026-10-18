"""Instalación contra una API local ficticia y un crontab aislado."""
import http.server
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OWNER = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'
KEY = 'DUMMY_KEY_ONLY_FOR_INSTALL_TESTS'


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.target = self.root / 'install with spaces'
        self.cron = self.root / 'crontab.txt'
        self.cron.write_text('17 8 * * * /usr/bin/true # keep-unrelated-job\n')
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        stub = self.bin / 'crontab'
        stub.write_text('#!' + sys.executable + ' -I\n'
                        'import os,sys\nfrom pathlib import Path\n'
                        'p=Path(os.environ["TEST_CRONTAB"])\n'
                        'if sys.argv[1]=="-l": sys.stdout.write(p.read_text())\n'
                        'else: p.write_text(sys.stdin.read())\n')
        stub.chmod(0o700)
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(handler):
                if handler.path == '/api/users/me':
                    result = {'id': OWNER}
                elif handler.path == '/api/server/version':
                    result = {'major': 3, 'minor': 3, 'patch': 3}
                else:
                    handler.send_error(404)
                    return
                handler.respond(result)
            def do_POST(handler):
                size = int(handler.headers.get('Content-Length','0'))
                handler.rfile.read(size)
                if handler.path != '/api/search/metadata':
                    handler.send_error(405)
                    return
                handler.respond({'assets': {'items': [], 'nextPage': None}})
            def respond(handler, result):
                data = json.dumps(result).encode()
                handler.send_response(200)
                handler.send_header('Content-Type', 'application/json')
                handler.send_header('Content-Length', str(len(data)))
                handler.end_headers()
                handler.wfile.write(data)
            def log_message(handler, *args):
                pass
        self.server = http.server.HTTPServer(('127.0.0.1',0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)
        self.env = dict(os.environ, PATH=str(self.bin)+os.pathsep+os.environ.get('PATH',''),
                        TEST_CRONTAB=str(self.cron), IT_DIR=str(self.target),
                        IT_API_URL=f'http://127.0.0.1:{self.server.server_port}', IT_API_KEY=KEY,
                        IT_MODE='archive', IT_NTFY_URL='', IT_CRON_MINUTES='1', IT_NO_CRON='1')

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def install(self, **env):
        return subprocess.run([sys.executable, '-I', str(ROOT/'install.py')],
                              env=dict(self.env,**env), cwd=self.root, text=True,
                              capture_output=True, timeout=15)

    def test_instalacion_archive_sin_media_y_sin_claves_en_salida(self):
        result = self.install()
        self.assertEqual(result.returncode,0,result.stderr)
        config=json.loads((self.target/'config.json').read_text())
        self.assertEqual(config['owner_id'],OWNER)
        self.assertEqual(config['mode'],'archive')
        self.assertIs(config['allow_truncate'],False)
        self.assertEqual((self.target/'config.json').stat().st_mode & 0o777,0o600)
        self.assertEqual(self.target.stat().st_mode & 0o777,0o700)
        self.assertNotIn(KEY,result.stdout+result.stderr)
        self.assertFalse((self.target/'state.json').exists())
        self.assertIn('keep-unrelated-job',self.cron.read_text())

    def test_cron_un_minuto_preserva_otras_tareas_y_cita_rutas(self):
        result = self.install(IT_NO_CRON='0')
        self.assertEqual(result.returncode,0,result.stderr)
        lines=self.cron.read_text().splitlines()
        self.assertIn('17 8 * * * /usr/bin/true # keep-unrelated-job',lines)
        ours=next(l for l in lines if '# immich-tombstone:' in l)
        self.assertTrue(ours.startswith('* * * * * '))
        self.assertIn("'"+str(self.target/'immich_tombstone.py')+"'",ours)
        self.assertIn('-I',ours)

    def test_no_activa_truncado_sin_consentimiento(self):
        result=self.install(IT_MODE='tombstone',IT_MEDIA_ROOT=str(self.root),IT_ALLOW_TRUNCATE='0')
        self.assertNotEqual(result.returncode,0)
        self.assertFalse((self.target/'immich_tombstone.py').exists())
        self.assertNotIn(KEY,result.stdout+result.stderr)

    def test_rechaza_inyeccion_en_intervalo_cron(self):
        before=self.cron.read_text()
        result=self.install(IT_CRON_MINUTES='1\n* * * * * /bad')
        self.assertNotEqual(result.returncode,0)
        self.assertEqual(self.cron.read_text(),before)
        self.assertFalse((self.target/'immich_tombstone.py').exists())

    def test_rechaza_saltos_o_porcentaje_en_ruta_cron(self):
        for name in ('bad\njob','bad%job'):
            with self.subTest(name=name):
                result=self.install(IT_DIR=str(self.root/name),IT_NO_CRON='0')
                self.assertNotEqual(result.returncode,0)
                self.assertFalse((self.root/name/'immich_tombstone.py').exists())

    def test_upgrade_con_cron_activo_se_niega_sin_sobrescribir(self):
        self.target.mkdir(mode=0o700)
        config=self.target/'config.json'
        config.write_text(json.dumps({'api_url':self.env['IT_API_URL'],'api_key':KEY,'mode':'archive'}))
        config.chmod(0o600)
        before=config.read_bytes()
        self.cron.write_text('* * * * * python3 '+str(self.target/'immich_tombstone.py')+'\n')
        result=self.install()
        self.assertNotEqual(result.returncode,0)
        self.assertEqual(config.read_bytes(),before)
        self.assertFalse((self.target/'immich_tombstone.py').exists())

    def test_upgrade_respalda_estado_y_preserva_secreto_sin_reescribirlo_en_logs(self):
        self.target.mkdir(mode=0o700)
        config=self.target/'config.json'
        config.write_text(json.dumps({'api_url':self.env['IT_API_URL'],'api_key':KEY,'mode':'archive'}))
        config.chmod(0o600)
        state=self.target/'state.json'
        state.write_text(json.dumps({'schema':2,'owner_id':OWNER,'mode':'archive','scope_verified':True,
                                     'blocked':{},'tombs':{},'origins':{},'pending':{}}))
        state.chmod(0o600)
        before=state.read_bytes()
        result=self.install()
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(state.read_bytes(),before)
        backups=list((self.target/'backups').glob('*/state.json'))
        self.assertEqual(len(backups),1)
        self.assertEqual(backups[0].read_bytes(),before)
        self.assertEqual(backups[0].stat().st_mode & 0o777,0o600)
        self.assertNotIn(KEY,result.stdout+result.stderr)

    def test_revalida_propietario_tras_crear_directorio_antes_de_escribir_secretos(self):
        import importlib.util
        from unittest.mock import patch
        spec=importlib.util.spec_from_file_location('installer_race',ROOT/'install.py')
        installer=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(installer)
        original_mkdir=Path.mkdir
        actual_uid=os.geteuid()
        created=False
        def mkdir(path,*args,**kwargs):
            nonlocal created
            result=original_mkdir(path,*args,**kwargs)
            if path==self.target:created=True
            return result
        with patch.dict(os.environ,self.env,clear=True), patch.object(Path,'mkdir',mkdir), \
             patch.object(os,'geteuid',side_effect=lambda:actual_uid+int(created)):
            with self.assertRaises(ValueError):
                installer.install()
        self.assertFalse((self.target/'config.json').exists())
        self.assertFalse((self.target/'immich_tombstone.py').exists())


if __name__=='__main__':
    unittest.main()
