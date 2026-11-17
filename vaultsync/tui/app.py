"""The app: a header, the update bar, one tab per sub-app and a footer with the keys of the focused tab. Tabs run
their tasks independently, so several can run at the same time. Quitting while a tab is busy cancels the tasks and
waits for them, so files moved aside are restored first."""
import os
import sys

from textual.app import App
from textual.binding import Binding
from textual.widgets import Footer, Header, TabbedContent, TabPane

from vaultsync import APP_NAME, __version__, paths, updater
from vaultsync.common import load_json, open_folder, save_json
from vaultsync.tui.common import MessageScreen, TaskPane
from vaultsync.tui.gog import GogPane
from vaultsync.tui.podcasts import PodcastsPane
from vaultsync.tui.tts import TtsPane
from vaultsync.tui.update import UpdateBar

APP_SETTINGS_FILE = os.path.join(paths.HOME, 'app.json')
# (tab id, title, pane class); the F key in front, not in brackets: "[...]" in titles is Textual markup.
TABS = (('gog', 'F1 GOG Library', GogPane), ('podcasts', 'F2 Podcasts', PodcastsPane),
        ('tts', 'F3 Tabletop Simulator', TtsPane))


class VaultSyncApp(App):
    TITLE = APP_NAME
    SUB_TITLE = 'v' + __version__
    BINDINGS = [
        Binding('ctrl+q', 'quit', 'Quit', priority=True),
        Binding('ctrl+o', 'open_data_folder', 'Data folder'),
        Binding('ctrl+u', 'update_now', 'Update', priority=True),
        Binding('f1', 'show_tab("gog")', 'GOG', show=False),
        Binding('f2', 'show_tab("podcasts")', 'Podcasts', show=False),
        Binding('f3', 'show_tab("tts")', 'TTS', show=False),
    ]

    def __init__(self, notes=(), smoke_test=False):
        """notes: lines shown once at the start (e.g. data moved from an earlier version). smoke_test: exit
        right after the first screen is up (used to test builds)."""
        super().__init__()
        self.closing = False
        self.notes = list(notes)
        self.smoke_test = smoke_test
        self.settings = load_json(APP_SETTINGS_FILE, {})

    def compose(self):
        yield Header()
        yield UpdateBar(id='update-bar')
        initial = self.settings.get('tab') if self.settings.get('tab') in {t[0] for t in TABS} else 'gog'
        with TabbedContent(initial=initial, id='tabs'):
            for tab_id, title, pane_class in TABS:
                with TabPane(title, id=tab_id):
                    yield pane_class()
        yield Footer()

    def on_mount(self):
        # The first tab gets the focus from on_tabbed_content_tab_activated, which also runs for the initial tab.
        if self.smoke_test:
            self.set_timer(0.5, lambda: self.exit(result='ok'))
            return
        if self.notes:
            self.push_screen(MessageScreen('\n'.join(self.notes), 'Data folder'))
        updater.cleanup_previous()
        if updater.can_self_update():
            self.set_timer(1.5, self.main_screen.query_one(UpdateBar).start_check)

    @property
    def main_screen(self):
        # App.query() searches the screen on top, which is a dialog while one is open.
        return self.screen_stack[0]

    @property
    def panes(self):
        return list(self.main_screen.query(TaskPane))

    def check_action(self, action, parameters):
        if action == 'update_now':
            return self.main_screen.query_one(UpdateBar).offering or None
        if action == 'show_tab':
            return self.screen is self.main_screen  # not behind a dialog
        return True

    # --- tabs -------------------------------------------------------------------------------------

    def action_show_tab(self, tab):
        self.main_screen.query_one('#tabs', TabbedContent).active = tab

    def _focus_tab(self, tab_pane):
        # After the refresh: a widget can only take the focus once its tab is shown. Skipped if another tab was
        # chosen meanwhile, since focusing a widget in a hidden tab would switch back to that tab.
        def focus():
            if self.main_screen.query_one('#tabs', TabbedContent).active == tab_pane.id:
                for pane in tab_pane.query(TaskPane):
                    pane.focus_default()

        self.call_after_refresh(focus)

    def on_tabbed_content_tab_activated(self, event):
        if event.tabbed_content.id != 'tabs':
            return  # the results tabs inside a pane
        pane_id = event.pane.id
        self._focus_tab(event.pane)
        if self.settings.get('tab') != pane_id:
            self.settings['tab'] = pane_id
            try:
                save_json(APP_SETTINGS_FILE, self.settings)
            except OSError:
                pass

    # --- actions ----------------------------------------------------------------------------------

    def action_open_data_folder(self):
        open_folder(paths.home_dir())

    def action_update_now(self):
        bar = self.main_screen.query_one(UpdateBar)
        if bar.offering:
            bar.update_now()

    async def action_quit(self):
        busy = [pane for pane in self.panes if pane.busy]
        if not busy:
            self.exit()
            return

        def answered(yes):
            if yes:
                self.closing = True
                for pane in busy:
                    pane.cancel_task()

        self.push_screen(MessageScreen('A task is still running. Cancel it and quit?\n\nFiles that were moved aside '
                                       'are restored first.', question=True), answered)

    def task_finished(self):
        """Called by a pane when its task ended while the app is closing."""
        if not any(pane.busy for pane in self.panes):
            self.exit()


def main(smoke_test=False):
    """Runs the app in this terminal; returns the exit code. With smoke_test it only checks that the app starts."""
    notes = [] if smoke_test else paths.migrate_system_folders()
    error = paths.check_writable()
    if error:
        print(error, file=sys.stderr)
        return 1
    result = VaultSyncApp(notes, smoke_test).run(headless=smoke_test)
    if smoke_test:
        print('TUI ok' if result == 'ok' else 'TUI failed')
        return 0 if result == 'ok' else 1
    if isinstance(result, tuple) and result[0] == 'restart':
        return updater.restart_in_terminal(result[1]) or 0
    return 0
