# GOG tab: keeps a local folder an exact mirror of a GOG library (offline installers and extras), using
# gogrepoc. "Check for changes" previews what would be downloaded and deleted, "Apply changes" carries it out.
import os
import re
import tkinter as tk
import webbrowser
from tkinter import filedialog, messagebox, ttk

from vaultsync import APP_NAME
from vaultsync.gog import engine as gog
from vaultsync.ui_common import Dialog, LogView, TaskTab, format_size, mono_font, open_folder, style_text


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
        self.bind_return()
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
        text = tk.Text(self.body, width=80, height=8, wrap='word', relief='flat', font=mono_font())
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
        self.bind_return()
        self.answer.focus_set()

    def ok(self):
        self.result = self.answer.get()
        self.destroy()


class GogTab(TaskTab):
    title = 'GOG Library'

    def __init__(self, parent, app):
        super().__init__(parent, app)
        self.engine = gog.Engine(log=self.log_cb, status=self.status_cb, progress=self.progress_cb, ask=self.ask_cb)
        self.plan = None
        self._loading = False
        copied = gog.migrate_legacy_data()
        self.settings = gog.load_settings()
        self._build()
        if copied:
            self.log_view.append(['Copied {} from {} to {}.'.format(', '.join(copied), gog.LEGACY_DIR, gog.WORK_DIR)])
        self._load_settings_into_ui()
        self._refresh_account()
        self.set_busy(False)
        self.after(200, self._recover_if_needed)

    def _recover_if_needed(self):
        # A sync that ended without cleaning up (crash, power loss) leaves its moved-aside files in !orphaned.
        if gog.Engine.needs_recovery(self.settings):
            settings = dict(self.settings)
            self.run_task(lambda: self.engine.recover(settings), on_done=lambda _: None)

    def cancel_task(self):
        self.engine.cancel()

    def ask_dialog(self, prompt, context):
        return PromptDialog(self, prompt, context).show()

    # --- layout -----------------------------------------------------------------------------------

    def _build(self):
        account = ttk.Frame(self)
        account.pack(fill='x')
        ttk.Label(account, text='Mirrors your GOG library (offline installers and extras) into a local folder.'
                  ).pack(side='left')
        self.login_button = ttk.Button(account, text='Log in...', command=self.on_login)
        self.login_button.pack(side='right')
        self.account_label = ttk.Label(account)
        self.account_label.pack(side='right', padx=(0, 12))

        settings = ttk.LabelFrame(self, text='Settings', padding=(14, 10))
        settings.pack(fill='x', pady=(12, 12))
        settings.columnconfigure(1, weight=1)

        ttk.Label(settings, text='Output folder').grid(row=0, column=0, sticky='w', padx=(0, 10))
        self.save_dir = tk.StringVar()
        self.save_dir_entry = ttk.Entry(settings, textvariable=self.save_dir)
        self.save_dir_entry.grid(row=0, column=1, sticky='ew')
        self.browse_button = ttk.Button(settings, text='Browse...', command=self.on_browse)
        self.browse_button.grid(row=0, column=2, padx=(8, 0))
        ttk.Button(settings, text='Open', command=lambda: open_folder(self.save_dir.get().strip())
                   ).grid(row=0, column=3, padx=(8, 0))

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
        buttons = self.build_status_bar(self)
        self.cancel_button = ttk.Button(buttons, text='Cancel', command=self.on_cancel)
        self.cancel_button.pack(side='right')
        self.apply_button = ttk.Button(buttons, text='Apply changes', style='Accent.TButton', command=self.on_apply)
        self.apply_button.pack(side='right', padx=(0, 8))
        self.check_button = ttk.Button(buttons, text='Check for changes', command=self.on_check)
        self.check_button.pack(side='right', padx=(0, 8))

        self.notebook = ttk.Notebook(self)
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
        self.log_view = LogView(log)
        self.log_view.pack(fill='both', expand=True)

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
        if self._loading:
            return
        if self.plan is not None:
            # The preview no longer matches what Apply would do.
            self._clear_plan('Settings changed. Click "Check for changes" again.')

    def _validated_settings(self):
        s = self._read_settings()
        if not s['save_dir']:
            messagebox.showerror(APP_NAME, 'Please choose an output folder.', parent=self)
            return None
        if not s['langs'] or not s['os']:
            messagebox.showerror(APP_NAME, 'Please enter at least one language and one operating system.',
                                 parent=self)
            return None
        self.settings = s
        gog.save_settings(s)
        return s

    # --- actions ----------------------------------------------------------------------------------

    def on_browse(self):
        folder = filedialog.askdirectory(parent=self, initialdir=self.save_dir.get() or None,
                                         title='Choose the output folder')
        if folder:
            self.save_dir.set(os.path.normpath(folder))

    def on_login(self):
        credentials = LoginDialog(self).show()
        if credentials:
            self.run_task(lambda: self.engine.login(*credentials), on_done=lambda _: self._refresh_account())

    def on_check(self):
        if not gog.is_logged_in():
            messagebox.showinfo(APP_NAME, 'Please log in to GOG first.', parent=self)
            return
        settings = self._validated_settings()
        if settings:
            self._clear_plan('Checking...')
            self.run_task(lambda: self.engine.check(settings), on_done=self._show_plan)

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
        if not messagebox.askyesno(APP_NAME, '\n'.join(lines), parent=self):
            return
        settings = self._validated_settings()
        if settings:
            self.run_task(lambda: self.engine.apply(settings, plan), on_done=self._after_apply)

    # --- results ----------------------------------------------------------------------------------

    def _clear_plan(self, summary):
        self.plan = None
        self.tree.delete(*self.tree.get_children())
        self.summary.configure(text=summary)
        self.set_busy(self.busy)

    def _show_plan(self, plan):
        self.plan = plan
        self.tree.delete(*self.tree.get_children())
        if plan.empty:
            self.summary.configure(text='Everything is up to date. The output folder mirrors your GOG library.')
            self.set_status('Up to date.')
        else:
            self.summary.configure(text='{} file(s) to download ({}), {} item(s) to delete ({}). '
                                        'Review the list, then click "Apply changes".'.format(
                                            len(plan.downloads), format_size(plan.download_size),
                                            len(plan.deletions), format_size(plan.deletion_size)))
            self.set_status('Changes found. Nothing has been changed yet.')
            self._add_section('Download', plan.downloads, plan.download_size)
            self._add_section('Delete', plan.deletions, plan.deletion_size)
        self.notebook.select(0)
        self.set_busy(False)

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
        messagebox.showinfo(APP_NAME, 'Sync complete. The output folder now mirrors your GOG library.', parent=self)

    # --- small helpers ----------------------------------------------------------------------------

    def _refresh_account(self):
        if gog.is_logged_in():
            self.account_label.configure(text='Logged in to GOG')
            self.login_button.configure(text='Log in again...')
        else:
            self.account_label.configure(text='Not logged in')
            self.login_button.configure(text='Log in...')

    def set_busy(self, busy):
        idle = 'disabled' if busy else '!disabled'
        for widget in (self.check_button, self.login_button, self.save_dir_entry, self.browse_button,
                       self.langs_entry, self.oses_entry, *self.option_checks):
            widget.state([idle])
        can_apply = not busy and self.plan is not None and not self.plan.empty
        self.apply_button.state(['!disabled' if can_apply else 'disabled'])
        self.cancel_button.state(['!disabled' if busy else 'disabled'])
