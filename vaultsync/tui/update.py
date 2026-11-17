"""The update bar: shows a new release at the top of the app and installs it there.

States, from hidden to restart:
  hidden        no update (or "Later")
  offering      "Vault Sync X is available (you have Y). Updating restarts the app."  [Update now] [What's new] [Later]
  waiting       "Waiting for the running task to stop..."
  downloading   "Downloading Vault Sync X... 42%" with a progress bar  [Cancel]
  failed        "Couldn't install Vault Sync X: ... This version keeps working."  [Try again] [Close]
When the new build is in place, the app exits with ('restart', target) and main() starts it in the same terminal.
The result of the check is shown in the header (app.sub_title).
"""
import threading
import webbrowser

from rich.text import Text
from textual import work
from textual.containers import Horizontal
from textual.widgets import Button, Label, ProgressBar

from vaultsync import APP_NAME, __version__, updater
from vaultsync.common import Cancelled
from vaultsync.tui.common import MessageScreen


class UpdateBar(Horizontal):
    DEFAULT_CSS = '''
    UpdateBar { display: none; height: auto; padding: 0 1; margin: 1 1 0 1; border: round $accent; }
    UpdateBar.shown { display: block; }
    UpdateBar #update-text { width: 1fr; padding: 1 0 0 0; }
    UpdateBar ProgressBar { width: 30; padding: 1 1 0 0; }
    UpdateBar #update-buttons { width: auto; height: auto; }
    UpdateBar Button { margin-left: 1; }
    '''

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.release = None
        self.offering = False
        self._stop = threading.Event()
        self._state = {}
        self._timer = None

    def compose(self):
        yield Label('', id='update-text')
        yield ProgressBar(total=1.0, show_eta=False, show_percentage=False, id='update-progress')
        yield Horizontal(id='update-buttons')

    def _set_header(self, text):
        self.app.sub_title = 'v{} · {}'.format(__version__, text)

    # --- check ------------------------------------------------------------------------------------

    def start_check(self):
        self._set_header('Checking for updates...')
        self._check()

    @work(thread=True, group='update-check')
    def _check(self):
        try:
            release, error = updater.check(), None
        except Exception as e:  # offline or GitHub unreachable: the app works without updating
            release, error = None, e
        self.app.call_from_thread(self._on_checked, release, error)

    def _on_checked(self, release, error):
        if error:
            self._set_header("Couldn't check for updates")
            return
        if not release:
            self._set_header('Up to date')
            return
        self.release = release
        self._set_header('Update available')
        self._offer()

    # --- states -----------------------------------------------------------------------------------

    def _show(self, text, buttons, progress=False):
        """buttons: [(label, name)], the first is the main one."""
        self.query_one('#update-text', Label).update(Text(text))
        self.query_one('#update-progress', ProgressBar).display = progress
        box = self.query_one('#update-buttons', Horizontal)
        box.remove_children()
        # Named, not given ids: the old buttons may still be in the DOM while the new ones are mounted.
        box.mount_all([Button(label, name=name, variant='primary' if i == 0 and len(buttons) > 1 else 'default')
                       for i, (label, name) in enumerate(buttons)])
        self.offering = bool(buttons) and buttons[0][1] == 'update-now'
        self.add_class('shown')
        self.app.refresh_bindings()

    def _hide(self):
        self.offering = False
        self.remove_class('shown')
        self.app.refresh_bindings()

    def _offer(self):
        self._show('{} {} is available (you have {}). Updating restarts the app (Ctrl+U).'.format(
            APP_NAME, self.release.version, __version__),
            [('Update now', 'update-now'), ("What's new", 'update-notes'), ('Later', 'update-later')])

    def on_button_pressed(self, event):
        actions = {'update-now': self.update_now, 'update-notes': lambda: webbrowser.open(self.release.page),
                   'update-later': self._hide, 'update-cancel': self._cancel, 'update-retry': self._install_when_idle,
                   'update-close': self._hide}
        action = actions.get(event.button.name)
        if action:
            event.stop()
            action()

    def update_now(self):
        busy = [pane for pane in self.app.panes if pane.busy]
        if not busy:
            self._install_when_idle()
            return

        def answered(yes):
            if yes:
                for pane in busy:
                    pane.cancel_task()
                self._show('Waiting for the running task to stop...', [])
                self._install_when_idle()

        self.app.push_screen(MessageScreen('A task is still running. Cancel it and update?\n\nFiles that were moved '
                                           'aside are restored first.', question=True), answered)

    def _install_when_idle(self):
        if any(pane.busy for pane in self.app.panes):
            self.set_timer(0.3, self._install_when_idle)
            return
        self._stop.clear()
        self._state = {'done': 0, 'total': self.release.size, 'finished': False, 'target': None, 'error': None}
        self._show('Downloading {} {}...'.format(APP_NAME, self.release.version), [('Cancel', 'update-cancel')],
                   progress=True)
        self.query_one('#update-progress', ProgressBar).update(progress=0)
        threading.Thread(target=self._work, daemon=True).start()
        self._timer = self.set_interval(0.1, self._poll)

    def _work(self):
        state = self._state
        try:
            state['target'] = updater.install(self.release, progress=self._on_progress, cancelled=self._stop.is_set)
        except Cancelled:
            pass
        except Exception as e:  # shown in the bar
            state['error'] = str(e) or e.__class__.__name__
        state['finished'] = True

    def _on_progress(self, done, total):
        self._state.update(done=done, total=total)

    def _poll(self):
        state, r = self._state, self.release
        if not state['finished']:
            if state['total']:
                fraction = state['done'] / state['total']
                self.query_one('#update-progress', ProgressBar).update(progress=fraction)
                self.query_one('#update-text', Label).update('Downloading {} {}... {}%'.format(
                    APP_NAME, r.version, int(fraction * 100)))
            return
        self._timer.stop()
        if state['error']:
            self._show("Couldn't install {} {}: {} This version keeps working.".format(
                APP_NAME, r.version, state['error'].rstrip('.') + '.'),
                [('Try again', 'update-retry'), ('Close', 'update-close')])
        elif state['target']:
            self._show('{} {} is installed. Restarting...'.format(APP_NAME, r.version), [])
            self.app.exit(result=('restart', state['target']))
        else:  # cancelled: nothing was replaced
            self._offer()

    def _cancel(self):
        self._stop.set()
        self.query_one('#update-text', Label).update('Cancelling...')
