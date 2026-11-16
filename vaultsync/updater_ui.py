"""Dialogs of the update check: asks whether to install a new release and shows the download."""
import threading
import tkinter as tk
from tkinter import ttk

from vaultsync import __version__, updater
from vaultsync.common import Cancelled, format_size
from vaultsync.ui_common import Dialog, mono_font, style_text

UPDATE, LATER, SKIP = 'update', 'later', 'skip'


class UpdateDialog(Dialog):
    """Tells about a new release; result is UPDATE, LATER or SKIP (skip this version)."""

    def __init__(self, parent, release):
        super().__init__(parent, 'Update available', resizable=True)
        self.result = LATER
        ttk.Label(self.body, text='Vault Sync {} is available. You have {}.'.format(release.version, __version__),
                  font=('TkDefaultFont', 13, 'bold')).pack(anchor='w')
        ttk.Label(self.body, text='Update now? The app downloads the new version ({}), closes and starts it again.'
                  .format(format_size(release.size) or 'size unknown'), foreground='gray'
                  ).pack(anchor='w', pady=(4, 10))
        buttons = ttk.Frame(self.body)
        buttons.pack(side='bottom', fill='x', pady=(14, 0))
        ttk.Button(buttons, text='Skip this version', command=lambda: self._close(SKIP)).pack(side='left')
        ttk.Button(buttons, text='Later', command=lambda: self._close(LATER)).pack(side='right')
        ttk.Button(buttons, text='Update now', style='Accent.TButton', command=lambda: self._close(UPDATE)
                   ).pack(side='right', padx=(0, 8))
        if release.notes:
            notes = tk.Text(self.body, width=72, height=10, wrap='word', relief='flat', font=mono_font())
            style_text(notes)
            notes.insert('1.0', release.notes)
            notes.configure(state='disabled')
            notes.pack(fill='both', expand=True)

    def _close(self, result):
        self.result = result
        self.destroy()

    def cancel(self):
        self._close(LATER)


class InstallDialog(Dialog):
    """Downloads and installs [release] in a worker thread. result: the path to restart, or None."""

    def __init__(self, parent, release):
        super().__init__(parent, 'Updating Vault Sync')
        self.release = release
        self.error = None
        self._state = {'done': 0, 'total': release.size, 'finished': False, 'target': None, 'error': None}
        self._stop = threading.Event()
        self.label = ttk.Label(self.body, text='Downloading Vault Sync {}...'.format(release.version), width=60)
        self.label.pack(anchor='w')
        self.bar = ttk.Progressbar(self.body, mode='determinate', maximum=1.0)
        self.bar.pack(fill='x', pady=(10, 0))
        buttons = ttk.Frame(self.body)
        buttons.pack(fill='x', pady=(14, 0))
        self.cancel_button = ttk.Button(buttons, text='Cancel', command=self.cancel)
        self.cancel_button.pack(side='right')
        threading.Thread(target=self._work, daemon=True).start()
        self.after(100, self._poll)

    def _work(self):
        state = self._state
        try:
            state['target'] = updater.install(self.release, progress=self._progress, cancelled=self._stop.is_set)
        except Cancelled:
            pass
        except Exception as e:  # shown by the caller
            state['error'] = str(e) or e.__class__.__name__
        state['finished'] = True

    def _progress(self, done, total):
        self._state.update(done=done, total=total)

    def _poll(self):
        state = self._state
        if state['finished']:
            self.result, self.error = state['target'], state['error']
            self.destroy()
            return
        if state['total']:
            self.bar.configure(value=state['done'] / state['total'])
            self.label.configure(text='Downloading Vault Sync {}: {} of {}'.format(
                self.release.version, format_size(state['done']), format_size(state['total'])))
        self.after(100, self._poll)

    def cancel(self):
        # The worker stops at its next chunk; nothing has been replaced before the download is complete.
        self._stop.set()
        self.cancel_button.state(['disabled'])
        self.label.configure(text='Cancelling...')
