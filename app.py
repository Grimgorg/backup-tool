# Vault Sync: one window with a tab per sub-app (GOG library, podcasts, Tabletop Simulator). Each tab runs its own tasks, so a
# GOG sync and a podcast download can run at the same time.
# The executable also runs gogrepoc itself when started with --gogrepoc (see vaultsync.gog.engine), and prints its
# version when started with --version.
import io
import sys


def fix_child_stdio():
    # A windowed exe starts without sys.stdout/stdin even when the parent passes pipes.
    if sys.stdout is None:
        sys.stdout = io.TextIOWrapper(io.FileIO(1, 'w', closefd=False), encoding='utf-8', errors='replace')
    if sys.stderr is None:
        sys.stderr = sys.stdout
    if sys.stdin is None:
        sys.stdin = io.TextIOWrapper(io.FileIO(0, 'r', closefd=False), encoding='utf-8')
    sys.stdout.reconfigure(encoding='utf-8', errors='replace', line_buffering=True)
    sys.stdin.reconfigure(encoding='utf-8')


if len(sys.argv) > 1 and sys.argv[1] == '--version':
    fix_child_stdio()
    from vaultsync import __version__ as _version
    print(_version)
    sys.exit(0)

if len(sys.argv) > 1 and sys.argv[1] == '--gogrepoc':
    fix_child_stdio()
    from vaultsync.gog import engine as _gog_engine
    _gog_engine.run_gogrepoc(sys.argv[2:])
    sys.exit(0)

import os  # noqa: E402
import tkinter as tk  # noqa: E402
from tkinter import messagebox, ttk  # noqa: E402

import sv_ttk  # noqa: E402

from vaultsync import APP_NAME, __version__, paths, updater  # noqa: E402
from vaultsync.common import load_json, save_json  # noqa: E402
from vaultsync.gog.tab import GogTab  # noqa: E402
from vaultsync.podcasts.tab import PodcastsTab  # noqa: E402
from vaultsync.tts.tab import TtsTab  # noqa: E402
from vaultsync.ui_common import heading_font, open_folder, run_in_background, set_dark_title_bar, \
    set_dpi_awareness, set_window_icon, system_dark_mode  # noqa: E402
from vaultsync.updater_ui import SKIP, UPDATE, InstallDialog, UpdateDialog  # noqa: E402

APP_SETTINGS_FILE = os.path.join(paths.CONFIG_DIR, 'app.json')
TABS = (GogTab, PodcastsTab, TtsTab)


class App:
    def __init__(self, root):
        self.root = root
        self.closing = False
        self.settings = load_json(APP_SETTINGS_FILE, {})

        root.title(APP_NAME)
        root.geometry(self.settings.get('geometry', '1080x780'))
        root.minsize(860, 620)
        root.protocol('WM_DELETE_WINDOW', self.on_close)

        outer = ttk.Frame(root, padding=(20, 16, 20, 16))
        outer.pack(fill='both', expand=True)
        header = ttk.Frame(outer)
        header.pack(fill='x', pady=(0, 10))
        ttk.Label(header, text=APP_NAME, font=heading_font()).pack(side='left')
        ttk.Button(header, text='Open data folder', command=lambda: open_folder(paths.data_dir())
                   ).pack(side='right')
        ttk.Label(header, text='v' + __version__, foreground='gray').pack(side='right', padx=(0, 12))

        self.notebook = ttk.Notebook(outer)
        self.notebook.pack(fill='both', expand=True)
        self.tabs = []
        for tab_class in TABS:
            tab = tab_class(self.notebook, self)
            self.notebook.add(tab, text='  {}  '.format(tab.title))
            self.tabs.append(tab)
        selected = self.settings.get('tab')
        for tab in self.tabs:
            if type(tab).__name__ == selected:
                self.notebook.select(tab)

        updater.cleanup_previous()
        if updater.can_self_update():
            root.after(1500, self.check_for_updates)

    # --- updates ----------------------------------------------------------------------------------

    def check_for_updates(self):
        """Looks for a newer release in the background and offers to install it."""
        run_in_background(self.root, updater.check, self._on_update_found)

    def _on_update_found(self, release, error):
        if error:
            print('Update check failed: {}'.format(error))  # offline or GitHub unreachable: try again next start
            return
        if not release or release.version == self.settings.get('skip_version') or self.closing:
            return
        answer = UpdateDialog(self.root, release).show()
        if answer == SKIP:
            self.settings['skip_version'] = release.version
            self._save_settings()
        elif answer == UPDATE:
            busy = [tab for tab in self.tabs if tab.busy]
            if busy and not messagebox.askyesno(
                    APP_NAME, 'Still running: {}. Cancel it and update?\n\nFiles that were moved aside are restored '
                              'first.'.format(', '.join(tab.title for tab in busy)), parent=self.root):
                return
            for tab in busy:
                tab.cancel_task()
            self._install_when_idle(release)

    def _install_when_idle(self, release):
        if any(tab.busy for tab in self.tabs):
            self.root.after(300, lambda: self._install_when_idle(release))
            return
        dialog = InstallDialog(self.root, release)
        target = dialog.show()
        if dialog.error:
            messagebox.showerror(APP_NAME, 'The update failed: {}\n\nThis version keeps working. You can also '
                                           'download the new version from\n{}'.format(dialog.error, release.page),
                                 parent=self.root)
        elif target:
            self._save_settings()
            updater.restart(target)
            self.root.destroy()

    def _save_settings(self):
        tab = self.notebook.nametowidget(self.notebook.select())
        self.settings.update(tab=type(tab).__name__, geometry=self.root.geometry())
        try:
            save_json(APP_SETTINGS_FILE, self.settings)
        except OSError:
            pass

    def on_close(self):
        busy = [tab for tab in self.tabs if tab.busy]
        if busy:
            names = ', '.join(tab.title for tab in busy)
            if not messagebox.askyesno(APP_NAME, 'Still running: {}. Cancel and close?\n\nFiles that were moved '
                                                 'aside are restored first.'.format(names), parent=self.root):
                return
            self.closing = True
            for tab in busy:
                tab.cancel_task()
            return
        self._save_settings()
        self.root.destroy()

    def task_finished(self):
        """Called by a tab when its task ended while the app is closing."""
        if not any(tab.busy for tab in self.tabs):
            self._save_settings()
            self.root.destroy()


def main():
    set_dpi_awareness()
    root = tk.Tk()
    set_window_icon(root)
    dark = system_dark_mode()
    sv_ttk.set_theme('dark' if dark else 'light')
    App(root)
    root.update_idletasks()
    set_dark_title_bar(root, dark)
    root.mainloop()


if __name__ == '__main__':
    main()
