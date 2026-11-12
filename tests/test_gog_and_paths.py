import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

from vaultsync import paths
from vaultsync.gog import engine as gog


class PathsTest(unittest.TestCase):
    def test_override_puts_everything_in_one_folder(self):
        home = os.environ['VAULT_SYNC_HOME']
        for folder in (paths.CONFIG_DIR, paths.DATA_DIR, paths.LOG_DIR):
            self.assertTrue(folder.startswith(os.path.abspath(home)))

    def test_platform_defaults(self):
        with mock.patch.dict(os.environ, {'VAULT_SYNC_HOME': '', 'XDG_CONFIG_HOME': '', 'XDG_DATA_HOME': '',
                                          'XDG_STATE_HOME': ''}):
            with mock.patch.object(sys, 'platform', 'linux'):
                config, data, logs = paths._dirs()
                self.assertTrue(config.endswith(os.path.join('.config', 'vault-sync')))
                self.assertTrue(data.endswith(os.path.join('.local', 'share', 'vault-sync')))
                self.assertTrue(logs.endswith(os.path.join('.local', 'state', 'vault-sync', 'logs')))
            with mock.patch.object(sys, 'platform', 'darwin'):
                config, data, logs = paths._dirs()
                self.assertTrue(config.endswith(os.path.join('Library', 'Application Support', 'Vault Sync')))
                self.assertTrue(logs.endswith(os.path.join('Library', 'Logs', 'Vault Sync')))
            with mock.patch.object(sys, 'platform', 'win32'), \
                    mock.patch.dict(os.environ, {'APPDATA': r'C:\R', 'LOCALAPPDATA': r'C:\L'}):
                config, data, logs = paths._dirs()
                self.assertEqual(config, os.path.join(r'C:\R', 'Vault Sync'))
                self.assertEqual(data, os.path.join(r'C:\L', 'Vault Sync'))


class GogTest(unittest.TestCase):
    def test_orphan_lines_on_every_os(self):
        for sep in ('\\', '/'):
            m = gog.ORPHAN_FILE_LINE.match("orphaning file 'Some Game{}setup.exe' as it has been marked for change."
                                           .format(sep))
            self.assertEqual(m.group(1, 2), ('Some Game', 'setup.exe'))

    def test_migrate_legacy_data_copies_once(self):
        legacy = tempfile.mkdtemp(prefix='vault-sync-legacy-')
        for name, text in (('gog-token.dat', 'token'), ('gog-manifest.dat', '[]'),
                           ('settings.json', '{"save_dir": "X:/GOG", "langs": ["de"]}')):
            with open(os.path.join(legacy, name), 'w', encoding='utf-8') as f:
                f.write(text)
        try:
            with mock.patch.object(gog, 'LEGACY_DIR', legacy):
                self.assertEqual(gog.migrate_legacy_data(), ['gog-token.dat', 'gog-manifest.dat', 'settings.json'])
                self.assertTrue(gog.is_logged_in())
                self.assertEqual(gog.load_settings()['save_dir'], 'X:/GOG')
                self.assertEqual(gog.load_settings()['os'], ['windows'])  # defaults fill missing keys
                self.assertEqual(gog.migrate_legacy_data(), [])
        finally:
            shutil.rmtree(legacy)
            shutil.rmtree(gog.WORK_DIR, ignore_errors=True)
            os.remove(gog.SETTINGS_FILE)


if __name__ == '__main__':
    unittest.main()
