import json
import shutil
import tempfile
import threading
import unittest
import zipfile
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from vaultsync.common import Cancelled
from vaultsync.tts import engine as tts

PNG = b'\x89PNG\r\n\x1a\n' + b'\0' * 64
ARKHAM_SCRIPT = ('MOD_VERSION = "4.9.3"\n'
                 'SOURCE_REPO = "https://github.com/Chr1Z93/SCED-downloads/releases/latest/download/"\n'
                 "WebRequest.get(SOURCE_REPO .. 'library.json', cb)\n")


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


class EngineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='tts-engine-'))
        www = self.tmp / 'www'
        www.mkdir()
        (www / 'board.png').write_bytes(PNG)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), partial(_QuietHandler, directory=str(www)))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base_url = 'http://127.0.0.1:%d' % self.server.server_port

        self.mods = self.tmp / 'TTS' / 'Mods'
        workshop = self.mods / 'Workshop'
        (workshop / 'Campaign Games').mkdir(parents=True)
        (workshop / '123.json').write_text(json.dumps({
            'SaveName': 'Board Game', 'TableURL': self.base_url + '/board.png',
            'ObjectStates': [{'CustomImage': {'ImageURL': self.base_url + '/gone.png'}}]}))
        self.arkham = {'SaveName': 'Arkham SCE - 4.9.3', 'LuaScript': ARKHAM_SCRIPT}
        (workshop / 'Campaign Games' / 'Arkham SCE 4.9.3.json').write_text(json.dumps(self.arkham))

        self.cfg = tts.default_config()
        self.cfg.update(mods_dir=str(self.mods), backup_dir=str(self.tmp / 'backups'))
        self.lines = []
        self.engine = tts.Engine(log=self.lines.append, status=lambda text: None, progress=lambda value: None)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def entries(self):
        return {e.name: e for e in tts.list_mods(self.cfg)}

    def test_list_mods_includes_subfolders(self):
        entries = self.entries()
        self.assertEqual(entries['Board Game'].key, '123.json')
        self.assertEqual(entries['Arkham SCE - 4.9.3'].folder, 'Campaign Games')

    def test_check_counts_without_downloading(self):
        checks = self.engine.check(self.cfg, [self.entries()['Board Game']])
        self.assertEqual((checks[0].assets, checks[0].cached, checks[0].to_download), (2, 0, 2))
        self.assertFalse((self.mods / 'Images').exists())

    def test_backup_and_restore(self):
        entry = self.entries()['Board Game']
        result, = self.engine.backup(self.cfg, [entry])
        self.assertEqual((result.assets, result.missing, result.error), (2, 1, ''))
        zip_path = self.tmp / 'backups' / 'Board Game (123).zip'
        self.assertEqual(result.zips, [zip_path])
        with zipfile.ZipFile(zip_path) as z:
            self.assertIn('Workshop/123.json', z.namelist())
            self.assertEqual(len(json.loads(z.read('manifest.json'))['missing']), 1)

        target = dict(self.cfg, mods_dir=str(self.tmp / 'Other' / 'Mods'))
        restored, written, problems = self.engine.restore(target, self.tmp / 'backups')
        self.assertEqual((restored, written, problems), (['Board Game'], 2, []))
        self.assertTrue((self.tmp / 'Other' / 'Mods' / 'Workshop' / '123.json').is_file())

    def test_arkham_packs_that_cannot_be_fetched_are_a_warning(self):
        entry = self.entries()['Arkham SCE - 4.9.3']
        earlier = tts.arkham.content_folder(self.mods, self.arkham) / 'Campaigns' / 'Old Pack.json'
        earlier.parent.mkdir(parents=True)
        earlier.write_text(json.dumps({'ObjectStates': [{'Name': 'Bag'}]}))
        with mock.patch.object(tts.arkham, 'fetch_packs', side_effect=OSError('GitHub unreachable')):
            result, = self.engine.backup(self.cfg, [entry])
        self.assertEqual(result.error, '')
        self.assertIn('GitHub unreachable', result.warnings[0])
        extras = self.tmp / 'backups' / 'Campaign Games' / 'Arkham SCE - 4.9.3 - Downloadable content.zip'
        self.assertEqual(result.zips[-1], extras)
        with zipfile.ZipFile(extras) as z:
            self.assertIn('Saves/Saved Objects/Arkham SCE Downloads/4.9.3/Campaigns/Old Pack.json', z.namelist())

    def test_extras_can_be_turned_off(self):
        entry = self.entries()['Arkham SCE - 4.9.3']
        with mock.patch.object(tts.arkham, 'fetch_packs') as fetch:
            result, = self.engine.backup(dict(self.cfg, extras=False), [entry])
        fetch.assert_not_called()
        self.assertEqual(len(result.zips), 1)

    def test_cancel_stops_and_leaves_no_partial_zip(self):
        entry = self.entries()['Board Game']

        def cancel_while_packing(*args, **kwargs):
            self.engine.cancel()
            return original(*args, **kwargs)

        original = tts.build_package
        with mock.patch.object(tts, 'build_package', side_effect=cancel_while_packing):
            with self.assertRaises(Cancelled):
                self.engine.backup(self.cfg, [entry])
        backups = self.tmp / 'backups'
        self.assertEqual([p.name for p in backups.rglob('*')] if backups.exists() else [], [])


if __name__ == '__main__':
    unittest.main()
