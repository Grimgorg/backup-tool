# GoG Sync: Windows app that keeps a local folder an exact mirror of a GOG library (offline installers and
# extras), using gogrepoc. "Check for changes" previews what would be downloaded and deleted, "Apply changes"
# carries it out. The exe also runs gogrepoc itself when started with --gogrepoc (see sync_engine).
import ctypes
import io
import os
import queue
import re
import sys
import threading
import tkinter as tk
import webbrowser
from tkinter import filedialog, messagebox, ttk

APP_NAME = 'GoG Sync'
MAX_LOG_LINES = 20000


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


if len(sys.argv) > 1 and sys.argv[1] == '--gogrepoc':
    fix_child_stdio()
    import sync_engine
    sync_engine.run_gogrepoc(sys.argv[2:])
    sys.exit(0)

import sv_ttk  # noqa: E402  (not needed by the child process)
import sync_engine  # noqa: E402
from sync_engine import Engine, format_size  # noqa: E402


def windows_dark_mode():
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r'Software\Microsoft\Windows\CurrentVersion\Themes\Personalize') as key:
            return winreg.QueryValueEx(key, 'AppsUseLightTheme')[0] == 0
    except OSError:
        return False


def style_text(widget):
    # sv_ttk only styles ttk widgets; tk.Text needs the colors set by hand.
    if sv_ttk.get_theme() == 'dark':
        widget.configure(background='#1c1c1c', foreground='#e6e6e6', insertbackground='#e6e6e6',
                         selectbackground='#2f5c8f', highlightthickness=0, borderwidth=8)
    else:
        widget.configure(background='#fafafa', foreground='#1c1c1c', highlightthickness=0, borderwidth=8)


def set_dark_title_bar(window, dark):
    try:
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
        value = ctypes.c_int(1 if dark else 0)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(value), ctypes.sizeof(value))
    except (AttributeError, OSError):
        pass


class Dialog(tk.Toplevel):
    """Modal dialog base: subclasses fill self.body and set self.result in ok()."""

    def __init__(self, parent, title):
        super().__init__(parent)
        self.withdraw()
        self.title(title)
        self.transient(parent)
        self.resizable(False, False)
        self.result = None
        self.body = ttk.Frame(self, padding=20)
        self.body.pack(fill='both', expand=True)
        self.protocol('WM_DELETE_WINDOW', self.cancel)
        self.bind('<Escape>', lambda e: self.cancel())
        self.bind('<Return>', lambda e: self.ok())

    def add_buttons(self, ok_text='OK'):
        buttons = ttk.Frame(self.body)
        buttons.pack(fill='x', pady=(16, 0))
        ttk.Button(buttons, text='Cancel', command=self.cancel).pack(side='right')
        ttk.Button(buttons, text=ok_text, style='Accent.TButton', command=self.ok).pack(side='right', padx=(0, 8))

    def show(self):
        self.update_idletasks()
        parent = self.master
        x = parent.winfo_rootx() + (parent.winfo_width() - self.winfo_reqwidth()) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - self.winfo_reqheight()) // 3
        self.geometry('+{}+{}'.format(max(x, 0), max(y, 0)))
        self.deiconify()
        set_dark_title_bar(self, sv_ttk.get_theme() == 'dark')
        self.grab_set()
        self.wait_window()
        return self.result

    def ok(self):
        self.destroy()

    def cancel(self):
        self.result = None
        self.destroy()


class LoginDialog(Dialog):
    def __init__(self, parent):
        super().__init__(parent, 'Log in to GOG')
        ttk.Label(self.body, text='Use your GOG account. Google and Discord sign-ins are not supported.',
                  wraplength=360).pack(anchor='w', pady=(0, 12))
        ttk.Label(self.body, text='E-mail or username').pack(anchor='w')
        self.user = ttk.Entry(self.body, width=44)
        self.user.pack(fill='x', pady=(2, 10))
        ttk.Label(self.body, text='Password').pack(anchor='w')
        self.password = ttk.Entry(self.body, width=44, show='•')
        self.password.pack(fill='x', pady=(2, 0))
        self.add_buttons('Log in')
        self.user.focus_set()

    def ok(self):
        if self.user.get().strip() and self.password.get():
            self.result = (self.user.get().strip(), self.password.get())
            self.destroy()


class PromptDialog(Dialog):
    """Shows a question gogrepoc asks on its console (two-step code, sign-in URL, ...)."""

    def __init__(self, parent, prompt, context):
        super().__init__(parent, 'GOG asks for input')
        ttk.Label(self.body, text='gogrepoc needs an answer to continue:').pack(anchor='w', pady=(0, 6))
        text = tk.Text(self.body, width=80, height=8, wrap='word', relief='flat', font=('Consolas', 9))
        style_text(text)
        text.insert('1.0', context)
        text.configure(state='disabled')
        text.pack(fill='both', pady=(0, 10))
        urls = re.findall(r'https?://\S+', context)
        if urls:
            ttk.Button(self.body, text='Open sign-in page in browser',
                       command=lambda: webbrowser.open(urls[-1])).pack(anchor='w', pady=(0, 10))
        ttk.Label(self.body, text=prompt).pack(anchor='w')
        self.answer = ttk.Entry(self.body, width=80)
        self.answer.pack(fill='x', pady=(2, 0))
        self.add_buttons()
        self.answer.focus_set()

    def ok(self):
        self.result = self.answer.get()
        self.destroy()


class App:
    def __init__(self, root):
        self.root = root
        self.events = queue.Queue()
        self.engine = Engine(log=lambda line: self.events.put(('log', line)),
                             status=lambda text: self.events.put(('status', text)),
                             progress=lambda value: self.events.put(('progress', value)),
                             ask=self._ask_from_worker)
        self.worker = None
        self.plan = None
        self.close_when_done = False
        self.settings = sync_engine.load_settings()

        root.title(APP_NAME)
        root.geometry('1040x720')
        root.minsize(820, 560)
        root.protocol('WM_DELETE_WINDOW', self.on_close)
        self._build()
        self._load_settings_into_ui()
        self._refresh_account()
        self._set_busy(False)
        root.after(50, self._poll_events)
        root.after(200, self._recover_if_needed)

    def _recover_if_needed(self):
        # A sync that ended without cleaning up (crash, power loss) leaves its moved-aside files in !orphaned.
        if Engine.needs_recovery(self.settings):
            settings = dict(self.settings)
            self._run(lambda: self.engine.recover(settings), on_done=lambda _: None)

    # --- layout -----------------------------------------------------------------------------------

    def _build(self):
        outer = ttk.Frame(self.root, padding=(20, 16, 20, 16))
        outer.pack(fill='both', expand=True)

        header = ttk.Frame(outer)
        header.pack(fill='x')
        ttk.Label(header, text=APP_NAME, font=('Segoe UI Semibold', 20)).pack(side='left')
        self.login_button = ttk.Button(header, text='Log in...', command=self.on_login)
        self.login_button.pack(side='right')
        self.account_label = ttk.Label(header)
        self.account_label.pack(side='right', padx=(0, 12))

        settings = ttk.LabelFrame(outer, text='Settings', padding=(14, 10))
        settings.pack(fill='x', pady=(14, 12))
        settings.columnconfigure(1, weight=1)

        ttk.Label(settings, text='Output folder').grid(row=0, column=0, sticky='w', padx=(0, 10))
        self.save_dir = tk.StringVar()
        self.save_dir_entry = ttk.Entry(settings, textvariable=self.save_dir)
        self.save_dir_entry.grid(row=0, column=1, sticky='ew')
        self.browse_button = ttk.Button(settings, text='Browse...', command=self.on_browse)
        self.browse_button.grid(row=0, column=2, padx=(8, 0))
        ttk.Button(settings, text='Open', command=self.on_open_folder).grid(row=0, column=3, padx=(8, 0))

        ttk.Label(settings, text='Languages').grid(row=1, column=0, sticky='w', padx=(0, 10), pady=(10, 0))
        filters = ttk.Frame(settings)
        filters.grid(row=1, column=1, columnspan=3, sticky='ew', pady=(10, 0))
        self.langs = tk.StringVar()
        self.langs_entry = ttk.Entry(filters, textvariable=self.langs, width=14)
        self.langs_entry.pack(side='left')
        ttk.Label(filters, text='Operating systems').pack(side='left', padx=(20, 10))
        self.oses = tk.StringVar()
        self.oses_entry = ttk.Entry(filters, textvariable=self.oses, width=22)
        self.oses_entry.pack(side='left')
        ttk.Label(filters, text='space-separated, e.g. "en de" and "windows linux"',
                  foreground='gray').pack(side='left', padx=(12, 0))

        options = ttk.Frame(settings)
        options.grid(row=2, column=0, columnspan=4, sticky='w', pady=(10, 0))
        self.extras = tk.BooleanVar()
        self.full_scan = tk.BooleanVar()
        self.verify = tk.BooleanVar()
        self.sort = tk.BooleanVar()
        self.option_checks = []
        for text, var in (('Include extras (manuals, soundtracks, artwork...)', self.extras),
                          ('Full library scan', self.full_scan),
                          ('Verify files after download', self.verify),
                          ('Sort into Installer / Extras folders', self.sort)):
            check = ttk.Checkbutton(options, text=text, variable=var)
            check.pack(side='left', padx=(0, 18))
            self.option_checks.append(check)

        for var in (self.save_dir, self.langs, self.oses, self.extras, self.full_scan, self.verify, self.sort):
            var.trace_add('write', lambda *_: self._on_settings_changed())

        # Packed before the notebook so the notebook shrinks first when the window is small.
        bottom = ttk.Frame(outer)
        bottom.pack(side='bottom', fill='x', pady=(12, 0))
        self.cancel_button = ttk.Button(bottom, text='Cancel', command=self.on_cancel)
        self.cancel_button.pack(side='right')
        self.apply_button = ttk.Button(bottom, text='Apply changes', style='Accent.TButton', command=self.on_apply)
        self.apply_button.pack(side='right', padx=(0, 8))
        self.check_button = ttk.Button(bottom, text='Check for changes', command=self.on_check)
        self.check_button.pack(side='right', padx=(0, 8))
        status_box = ttk.Frame(bottom)
        status_box.pack(side='left', fill='x', expand=True, padx=(0, 16))
        self.status = ttk.Label(status_box, text='Ready.')
        self.status.pack(anchor='w')
        self.progress = ttk.Progressbar(status_box, mode='determinate', maximum=1.0)
        self.progress.pack(fill='x', pady=(6, 0))

        self.notebook = ttk.Notebook(outer)
        self.notebook.pack(fill='both', expand=True)

        changes = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(changes, text='Changes')
        self.summary = ttk.Label(changes, text='Click "Check for changes" to compare your GOG library with the output folder.')
        self.summary.pack(anchor='w', pady=(0, 8))
        tree_frame = ttk.Frame(changes)
        tree_frame.pack(fill='both', expand=True)
        self.tree = ttk.Treeview(tree_frame, columns=('size',), selectmode='browse')
        self.tree.heading('#0', text='File', anchor='w')
        self.tree.heading('size', text='Size', anchor='e')
        self.tree.column('#0', stretch=True, width=700)
        self.tree.column('size', stretch=False, width=120, anchor='e')
        tree_scroll = ttk.Scrollbar(tree_frame, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=tree_scroll.set)
        self.tree.pack(side='left', fill='both', expand=True)
        tree_scroll.pack(side='right', fill='y')

        log = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(log, text='Log')
        self.log_text = tk.Text(log, wrap='none', font=('Consolas', 9), relief='flat', state='disabled')
        style_text(self.log_text)
        log_scroll = ttk.Scrollbar(log, orient='vertical', command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_scroll.set)
        self.log_text.pack(side='left', fill='both', expand=True)
        log_scroll.pack(side='right', fill='y')
        self.log_text.tag_configure('warning', foreground='#d08700')
        self.log_text.tag_configure('error', foreground='#e5484d')
        self.log_text.tag_configure('command', foreground='#3b82f6')

    # --- settings ---------------------------------------------------------------------------------

    def _load_settings_into_ui(self):
        s = self.settings
        self._loading = True
        self.save_dir.set(s['save_dir'])
        self.langs.set(' '.join(s['langs']))
        self.oses.set(' '.join(s['os']))
        self.extras.set(s['extras'])
        self.full_scan.set(s['full_scan'])
        self.verify.set(s['verify'])
        self.sort.set(s['sort'])
        self._loading = False

    def _read_settings(self):
        s = dict(self.settings)
        s['save_dir'] = self.save_dir.get().strip()
        s['langs'] = self.langs.get().split()
        s['os'] = self.oses.get().split()
        s['extras'] = self.extras.get()
        s['full_scan'] = self.full_scan.get()
        s['verify'] = self.verify.get()
        s['sort'] = self.sort.get()
        return s

    def _on_settings_changed(self):
        if getattr(self, '_loading', False):
            return
        if self.plan is not None:
            # The preview no longer matches what Apply would do.
            self._clear_plan('Settings changed. Click "Check for changes" again.')

    def _validated_settings(self):
        s = self._read_settings()
        if not s['save_dir']:
            messagebox.showerror(APP_NAME, 'Please choose an output folder.', parent=self.root)
            return None
        if not s['langs'] or not s['os']:
            messagebox.showerror(APP_NAME, 'Please enter at least one language and one operating system.',
                                 parent=self.root)
            return None
        self.settings = s
        sync_engine.save_settings(s)
        return s

    # --- actions ----------------------------------------------------------------------------------

    def on_browse(self):
        folder = filedialog.askdirectory(parent=self.root, initialdir=self.save_dir.get() or None,
                                         title='Choose the output folder')
        if folder:
            self.save_dir.set(os.path.normpath(folder))

    def on_open_folder(self):
        path = self.save_dir.get().strip()
        if os.path.isdir(path):
            os.startfile(path)

    def on_login(self):
        credentials = LoginDialog(self.root).show()
        if credentials:
            self._run(lambda: self.engine.login(*credentials), on_done=lambda _: self._refresh_account())

    def on_check(self):
        if not sync_engine.is_logged_in():
            messagebox.showinfo(APP_NAME, 'Please log in to GOG first.', parent=self.root)
            return
        settings = self._validated_settings()
        if settings:
            self._clear_plan('Checking...')
            self._run(lambda: self.engine.check(settings), on_done=self._show_plan)

    def on_apply(self):
        plan = self.plan
        if plan is None or plan.empty:
            return
        lines = []
        if plan.downloads:
            lines.append('Download {} file(s), {}.'.format(len(plan.downloads), format_size(plan.download_size)))
        if plan.deletions:
            lines.append('Delete {} item(s), {}.'.format(len(plan.deletions), format_size(plan.deletion_size)))
        lines.append('\nOld files are only deleted once all downloads are complete.')
        if not messagebox.askyesno(APP_NAME, '\n'.join(lines), parent=self.root):
            return
        settings = self._validated_settings()
        if settings:
            self._run(lambda: self.engine.apply(settings, plan), on_done=self._after_apply)

    def on_cancel(self):
        if self.worker and self.worker.is_alive():
            self._set_status('Cancelling...')
            self.engine.cancel()

    def on_close(self):
        if self.worker and self.worker.is_alive():
            if not messagebox.askyesno(APP_NAME, 'A sync is running. Cancel it and close?\n\n'
                                       'Files that were moved aside are restored first.', parent=self.root):
                return
            self.close_when_done = True
            self.engine.cancel()
            return
        self.root.destroy()

    # --- worker -----------------------------------------------------------------------------------

    def _run(self, task, on_done):
        self._set_busy(True)

        def target():
            try:
                result = task()
                self.events.put(('done', on_done, result, None))
            except sync_engine.Cancelled:
                self.events.put(('done', None, None, 'Cancelled. Nothing was deleted.'))
            except Exception as e:  # shown to the user, details are in the log
                self.events.put(('log', 'ERROR: {}'.format(e)))
                self.events.put(('done', None, None, str(e) or e.__class__.__name__))

        self.worker = threading.Thread(target=target, daemon=True)
        self.worker.start()

    def _ask_from_worker(self, prompt, context):
        done = threading.Event()
        holder = {}
        self.events.put(('ask', prompt, context, holder, done))
        done.wait()
        return holder.get('answer')

    def _poll_events(self):
        log_lines = []
        try:
            while True:
                event = self.events.get_nowait()
                kind = event[0]
                if kind == 'log':
                    log_lines.append(event[1])
                    continue
                if log_lines:
                    self._append_log(log_lines)
                    log_lines = []
                if kind == 'status':
                    self._set_status(event[1])
                elif kind == 'progress':
                    self._set_progress(event[1])
                elif kind == 'ask':
                    _, prompt, context, holder, done = event
                    holder['answer'] = PromptDialog(self.root, prompt, context).show()
                    done.set()
                elif kind == 'done':
                    self._finish(*event[1:])
        except queue.Empty:
            pass
        if log_lines:
            self._append_log(log_lines)
        self.root.after(50, self._poll_events)

    def _finish(self, on_done, result, error):
        self.worker = None
        self._set_busy(False)
        self._set_progress(0)
        if self.close_when_done:
            self.root.destroy()
            return
        if error:
            self._set_status(error)
            if not error.startswith('Cancelled'):
                messagebox.showerror(APP_NAME, error, parent=self.root)
        elif on_done:
            on_done(result)

    # --- results ----------------------------------------------------------------------------------

    def _clear_plan(self, summary):
        self.plan = None
        self.tree.delete(*self.tree.get_children())
        self.summary.configure(text=summary)
        self._set_busy(bool(self.worker))

    def _show_plan(self, plan):
        self.plan = plan
        self.tree.delete(*self.tree.get_children())
        if plan.empty:
            self.summary.configure(text='Everything is up to date. The output folder mirrors your GOG library.')
            self._set_status('Up to date.')
        else:
            self.summary.configure(text='{} file(s) to download ({}), {} item(s) to delete ({}). '
                                        'Review the list, then click "Apply changes".'.format(
                                            len(plan.downloads), format_size(plan.download_size),
                                            len(plan.deletions), format_size(plan.deletion_size)))
            self._set_status('Changes found. Nothing has been changed yet.')
            self._add_section('Download', plan.downloads, plan.download_size)
            self._add_section('Delete', plan.deletions, plan.deletion_size)
        self.notebook.select(0)
        self._set_busy(False)

    def _add_section(self, title, items, total):
        section = self.tree.insert('', 'end', text='{} ({} file(s))'.format(title, len(items)),
                                   values=(format_size(total),), open=True)
        games = {}
        for item in items:
            games.setdefault(item.game, []).append(item)
        for game, game_items in sorted(games.items()):
            node = self.tree.insert(section, 'end', text=game, open=True,
                                    values=(format_size(sum(i.size or 0 for i in game_items)),))
            for item in game_items:
                self.tree.insert(node, 'end', text=item.file, values=(format_size(item.size),))

    def _after_apply(self, _):
        self._clear_plan('Sync complete. Click "Check for changes" to compare again.')
        messagebox.showinfo(APP_NAME, 'Sync complete. The output folder now mirrors your GOG library.',
                            parent=self.root)

    # --- small helpers ----------------------------------------------------------------------------

    def _refresh_account(self):
        if sync_engine.is_logged_in():
            self.account_label.configure(text='Logged in to GOG')
            self.login_button.configure(text='Log in again...')
        else:
            self.account_label.configure(text='Not logged in')
            self.login_button.configure(text='Log in...')

    def _set_busy(self, busy):
        idle = 'disabled' if busy else '!disabled'
        for widget in (self.check_button, self.login_button, self.save_dir_entry, self.browse_button,
                       self.langs_entry, self.oses_entry, *self.option_checks):
            widget.state([idle])
        can_apply = not busy and self.plan is not None and not self.plan.empty
        self.apply_button.state(['!disabled' if can_apply else 'disabled'])
        self.cancel_button.state(['!disabled' if busy else 'disabled'])

    def _set_status(self, text):
        self.status.configure(text=text)

    def _set_progress(self, value):
        if value is None:
            if str(self.progress.cget('mode')) != 'indeterminate':
                self.progress.configure(mode='indeterminate')
                self.progress.start(15)
        else:
            if str(self.progress.cget('mode')) != 'determinate':
                self.progress.stop()
                self.progress.configure(mode='determinate')
            self.progress.configure(value=value)

    def _append_log(self, lines):
        self.log_text.configure(state='normal')
        at_end = self.log_text.yview()[1] > 0.999
        for line in lines:
            lower = line.lower()
            tag = ()
            if line.startswith('> '):
                tag = ('command',)
            elif 'error' in lower or 'traceback' in lower or 'fatal' in lower:
                tag = ('error',)
            elif 'warn' in lower:
                tag = ('warning',)
            self.log_text.insert('end', line + '\n', tag)
        excess = int(self.log_text.index('end-1c').split('.')[0]) - MAX_LOG_LINES
        if excess > 0:
            self.log_text.delete('1.0', '{}.0'.format(excess + 1))
        if at_end:
            self.log_text.see('end')
        self.log_text.configure(state='disabled')


def main():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass
    root = tk.Tk()
    dark = windows_dark_mode()
    sv_ttk.set_theme('dark' if dark else 'light')
    App(root)
    root.update_idletasks()
    set_dark_title_bar(root, dark)
    root.mainloop()


if __name__ == '__main__':
    main()
