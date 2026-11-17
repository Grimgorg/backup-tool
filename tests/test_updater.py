import io
import os
import shutil
import sys
import tarfile
import tempfile
import threading
import unittest
import zipfile
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

from vaultsync import updater
from vaultsync.common import Cancelled, SyncError


def github_answer(tag, names, **extra):
    return dict({'tag_name': tag, 'html_url': 'https://example/release', 'body': 'Notes',
                 'assets': [{'name': n, 'browser_download_url': 'https://example/' + n, 'size': 10} for n in names]},
                **extra)


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


class VersionTest(unittest.TestCase):
    def test_parse_version(self):
        self.assertEqual(updater.parse_version('v1.2.3'), (1, 2, 3))
        self.assertEqual(updater.parse_version('1.0.10'), (1, 0, 10))
        for text in ('1.0.1-dev+abc1234', '1.0-dev', '', None, 'v1.2'):
            self.assertIsNone(updater.parse_version(text))

    def test_asset_prefix_per_os(self):
        self.assertEqual(updater.asset_prefix('win32', 'AMD64'), 'vault-sync-windows-x64-')
        self.assertEqual(updater.asset_prefix('darwin', 'arm64'), 'vault-sync-macos-arm64-')
        self.assertEqual(updater.asset_prefix('linux', 'x86_64'), 'vault-sync-linux-x64-')
        self.assertIsNone(updater.asset_prefix('darwin', 'x86_64'))  # no Intel Mac build

    def test_window_app_releases_are_not_offered(self):
        # Releases before 2.0 were window apps named VaultSync-...; the console build never installs those.
        answer = github_answer('v2.0.1', ['VaultSync-linux-x64-2.0.1.tar.gz'])
        self.assertIsNone(updater.pick_release(answer, '2.0.0', updater.asset_prefix('linux', 'x86_64')))

    def test_pick_release_only_newer_with_a_build_for_this_os(self):
        names = ['vault-sync-linux-x64-1.0.3.tar.gz', 'vault-sync-windows-x64-1.0.3.zip']
        prefix = 'vault-sync-linux-x64-'
        release = updater.pick_release(github_answer('v1.0.3', names), '1.0.2', prefix)
        self.assertEqual((release.version, release.asset_name), ('1.0.3', 'vault-sync-linux-x64-1.0.3.tar.gz'))
        self.assertEqual(updater.pick_release(github_answer('v1.1.0', names[1:]), '1.0.2', prefix), None)
        for tag, current in (('v1.0.3', '1.0.3'), ('v1.0.3', '1.0.10'), ('v1.0.3', '1.0.4-dev+abc')):
            self.assertIsNone(updater.pick_release(github_answer(tag, names), current, prefix))
        self.assertIsNone(updater.pick_release(github_answer('v2.0.0', names, prerelease=True), '1.0.0', prefix))

    def test_source_and_dev_builds_never_update(self):
        with mock.patch.object(updater, '__version__', '1.0.0'):
            self.assertFalse(updater.can_self_update())  # not frozen
        with mock.patch.object(sys, 'frozen', True, create=True), \
                mock.patch.object(updater, '__version__', '1.0.1-dev+abc1234'):
            self.assertFalse(updater.can_self_update())


class InstallTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='vault-sync-update-')
        self.www = os.path.join(self.tmp, 'www')
        os.mkdir(self.www)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), partial(_QuietHandler, directory=self.www))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.app_dir = os.path.join(self.tmp, 'apps')
        os.mkdir(self.app_dir)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def release(self, name):
        url = 'http://127.0.0.1:{}/{}'.format(self.server.server_port, name)
        return updater.Release('1.0.3', '', 'https://example/release', name, url,
                               os.path.getsize(os.path.join(self.www, name)))

    def add_tar(self, name, member, data):
        with tarfile.open(os.path.join(self.www, name), 'w:gz') as tar:
            info = tarfile.TarInfo(member)
            info.size, info.mode = len(data), 0o755
            tar.addfile(info, io.BytesIO(data))

    def test_linux_build_replaces_itself_and_cleans_up_next_start(self):
        target = os.path.join(self.app_dir, 'vault-sync')
        with open(target, 'wb') as f:
            f.write(b'old build')
        self.add_tar('vault-sync-linux-x64-1.0.3.tar.gz', 'vault-sync', b'new build')
        with mock.patch.object(sys, 'executable', target), mock.patch.object(sys, 'platform', 'linux'), \
                mock.patch.object(sys, 'frozen', True, create=True):
            self.assertEqual(updater.install(self.release('vault-sync-linux-x64-1.0.3.tar.gz')), target)
            with open(target, 'rb') as f:
                self.assertEqual(f.read(), b'new build')
            self.assertTrue(os.access(target, os.X_OK))
            self.assertEqual(sorted(os.listdir(self.app_dir)), ['vault-sync', 'vault-sync.old'])
            updater.cleanup_previous()
            self.assertEqual(os.listdir(self.app_dir), ['vault-sync'])

    def test_windows_zip_build(self):
        target = os.path.join(self.app_dir, 'vault-sync.exe')
        with open(target, 'wb') as f:
            f.write(b'old exe')
        with zipfile.ZipFile(os.path.join(self.www, 'vault-sync-windows-x64-1.0.3.zip'), 'w') as z:
            z.writestr('vault-sync.exe', b'new exe')
        with mock.patch.object(sys, 'executable', target), mock.patch.object(sys, 'platform', 'win32'):
            updater.install(self.release('vault-sync-windows-x64-1.0.3.zip'))
        with open(target, 'rb') as f:
            self.assertEqual(f.read(), b'new exe')

    def test_cancel_leaves_the_build_untouched(self):
        target = os.path.join(self.app_dir, 'vault-sync')
        with open(target, 'wb') as f:
            f.write(b'old build')
        self.add_tar('vault-sync-linux-x64-1.0.3.tar.gz', 'vault-sync', b'x' * 2000000)
        with mock.patch.object(sys, 'executable', target), mock.patch.object(sys, 'platform', 'linux'):
            with self.assertRaises(Cancelled):
                updater.install(self.release('vault-sync-linux-x64-1.0.3.tar.gz'), cancelled=lambda: True)
        self.assertEqual(os.listdir(self.app_dir), ['vault-sync'])
        with open(target, 'rb') as f:
            self.assertEqual(f.read(), b'old build')

    def test_unsafe_zip_is_refused(self):
        target = os.path.join(self.app_dir, 'vault-sync.exe')
        with open(target, 'wb') as f:
            f.write(b'old exe')
        with zipfile.ZipFile(os.path.join(self.www, 'bad.zip'), 'w') as z:
            z.writestr('../evil.exe', b'x')
        with mock.patch.object(sys, 'executable', target), mock.patch.object(sys, 'platform', 'win32'):
            with self.assertRaises(SyncError):
                updater.install(self.release('bad.zip'))
        self.assertFalse(os.path.exists(os.path.join(self.app_dir, 'evil.exe')))
        with open(target, 'rb') as f:
            self.assertEqual(f.read(), b'old exe')


class RestartTest(unittest.TestCase):
    def test_posix_replaces_the_process(self):
        with mock.patch.object(sys, 'platform', 'linux'), mock.patch.object(updater.os, 'execv') as execv:
            updater.restart_in_terminal('/opt/vault-sync')
        execv.assert_called_once_with('/opt/vault-sync', ['/opt/vault-sync'])

    def test_windows_waits_for_the_new_build(self):
        target = os.path.join('Tools', 'vault-sync.exe')
        with mock.patch.object(sys, 'platform', 'win32'), \
                mock.patch.object(updater.subprocess, 'call', return_value=3) as call:
            self.assertEqual(updater.restart_in_terminal(target), 3)
        call.assert_called_once_with([target])


if __name__ == '__main__':
    unittest.main()
