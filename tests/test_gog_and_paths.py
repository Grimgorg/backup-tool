import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

from vaultsync import SOURCE_ROOT, paths
from vaultsync.gog import engine as gog


def write(path, text='x'):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(text)


def read(path):
    with open(path, encoding='utf-8') as f:
        return f.read()


class PathsTest(unittest.TestCase):
    def test_override_puts_everything_in_one_folder(self):
        home = os.path.abspath(os.environ['VAULT_SYNC_HOME'])
        self.assertEqual(paths.HOME, home)
        self.assertEqual(paths.LOG_DIR, os.path.join(home, 'logs'))
        for path in (gog.WORK_DIR, gog.SETTINGS_FILE):
            self.assertTrue(path.startswith(home))

    def test_data_folder_sits_next_to_the_program(self):
        with mock.patch.dict(os.environ, {'VAULT_SYNC_HOME': ''}):
            self.assertEqual(paths._home(), os.path.join(SOURCE_ROOT, 'vault-sync-data'))
            exe = os.path.join(tempfile.gettempdir(), 'tools', 'vault-sync.exe')
            with mock.patch.object(sys, 'frozen', True, create=True), mock.patch.object(sys, 'executable', exe):
                self.assertEqual(paths._home(), os.path.join(os.path.dirname(exe), 'vault-sync-data'))

    def test_system_dirs_of_earlier_versions(self):
        with mock.patch.dict(os.environ, {'XDG_CONFIG_HOME': '', 'XDG_DATA_HOME': '', 'XDG_STATE_HOME': '',
                                          'APPDATA': r'C:\R', 'LOCALAPPDATA': r'C:\L'}):
            config, data, logs = paths.system_dirs('linux')
            self.assertTrue(config.endswith(os.path.join('.config', 'vault-sync')))
            self.assertTrue(logs.endswith(os.path.join('.local', 'state', 'vault-sync', 'logs')))
            config, data, logs = paths.system_dirs('darwin')
            self.assertEqual(config, data)
            self.assertEqual(paths.system_dirs('win32'), (os.path.join(r'C:\R', 'Vault Sync'),
                                                          os.path.join(r'C:\L', 'Vault Sync'),
                                                          os.path.join(r'C:\L', 'Vault Sync', 'Logs')))

    def test_writable_check(self):
        self.assertIsNone(paths.check_writable())


class MigrateTest(unittest.TestCase):
    """Moving the data of versions before 2.0 (per-OS folders) into the data folder next to the program."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='vault-sync-migrate-')
        self.home = os.path.join(self.tmp, 'vault-sync-data')
        self.patches = [mock.patch.object(paths, 'HOME', self.home),
                        mock.patch.object(paths, 'LOG_DIR', os.path.join(self.home, 'logs'))]
        for patch in self.patches:
            patch.start()

    def tearDown(self):
        for patch in self.patches:
            patch.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_windows_layout(self):
        roaming = os.path.join(self.tmp, 'Roaming', 'Vault Sync')
        local = os.path.join(self.tmp, 'Local', 'Vault Sync')
        write(os.path.join(roaming, 'gog', 'settings.json'), '{"save_dir": "X:/GOG"}')
        write(os.path.join(roaming, 'podcasts', 'podcasts.json'), '{}')
        write(os.path.join(roaming, 'app.json'), '{}')
        write(os.path.join(local, 'gog', 'gog-token.dat'), 'token')
        write(os.path.join(local, 'podcasts', 'Show', 'state.json'), '{}')
        write(os.path.join(local, 'Logs', 'gog-check.log'), 'log')

        lines = paths.migrate_system_folders((roaming, local, os.path.join(local, 'Logs')))

        self.assertEqual(read(os.path.join(self.home, 'gog', 'settings.json')), '{"save_dir": "X:/GOG"}')
        self.assertEqual(read(os.path.join(self.home, 'gog', 'gog-token.dat')), 'token')
        for rel in (('podcasts', 'podcasts.json'), ('podcasts', 'Show', 'state.json'), ('app.json',),
                    ('logs', 'gog-check.log')):
            self.assertTrue(os.path.isfile(os.path.join(self.home, *rel)), rel)
        self.assertNotIn('Logs', os.listdir(self.home))  # case-insensitive on Windows, so compare names
        self.assertFalse(os.path.exists(roaming))
        self.assertFalse(os.path.exists(local))
        self.assertEqual(len(lines), 3)

    def test_macos_layout_with_one_folder_for_settings_and_data(self):
        support = os.path.join(self.tmp, 'Application Support', 'Vault Sync')
        logs = os.path.join(self.tmp, 'Logs', 'Vault Sync')
        write(os.path.join(support, 'gog', 'settings.json'))
        write(os.path.join(support, 'gog', 'gog-manifest.dat'))
        write(os.path.join(logs, 'tts-backup.log'))
        paths.migrate_system_folders((support, support, logs))
        self.assertTrue(os.path.isfile(os.path.join(self.home, 'gog', 'gog-manifest.dat')))
        self.assertTrue(os.path.isfile(os.path.join(self.home, 'logs', 'tts-backup.log')))
        self.assertFalse(os.path.exists(support))

    def test_existing_files_are_never_replaced(self):
        old = os.path.join(self.tmp, 'old')
        write(os.path.join(old, 'tts', 'tts.json'), 'old')
        write(os.path.join(old, 'tts', 'other.json'), 'moved')
        write(os.path.join(self.home, 'tts', 'tts.json'), 'new')
        lines = paths.migrate_system_folders((old, os.path.join(self.tmp, 'none'), os.path.join(self.tmp, 'none')))
        self.assertEqual(read(os.path.join(self.home, 'tts', 'tts.json')), 'new')
        self.assertEqual(read(os.path.join(self.home, 'tts', 'other.json')), 'moved')
        self.assertEqual(read(os.path.join(old, 'tts', 'tts.json')), 'old')  # kept where it was
        self.assertTrue(any('Left 1 file(s)' in line for line in lines))

    def test_runs_only_once_and_not_with_an_override(self):
        old = os.path.join(self.tmp, 'old')
        write(os.path.join(old, 'app.json'))
        with mock.patch.object(paths, 'system_dirs', return_value=(old, old, os.path.join(self.tmp, 'no-logs'))):
            self.assertEqual(paths.migrate_system_folders(), [])  # VAULT_SYNC_HOME is set in tests
            with mock.patch.dict(os.environ, {'VAULT_SYNC_HOME': ''}):
                os.makedirs(self.home)
                self.assertEqual(paths.migrate_system_folders(), [])  # the data folder exists already
                os.rmdir(self.home)
                self.assertEqual(len(paths.migrate_system_folders()), 1)
        self.assertTrue(os.path.isfile(os.path.join(self.home, 'app.json')))


class GogTest(unittest.TestCase):
    def test_orphan_lines_on_every_os(self):
        for sep in ('\\', '/'):
            m = gog.ORPHAN_FILE_LINE.match("orphaning file 'Some Game{}setup.exe' as it has been marked for change."
                                           .format(sep))
            self.assertEqual(m.group(1, 2), ('Some Game', 'setup.exe'))

    def test_settings_defaults_fill_missing_keys(self):
        try:
            write(gog.SETTINGS_FILE, '{"save_dir": "X:/GOG", "langs": ["de"]}')
            settings = gog.load_settings()
            self.assertEqual((settings['save_dir'], settings['langs'], settings['os']), ('X:/GOG', ['de'], ['windows']))
        finally:
            os.remove(gog.SETTINGS_FILE)


if __name__ == '__main__':
    unittest.main()
