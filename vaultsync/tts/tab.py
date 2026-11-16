# Tabletop Simulator tab: lists the TTS mods, "Check" counts what is already cached, "Back up" downloads every
# asset and writes one zip per mod into the backup folder, "Restore..." puts backups back into TTS.
import copy
import os
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from vaultsync import APP_NAME
from vaultsync.tts import engine as tts
from vaultsync.tts.mods import default_mods_dir
from vaultsync.ui_common import LogView, TaskTab, format_size, open_folder, run_in_background

INTRO = ('Backs up Tabletop Simulator mods with every image, model, sound and PDF they use, so they still work '
         'when the original links are gone.')
EXTRAS_HELP = ('Arkham Horror LCG (SCE): also back up the packs of its in-game download menu. They are saved as '
               'Saved Objects (in TTS: Objects > Saved Objects > {}) and can be large.'
               .format(tts.arkham.SAVED_OBJECTS_FOLDER))


class TtsTab(TaskTab):
    title = 'Tabletop Simulator'

    def __init__(self, parent, app):
        super().__init__(parent, app)
        self.engine = tts.Engine(log=self.log_cb, status=self.status_cb, progress=self.progress_cb)
        self.cfg = tts.load_config()
        self.entries = []
        self.checks = {}
        self.results = {}
        self._reload_job = None
        self._list_request = 0
        self._loading = True
        self._build()
        self.mods_dir.set(self.cfg['mods_dir'])
        self.backup_dir.set(self.cfg['backup_dir'])
        self.extras.set(self.cfg['extras'])
        self.languages.set(self.cfg['arkham_languages'])
        self._loading = False
        self.set_busy(False)
        self.reload_mods()

    def cancel_task(self):
        self.engine.cancel()

    # --- layout -----------------------------------------------------------------------------------

    def _build(self):
        ttk.Label(self, text=INTRO).pack(anchor='w')

        settings = ttk.LabelFrame(self, text='Settings', padding=(14, 10))
        settings.pack(fill='x', pady=(12, 12))
        settings.columnconfigure(1, weight=1)
        self.mods_dir = tk.StringVar()
        self.backup_dir = tk.StringVar()
        self.folder_widgets = []
        for row, (label, var, title) in enumerate((
                ('TTS Mods folder', self.mods_dir, 'Choose the Tabletop Simulator "Mods" folder'),
                ('Backup folder', self.backup_dir, 'Choose the folder for the backups'))):
            ttk.Label(settings, text=label).grid(row=row * 2, column=0, sticky='w', padx=(0, 10))
            entry = ttk.Entry(settings, textvariable=var)
            entry.grid(row=row * 2, column=1, sticky='ew')
            browse = ttk.Button(settings, text='Browse...', command=lambda v=var, t=title: self.on_browse(v, t))
            browse.grid(row=row * 2, column=2, padx=(8, 0))
            ttk.Button(settings, text='Open', command=lambda v=var: open_folder(self._folder_of(v))
                       ).grid(row=row * 2, column=3, padx=(8, 0))
            self.folder_widgets += [entry, browse]
        ttk.Label(settings, text='Leave empty for the TTS default: {}'.format(default_mods_dir()), foreground='gray'
                  ).grid(row=1, column=1, columnspan=3, sticky='w', pady=(2, 8))
        ttk.Label(settings, text='One zip per mod. Mods in TTS folders go into the same subfolder.',
                  foreground='gray').grid(row=3, column=1, columnspan=3, sticky='w', pady=(2, 8))

        extras = ttk.Frame(settings)
        extras.grid(row=4, column=0, columnspan=4, sticky='ew')
        self.extras = tk.BooleanVar()
        self.extras_check = ttk.Checkbutton(extras, text='Back up downloadable content', variable=self.extras)
        self.extras_check.pack(side='left')
        ttk.Label(extras, text='Language packs').pack(side='left', padx=(24, 8))
        self.languages = tk.StringVar()
        self.languages_entry = ttk.Entry(extras, textvariable=self.languages, width=24)
        self.languages_entry.pack(side='left')
        ttk.Label(extras, text='e.g. German,French  or  all / none', foreground='gray').pack(side='left', padx=(8, 0))
        ttk.Label(settings, text=EXTRAS_HELP, foreground='gray', wraplength=900
                  ).grid(row=5, column=0, columnspan=4, sticky='w', pady=(4, 0))
        for var in (self.mods_dir, self.backup_dir, self.extras, self.languages):
            var.trace_add('write', lambda *_: self._on_settings_changed())

        buttons = self.build_status_bar(self)
        self.cancel_button = ttk.Button(buttons, text='Cancel', command=self.on_cancel)
        self.cancel_button.pack(side='right')
        self.backup_button = ttk.Button(buttons, text='Back up', style='Accent.TButton', command=self.on_backup)
        self.backup_button.pack(side='right', padx=(0, 8))
        self.check_button = ttk.Button(buttons, text='Check', command=self.on_check)
        self.check_button.pack(side='right', padx=(0, 8))
        self.restore_button = ttk.Button(buttons, text='Restore...', command=self.on_restore)
        self.restore_button.pack(side='right', padx=(0, 24))

        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill='both', expand=True)
        mods = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(mods, text='Mods')
        top = ttk.Frame(mods)
        top.pack(fill='x', pady=(0, 8))
        self.summary = ttk.Label(top, text='')
        self.summary.pack(side='left')
        self.list_buttons = []
        for text, command in (('Reload', self.reload_mods), ('Include none', lambda: self._include_all(False)),
                              ('Include all', lambda: self._include_all(True))):
            button = ttk.Button(top, text=text, command=command)
            button.pack(side='right', padx=(8, 0))
            self.list_buttons.append(button)
        frame = ttk.Frame(mods)
        frame.pack(fill='both', expand=True)
        self.tree = ttk.Treeview(frame, columns=('include', 'folder', 'assets', 'status'), selectmode='browse')
        scroll = ttk.Scrollbar(frame, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side='left', fill='both', expand=True)
        scroll.pack(side='right', fill='y')
        for column, text, width, anchor, stretch in (
                ('#0', 'Mod', 320, 'w', True), ('include', 'Back up', 80, 'center', False),
                ('folder', 'TTS folder', 150, 'w', False), ('assets', 'Assets', 90, 'e', False),
                ('status', 'Status', 380, 'w', True)):
            self.tree.heading(column, text=text, anchor=anchor)
            self.tree.column(column, width=width, anchor=anchor, stretch=stretch)
        self.tree.bind('<Double-1>', self._on_double_click)
        ttk.Label(mods, text='Double-click a mod to include or leave it out.', foreground='gray'
                  ).pack(anchor='w', pady=(6, 0))
        log = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(log, text='Log')
        self.log_view = LogView(log)
        self.log_view.pack(fill='both', expand=True)

    # --- settings ---------------------------------------------------------------------------------

    def _folder_of(self, var):
        value = var.get().strip()
        if var is self.mods_dir and not value:
            return str(default_mods_dir())
        return os.path.expanduser(value)

    def on_browse(self, var, title):
        folder = filedialog.askdirectory(parent=self, initialdir=self._folder_of(var) or None, title=title)
        if folder:
            var.set(os.path.normpath(folder))

    def _on_settings_changed(self):
        if self._loading:
            return
        old_mods_dir = self.cfg['mods_dir']
        self.cfg.update(mods_dir=self.mods_dir.get().strip(), backup_dir=self.backup_dir.get().strip(),
                        extras=self.extras.get(), arkham_languages=self.languages.get().strip())
        tts.save_config(self.cfg)
        self.checks, self.results = {}, {}
        if self.cfg['mods_dir'] != old_mods_dir:
            # Wait until typing pauses instead of reading the folder on every key press.
            if self._reload_job:
                self.after_cancel(self._reload_job)
            self._reload_job = self.after(600, self.reload_mods)
        else:
            self._fill_tree()

    # --- mod list ---------------------------------------------------------------------------------

    def reload_mods(self):
        self._reload_job = None
        self._list_request += 1
        request = self._list_request
        self.summary.configure(text='Reading the mod list...')
        cfg = copy.deepcopy(self.cfg)
        run_in_background(self, lambda: tts.list_mods(cfg),
                          lambda entries, error: self._show_mods(entries, error, request))

    def _show_mods(self, entries, error, request):
        if request != self._list_request:
            return  # an older read finished after a newer one was started
        if error:
            self.entries = []
            self._fill_tree()
            self.summary.configure(text='Could not read the mods: {}'.format(error))
            return
        self.entries = entries
        self.checks, self.results = {}, {}
        self._fill_tree()

    def _included(self):
        skip = set(self.cfg['skip'])
        return [e for e in self.entries if e.key not in skip]

    def _fill_tree(self):
        tree = self.tree
        tree.delete(*tree.get_children())
        skip = set(self.cfg['skip'])
        for entry in self.entries:
            assets, status = self._describe(entry)
            tree.insert('', 'end', iid=entry.key, text=entry.name,
                        values=('–' if entry.key in skip else '✔', entry.folder, assets, status))
        if not self.entries:
            folder = self._folder_of(self.mods_dir)
            self.summary.configure(text='No mods found in {}.'.format(os.path.join(folder, 'Workshop')))
        else:
            self.summary.configure(text='{} mods, {} included. Click "Check" to see what is missing, "Back up" to '
                                        'download it and write the backups.'.format(len(self.entries),
                                                                                    len(self._included())))
        self.set_busy(self.busy)

    def _describe(self, entry):
        result = self.results.get(entry.key)
        if result:
            if result.error:
                return '', 'Error: ' + result.error
            size = sum(p.stat().st_size for p in result.zips if p.exists())
            status = 'Backed up ({}{})'.format(format_size(size), ', {} zips'.format(len(result.zips))
                                               if len(result.zips) > 1 else '')
            if result.missing:
                status += ', {} missing'.format(result.missing)
            if result.warnings:
                status += ', warning (see log)'
            return result.assets, status
        check = self.checks.get(entry.key)
        if check:
            if check.error:
                return '', 'Error: ' + check.error
            assets = check.assets + check.pack_assets
            status = 'all cached' if not check.to_download else '{} to download'.format(check.to_download)
            if check.arkham:
                status += ', {} downloadable packs from earlier'.format(check.packs_local) if check.packs_local \
                    else ', packs are fetched during the backup'
            if check.warning:
                status += ', warning: ' + check.warning
            return assets, status
        return '', ''

    def _on_double_click(self, event):
        row = self.tree.identify_row(event.y)
        if row and not self.busy:
            self.toggle_mod(row)

    def toggle_mod(self, row):
        """Includes or leaves out the mod with list key [row]."""
        skip = self.cfg['skip']
        if row in skip:
            skip.remove(row)
        else:
            skip.append(row)
        tts.save_config(self.cfg)
        self._fill_tree()
        self.tree.selection_set(row)

    def _include_all(self, include):
        self.cfg['skip'] = [] if include else [e.key for e in self.entries]
        tts.save_config(self.cfg)
        self._fill_tree()

    # --- actions ----------------------------------------------------------------------------------

    def on_check(self):
        entries = self._included()
        if not entries:
            messagebox.showinfo(APP_NAME, 'Include at least one mod first.', parent=self)
            return
        cfg = copy.deepcopy(self.cfg)
        self.results = {}
        self.run_task(lambda: self.engine.check(cfg, entries), on_done=self._after_check)

    def _after_check(self, checks):
        self.checks = {c.entry.key: c for c in checks}
        self._fill_tree()
        to_download = sum(c.to_download for c in checks)
        arkham = any(c.arkham for c in checks)
        text = '{} asset(s) to download.'.format(to_download) if to_download else 'All assets are cached.'
        if arkham:
            text += ' Downloadable packs are fetched during the backup.'
        self.set_status(text + ' Nothing has been downloaded yet.')

    def on_backup(self):
        entries = self._included()
        if not entries:
            messagebox.showinfo(APP_NAME, 'Include at least one mod first.', parent=self)
            return
        if not self.cfg['backup_dir']:
            messagebox.showerror(APP_NAME, 'Please choose a backup folder.', parent=self)
            return
        text = ('Back up {} mod(s) into\n{}?\n\nMissing assets are downloaded into the TTS folders first. Existing '
                'backups of these mods are replaced.'.format(len(entries), self.cfg['backup_dir']))
        if self.cfg['extras']:
            text += ('\n\nArkham Horror (SCE) downloadable content can be tens of GB; the backup needs about the '
                     'same space again.')
        if not messagebox.askyesno(APP_NAME, text, parent=self):
            return
        cfg = copy.deepcopy(self.cfg)
        self.notebook.select(1)
        self.run_task(lambda: self.engine.backup(cfg, entries), on_done=self._after_backup)

    def _after_backup(self, results):
        self.results = {r.entry.key: r for r in results}
        self._fill_tree()
        self.notebook.select(0)
        errors = [r for r in results if r.error]
        missing = sum(r.missing for r in results)
        warnings = ['{}: {}'.format(r.entry.name, w) for r in results for w in r.warnings]
        size = sum(p.stat().st_size for r in results for p in r.zips if p.exists())
        text = 'Backed up {} of {} mod(s), {} in {}.'.format(len(results) - len(errors), len(results),
                                                             format_size(size), self.cfg['backup_dir'])
        self.set_status(text)
        details = []
        if missing:
            details.append('{} asset(s) could not be downloaded; they are listed in each zip\'s manifest.json '
                           'and in the log.'.format(missing))
        if errors:
            details.append('Failed:\n' + '\n'.join('{}: {}'.format(r.entry.name, r.error) for r in errors))
        if warnings:
            details.append('Warnings:\n' + '\n'.join(warnings))
        if details:
            messagebox.showwarning(APP_NAME, text + '\n\n' + '\n\n'.join(details), parent=self)
        else:
            messagebox.showinfo(APP_NAME, text, parent=self)

    def on_restore(self):
        folder = filedialog.askdirectory(parent=self, title='Choose the folder with the backups',
                                         initialdir=self._folder_of(self.backup_dir) or None)
        if not folder:
            return
        target = self._folder_of(self.mods_dir)
        if not messagebox.askyesno(APP_NAME, 'Restore every backup in\n{}\ninto\n{}?\n\nFiles that already exist '
                                             'are kept.'.format(folder, target), parent=self):
            return
        cfg = copy.deepcopy(self.cfg)
        self.notebook.select(1)
        self.run_task(lambda: self.engine.restore(cfg, folder), on_done=self._after_restore)

    def _after_restore(self, result):
        restored, written, problems = result
        text = 'Restored {} backup(s), {} file(s) written.'.format(len(restored), written)
        self.set_status(text)
        self.reload_mods()
        if problems:
            messagebox.showwarning(APP_NAME, '{}\n\nSkipped:\n{}'.format(text, '\n'.join(problems[:10])), parent=self)
        else:
            messagebox.showinfo(APP_NAME, text, parent=self)

    def set_busy(self, busy):
        idle = 'disabled' if busy else '!disabled'
        for widget in (self.check_button, self.restore_button, self.extras_check, self.languages_entry,
                       *self.folder_widgets, *self.list_buttons):
            widget.state([idle])
        can_run = not busy and bool(self._included())
        self.backup_button.state(['!disabled' if can_run else 'disabled'])
        self.check_button.state(['!disabled' if can_run else 'disabled'])
        self.cancel_button.state(['!disabled' if busy else 'disabled'])
