"""Drives the console app with key presses, like a user would (Textual's run_test runs it without a terminal)."""
import asyncio
import http.server
import json
import os
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from textual.widgets import Checkbox, DataTable, Input, Label, TextArea, Tree

from tests.test_podcasts import Handler, feed_xml
from vaultsync import updater
from vaultsync.gog import engine as gog
from vaultsync.podcasts import engine as pc
from vaultsync.tts import engine as tts
from vaultsync.tui import app as tui_app
from vaultsync.tui.app import VaultSyncApp
from vaultsync.tui.common import MessageScreen, PathScreen, TaskPane
from vaultsync.tui.gog import GogPane, LoginScreen, PromptScreen
from vaultsync.tui.podcasts import PodcastScreen, PodcastsPane, RuleScreen
from vaultsync.tui.tts import TtsPane
from vaultsync.tui.update import UpdateBar


async def wait_for(pilot, condition, timeout=20):
    for _ in range(int(timeout / 0.05)):
        if condition():
            return
        await pilot.pause(0.05)
    app = pilot.app
    raise AssertionError('timed out (screen {}, tab {}, focused {})'.format(
        app.screen, app.main_screen.query_one('#tabs').active, app.focused))


def focus_in(app, pane_class):
    focused = app.focused
    return focused is not None and any(isinstance(node, pane_class) for node in focused.ancestors)


async def show_tab(pilot, key, pane_class):
    """Switches tabs with an F key and waits until the tab's main widget has the focus; returns the pane.
    Waits for the app to settle first (focus in the start tab), as a key pressed during start-up can be undone
    by the tabs' own initial activation."""
    await wait_for(pilot, lambda: focus_in(pilot.app, TaskPane))
    await pilot.press(key)
    await wait_for(pilot, lambda: focus_in(pilot.app, pane_class))
    return pilot.app.query_one(pane_class)


def label_text(pane, selector):
    return str(pane.query_one(selector, Label).render())


class UiTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # IsolatedAsyncioTestCase turns on asyncio's debug mode, which records a stack trace for every callback;
        # with Textual's timers that makes the tests crawl.
        asyncio.get_running_loop().set_debug(False)


class PodcastsTest(UiTest):
    def setUp(self):
        self.archive = tempfile.mkdtemp(prefix='vault-sync-archive-')
        Handler.feed = feed_xml([('Alpha - Teil 1', '/a1.mp3', 1, 'audio/mpeg'),
                                 ('Beta: Start', '/b1.mp3', 2, 'audio/mpeg')])
        self.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.feed = 'http://127.0.0.1:%d/feed.xml' % self.server.server_port
        pc.save_config(pc.default_config())

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        shutil.rmtree(self.archive, ignore_errors=True)
        pc.save_config(pc.default_config())

    async def test_add_check_and_download(self):
        app = VaultSyncApp()
        async with app.run_test(size=(140, 45)) as pilot:
            pane = await show_tab(pilot, 'f2', PodcastsPane)
            pane.query_one('#archive-dir', Input).value = self.archive
            await pilot.pause()
            self.assertEqual(pc.load_config()['archive_dir'], self.archive)

            pane.focus_default()
            await pilot.press('a')
            self.assertIsInstance(app.screen, PodcastScreen)
            app.screen.query_one('#name', Input).value = 'Test Cast'
            app.screen.query_one('#feed', Input).value = self.feed
            await pilot.press('ctrl+s')
            await pilot.pause()
            self.assertEqual([p['name'] for p in pc.load_config()['podcasts']], ['Test Cast'])
            self.assertEqual(pane.query_one('#watch', DataTable).row_count, 1)

            await pilot.press('c')
            await wait_for(pilot, lambda: pane.plan is not None)
            self.assertEqual(len(pane.plan.new), 2)
            self.assertIn('2 new episode(s)', label_text(pane, '#summary'))
            self.assertEqual(len(pane.query_one('#plan', Tree).root.children), 1)

            await pilot.press('d')
            self.assertIsInstance(app.screen, MessageScreen)
            await pilot.press('y')
            await wait_for(pilot, lambda: pane.plan is None and not pane.busy and isinstance(app.screen,
                                                                                               MessageScreen))
            await pilot.press('enter')
            folder = os.path.join(self.archive, 'Test Cast')
            files = [f for _, _, names in os.walk(folder) for f in names if f.endswith('.mp3')]
            self.assertEqual(len(files), 2)
            await pilot.press('l')  # RichLog renders its lines once its tab is shown
            await pilot.pause()
            self.assertTrue(pane.log_view.lines)

    async def test_folder_rules_are_edited_in_the_podcast_dialog(self):
        pc.save_config({'archive_dir': self.archive, 'podcasts': [pc.normalize_podcast(
            {'name': 'Show', 'feed': self.feed, 'sessions': [{'name': 'Alpha', 'match': ['^Alpha']}]})]})
        app = VaultSyncApp()
        async with app.run_test(size=(140, 45)) as pilot:
            await show_tab(pilot, 'f2', PodcastsPane)
            await pilot.press('e')
            screen = app.screen
            self.assertIsInstance(screen, PodcastScreen)
            screen.query_one('#rules', DataTable).focus()
            await pilot.press('a')
            self.assertIsInstance(app.screen, RuleScreen)
            app.screen.query_one('#name', Input).value = 'Beta'
            app.screen.query_one('#patterns', TextArea).text = '^Beta\n\n'
            await pilot.press('ctrl+s')
            await pilot.press('shift+up')  # the new rule first
            await pilot.press('ctrl+s')
            await pilot.pause()
            rules = pc.load_config()['podcasts'][0]['sessions']
            self.assertEqual(rules, [{'name': 'Beta', 'match': ['^Beta']}, {'name': 'Alpha', 'match': ['^Alpha']}])

    async def test_check_without_archive_folder_explains(self):
        app = VaultSyncApp()
        async with app.run_test() as pilot:
            await show_tab(pilot, 'f2', PodcastsPane)
            await pilot.press('c')
            self.assertIsInstance(app.screen, MessageScreen)
            await pilot.press('escape')
            self.assertNotIsInstance(app.screen, MessageScreen)

    async def test_import_asks_for_the_file(self):
        path = os.path.join(self.archive, 'podcasts.json')
        with open(path, 'w', encoding='utf-8') as f:
            json.dump({'base_dir': self.archive, 'podcasts': [{'name': 'Old Show', 'feed': self.feed}]}, f)
        app = VaultSyncApp()
        async with app.run_test(size=(140, 45)) as pilot:
            await show_tab(pilot, 'f2', PodcastsPane)
            await pilot.press('i')
            self.assertIsInstance(app.screen, PathScreen)
            app.screen.query_one('#path', Input).value = path
            await pilot.press('enter')
            await pilot.pause()
            self.assertEqual([p['name'] for p in pc.load_config()['podcasts']], ['Old Show'])


class GogTest(UiTest):
    def tearDown(self):
        if os.path.exists(gog.SETTINGS_FILE):
            os.remove(gog.SETTINGS_FILE)

    async def test_check_with_a_question_then_apply(self):
        plan = gog.Plan(downloads=[gog.Item('Game A', 'setup.exe', 3000), gog.Item('Game A', 'manual.pdf', 100)],
                        deletions=[gog.Item('Old Game', '(whole folder)', 500)])
        applied = []
        app = VaultSyncApp()
        with mock.patch.object(gog, 'is_logged_in', return_value=True):
            async with app.run_test(size=(140, 45)) as pilot:
                pane = await show_tab(pilot, 'f1', GogPane)
                self.assertIn('Logged in', label_text(pane, '#account'))
                # The engine asks a question on the way, like gogrepoc's two-step code prompt.
                pane.engine.check = lambda settings: plan if pane.engine.ask(
                    'Enter code: ', 'Open https://login.gog.com/x') == '123456' else gog.Plan()
                pane.engine.apply = lambda settings, p: applied.append((settings['save_dir'], p))
                pane.query_one('#save_dir', Input).value = os.path.join(tempfile.gettempdir(), 'GOG')
                await pilot.pause()
                self.assertEqual(gog.load_settings()['save_dir'], os.path.join(tempfile.gettempdir(), 'GOG'))

                await pilot.press('c')
                await wait_for(pilot, lambda: isinstance(app.screen, PromptScreen))
                self.assertEqual(app.screen.url, 'https://login.gog.com/x')
                await pilot.press(*'123456', 'enter')
                await wait_for(pilot, lambda: pane.plan is not None)
                self.assertIn('2 file(s) to download', label_text(pane, '#summary'))
                self.assertEqual(len(pane.query_one('#plan', Tree).root.children), 2)

                await pilot.press('d', 'y')
                await wait_for(pilot, lambda: applied and not pane.busy)
                self.assertIs(applied[0][1], plan)
                self.assertIsNone(pane.plan)

    async def test_login_and_option_changes(self):
        app = VaultSyncApp()
        with mock.patch.object(gog, 'is_logged_in', return_value=False):
            async with app.run_test(size=(140, 45)) as pilot:
                pane = await show_tab(pilot, 'f1', GogPane)
                logins = []
                pane.engine.login = lambda user, password: logins.append((user, password))
                pane.query_one('#verify', Checkbox).value = True
                await pilot.pause()
                self.assertTrue(gog.load_settings()['verify'])
                await pilot.press('c')
                self.assertIsInstance(app.screen, MessageScreen)  # not logged in yet
                await pilot.press('enter')
                await pilot.press('i')
                self.assertIsInstance(app.screen, LoginScreen)
                await pilot.press(*'me', 'enter', *'secret', 'enter')
                await wait_for(pilot, lambda: logins and not pane.busy)
                self.assertEqual(logins, [('me', 'secret')])


class TtsTest(UiTest):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='vault-sync-tts-'))
        self.mods = self.tmp / 'Mods'
        (self.mods / 'Workshop' / 'Folder').mkdir(parents=True)
        (self.mods / 'Workshop' / '1.json').write_text(json.dumps({'SaveName': 'Chess'}))
        (self.mods / 'Workshop' / 'Folder' / '2.json').write_text(json.dumps({'SaveName': 'Go'}))
        cfg = tts.default_config()
        cfg.update(mods_dir=str(self.mods), backup_dir=str(self.tmp / 'backups'))
        tts.save_config(cfg)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        tts.save_config(tts.default_config())

    async def test_list_toggle_and_back_up(self):
        app = VaultSyncApp()
        async with app.run_test(size=(140, 45)) as pilot:
            pane = await show_tab(pilot, 'f3', TtsPane)
            table = pane.query_one('#mods', DataTable)
            await wait_for(pilot, lambda: table.row_count == 2)
            self.assertIn('2 mods, 2 included', label_text(pane, '#summary'))

            await pilot.press('space')  # leave out the first mod
            self.assertEqual(len(tts.load_config()['skip']), 1)
            self.assertIn('1 included', label_text(pane, '#summary'))

            await pilot.press('n')
            self.assertIsNone(pane.check_action('run', ()))  # nothing to back up
            await pilot.press('a')

            await pilot.press('d', 'y')
            await wait_for(pilot, lambda: pane.results and not pane.busy)
            await pilot.press('enter')  # the summary message
            zips = sorted(p.name for p in (self.tmp / 'backups').rglob('*.zip'))
            self.assertEqual([name.split(' ')[0] for name in zips], ['Chess', 'Go'])
            self.assertTrue(str(table.get_row_at(0)[4]).startswith('Backed up'))

    async def test_restore_asks_for_the_folder(self):
        app = VaultSyncApp()
        async with app.run_test(size=(140, 45)) as pilot:
            pane = await show_tab(pilot, 'f3', TtsPane)
            restored = []
            pane.engine.restore = lambda cfg, folder: restored.append(folder) or ([], 0, [])
            await pilot.press('r')
            self.assertIsInstance(app.screen, PathScreen)
            app.screen.query_one('#path', Input).value = str(self.tmp)
            await pilot.press('enter', 'y')
            await wait_for(pilot, lambda: restored and not pane.busy)
            self.assertEqual(restored, [str(self.tmp)])


class AppTest(UiTest):
    async def test_quit_while_busy_cancels_first(self):
        app = VaultSyncApp()
        async with app.run_test() as pilot:
            pane = app.query_one(PodcastsPane)
            release = threading.Event()
            pane.cancel_task = release.set
            pane.run_task(lambda: release.wait(10), on_done=lambda _: None)
            await pilot.press('ctrl+q')
            self.assertIsInstance(app.screen, MessageScreen)
            await pilot.press('y')
            await wait_for(pilot, lambda: not app.is_running)
        self.assertTrue(release.is_set())

    async def test_selected_tab_is_remembered(self):
        async with VaultSyncApp().run_test() as pilot:
            await show_tab(pilot, 'f3', TtsPane)
        async with VaultSyncApp().run_test() as pilot:
            self.assertEqual(pilot.app.query_one('#tabs').active, 'tts')
            await show_tab(pilot, 'f1', GogPane)

    async def test_notes_are_shown_once_at_the_start(self):
        app = VaultSyncApp(notes=['Moved 3 file(s) of an earlier version from A to B.'])
        async with app.run_test():
            self.assertIsInstance(app.screen, MessageScreen)

    async def test_update_bar_offers_and_installs(self):
        release = updater.Release('9.0.0', '', 'https://example/release', 'vault-sync-linux-x64-9.0.0.tar.gz',
                                  'https://example/x', 10)
        app = VaultSyncApp()
        with mock.patch.object(updater, 'check', return_value=release), \
                mock.patch.object(updater, 'install', return_value='/opt/vault-sync'):
            async with app.run_test(size=(140, 45)) as pilot:
                bar = app.query_one(UpdateBar)
                bar.start_check()
                await wait_for(pilot, lambda: bar.offering)
                self.assertIn('Update available', app.sub_title)
                await pilot.press('ctrl+u')
                await wait_for(pilot, lambda: not app.is_running)
        self.assertEqual(app.return_value, ('restart', '/opt/vault-sync'))

    def test_smoke_test_mode(self):
        with mock.patch('builtins.print') as printed:
            self.assertEqual(tui_app.main(smoke_test=True), 0)
        printed.assert_called_with('TUI ok')


if __name__ == '__main__':
    unittest.main()
