"""The update bar: shows a new release inside the main window and installs it there, without pop-up windows.

States, from hidden to restart:
  hidden        no update (or "Later" was clicked)
  offering      "Vault Sync X is available (you have Y). Updating restarts the app."  [Update now] [What's new] [Later]
  waiting       "Waiting for the running task to stop..."
  downloading   "Downloading Vault Sync X... 42%" with a progress bar  [Cancel]
  failed        "Couldn't install Vault Sync X: ... This version keeps working."  [Try again] [Close]
The App asks the bar to check (start_check) and gives it the callbacks it needs to cancel tasks and restart.
"""
import threading
import webbrowser
from tkinter import messagebox, ttk

from vaultsync import APP_NAME, __version__, updater
from vaultsync.common import Cancelled
from vaultsync.ui_common import run_in_background


class UpdateBar(ttk.Frame):
    """Hidden until a newer release is found. [status_label] is a label in the window header that shows the state
    of the check; [busy_tabs]() lists running tabs, [cancel_tabs]() stops them, [restart](target) ends the app."""

    def __init__(self, parent, status_label, busy_tabs, cancel_tabs, restart, pack_options):
        super().__init__(parent, padding=(14, 10), style='Card.TFrame')
        self.status_label = status_label
        self.busy_tabs, self.cancel_tabs, self.restart = busy_tabs, cancel_tabs, restart
        self.pack_options = pack_options
        self.release = None
        self._stop = threading.Event()
        self._state = {}

        self.text = ttk.Label(self, text='', wraplength=620)
        self.text.pack(side='left', fill='x', expand=True)
        self.buttons = ttk.Frame(self)
        self.buttons.pack(side='right', padx=(12, 0))
        self.progress = ttk.Progressbar(self, mode='determinate', maximum=1.0, length=180)

    # --- check ------------------------------------------------------------------------------------

    def start_check(self):
        self.status_label.configure(text='Checking for updates...')
        run_in_background(self, updater.check, self._on_checked)

    def _on_checked(self, release, error):
        if error:
            # Offline or GitHub unreachable: the app works without updating, but say so instead of hiding it.
            self.status_label.configure(text="Couldn't check for updates")
            print('Update check failed: {}'.format(error))
            return
        if not release:
            self.status_label.configure(text='Up to date')
            return
        self.release = release
        self.status_label.configure(text='Update available')
        self._offer()

    # --- states -----------------------------------------------------------------------------------

    def _show(self, text, buttons, progress=False):
        self.text.configure(text=text)
        for child in self.buttons.winfo_children():
            child.destroy()
        for i, (label, command) in enumerate(buttons):
            ttk.Button(self.buttons, text=label, command=command,
                       style='Accent.TButton' if i == 0 and len(buttons) > 1 else 'TButton'
                       ).pack(side='left', padx=(0 if i == 0 else 8, 0))
        if progress:
            self.progress.pack(side='right', padx=(12, 0), before=self.buttons)
        else:
            self.progress.pack_forget()
        if not self.winfo_ismapped():
            self.pack(**self.pack_options)

    def _hide(self):
        self.pack_forget()

    def _offer(self):
        r = self.release
        self._show('Vault Sync {} is available (you have {}). Updating restarts the app.'.format(r.version, __version__),
                   [('Update now', self._update_now), ("What's new", lambda: webbrowser.open(r.page)),
                    ('Later', self._hide)])

    def _update_now(self):
        busy = self.busy_tabs()
        if busy:
            if not messagebox.askyesno(APP_NAME, 'Still running: {}. Cancel it and update?\n\nFiles that were moved '
                                                 'aside are restored first.'.format(', '.join(busy)), parent=self):
                return
            self.cancel_tabs()
            self._show('Waiting for the running task to stop...', [])
        self._install_when_idle()

    def _install_when_idle(self):
        if self.busy_tabs():
            self.after(300, self._install_when_idle)
            return
        self._stop.clear()
        self._state = {'done': 0, 'total': self.release.size, 'finished': False, 'target': None, 'error': None}
        self._show('Downloading Vault Sync {}...'.format(self.release.version), [('Cancel', self._cancel)],
                   progress=True)
        self.progress.configure(value=0)
        threading.Thread(target=self._work, daemon=True).start()
        self.after(100, self._poll)

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
                self.progress.configure(value=fraction)
                self.text.configure(text='Downloading Vault Sync {}... {}%'.format(r.version, int(fraction * 100)))
            self.after(100, self._poll)
        elif state['error']:
            self._show("Couldn't install Vault Sync {}: {} This version keeps working.".format(
                r.version, state['error'].rstrip('.') + '.'), [('Try again', self._install_when_idle),
                                                               ('Close', self._hide)])
        elif state['target']:
            self._show('Vault Sync {} is installed. Restarting...'.format(r.version), [])
            self.after(300, lambda: self.restart(state['target']))
        else:  # cancelled: nothing was replaced
            self._offer()

    def _cancel(self):
        self._stop.set()
        self.text.configure(text='Cancelling...')
