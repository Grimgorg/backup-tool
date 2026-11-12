# Podcasts tab: a watch list of podcasts whose episodes are archived into numbered folders. "Check for new
# episodes" reads the feeds and lists what would be downloaded, "Download new episodes" fetches it.
import copy
import os
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from vaultsync import APP_NAME
from vaultsync.podcasts import engine as pc
from vaultsync.ui_common import Dialog, LogView, TaskTab, format_size, mono_font, open_folder, run_in_background, \
    style_text

RULES_HELP = ('Each episode goes into the folder of the first rule with a pattern that matches its title. Patterns '
              'are regular expressions and ignore upper/lower case; "^" means "the title starts with". Episodes '
              'that match no rule get a folder named after the start of their title.')


def scrolled_tree(parent, columns, height):
    frame = ttk.Frame(parent)
    tree = ttk.Treeview(frame, columns=columns, height=height, selectmode='browse')
    scroll = ttk.Scrollbar(frame, orient='vertical', command=tree.yview)
    tree.configure(yscrollcommand=scroll.set)
    tree.pack(side='left', fill='both', expand=True)
    scroll.pack(side='right', fill='y')
    return frame, tree


class FeedSearchDialog(Dialog):
    """Finds a podcast's RSS feed by name in the Apple Podcasts directory."""

    def __init__(self, parent, term=''):
        super().__init__(parent, 'Find a podcast', resizable=True)
        self.results = []
        row = ttk.Frame(self.body)
        row.pack(fill='x')
        self.term = ttk.Entry(row, width=50)
        self.term.insert(0, term)
        self.term.pack(side='left', fill='x', expand=True)
        self.term.bind('<Return>', lambda e: self.search())
        self.search_button = ttk.Button(row, text='Search', style='Accent.TButton', command=self.search)
        self.search_button.pack(side='left', padx=(8, 0))
        self.info = ttk.Label(self.body, text='Search by podcast name. Results come from the Apple Podcasts '
                                              'directory and work in any podcast app.', foreground='gray')
        self.info.pack(anchor='w', pady=(8, 8))
        self.add_buttons('Use this podcast')
        frame, self.tree = scrolled_tree(self.body, ('author',), 12)
        frame.pack(fill='both', expand=True)
        self.tree.heading('#0', text='Podcast', anchor='w')
        self.tree.heading('author', text='By', anchor='w')
        self.tree.column('#0', width=380)
        self.tree.column('author', width=220)
        self.tree.bind('<Double-1>', lambda e: self.ok())
        self.term.focus_set()
        if term:
            self.after(50, self.search)

    def search(self):
        term = self.term.get().strip()
        if not term:
            return
        self.search_button.state(['disabled'])
        self.info.configure(text='Searching...')
        run_in_background(self, lambda: pc.search_feeds(term), self._show)

    def _show(self, results, error):
        self.search_button.state(['!disabled'])
        self.tree.delete(*self.tree.get_children())
        if error:
            self.info.configure(text='Search failed: {}'.format(error))
            return
        self.results = results
        self.info.configure(text='{} podcast(s) found. Pick one and click "Use this podcast".'.format(len(results))
                            if results else 'Nothing found. Try another name.')
        for i, r in enumerate(results):
            self.tree.insert('', 'end', iid=str(i), text=r['name'], values=(r['author'],))
        if results:
            self.tree.selection_set('0')

    def ok(self):
        selection = self.tree.selection()
        if selection:
            self.result = self.results[int(selection[0])]
            self.destroy()


class RuleDialog(Dialog):
    def __init__(self, parent, rule=None):
        super().__init__(parent, 'Folder rule')
        rule = rule or {'name': '', 'match': []}
        ttk.Label(self.body, text='Folder name (without number; the number is added automatically)').pack(anchor='w')
        self.name = ttk.Entry(self.body, width=60)
        self.name.insert(0, rule['name'])
        self.name.pack(fill='x', pady=(2, 10))
        ttk.Label(self.body, text='Patterns, one per line (e.g. "^Shadowrun" for titles that start with '
                                  'Shadowrun)').pack(anchor='w')
        self.patterns = tk.Text(self.body, width=60, height=8, wrap='none', relief='flat', font=mono_font())
        style_text(self.patterns)
        self.patterns.insert('1.0', '\n'.join(rule['match']))
        self.patterns.pack(fill='both', pady=(2, 0))
        self.add_buttons()
        self.name.focus_set()

    def ok(self):
        rule = {'name': self.name.get().strip(),
                'match': [line.strip() for line in self.patterns.get('1.0', 'end').splitlines() if line.strip()]}
        error = pc.validate_podcast({'name': 'x', 'feed': 'http://x', 'sessions': [rule]}, [])
        if error:
            self.error(error)
            return
        self.result = rule
        self.destroy()


class PodcastDialog(Dialog):
    """Adds or edits a watch list entry: name (= folder), feed, whether it is updated, and folder rules."""

    def __init__(self, parent, podcast, others):
        super().__init__(parent, 'Edit podcast' if podcast else 'Add podcast', resizable=True)
        self.others = others
        podcast = copy.deepcopy(podcast) if podcast else {'name': '', 'feed': '', 'enabled': True, 'sessions': []}
        self.rules = podcast['sessions']

        form = ttk.Frame(self.body)
        form.pack(fill='x')
        form.columnconfigure(1, weight=1)
        ttk.Label(form, text='Name').grid(row=0, column=0, sticky='w', padx=(0, 10))
        self.name = ttk.Entry(form, width=50)
        self.name.insert(0, podcast['name'])
        self.name.grid(row=0, column=1, columnspan=3, sticky='ew')
        ttk.Label(form, text='Also the name of its folder in the archive folder.', foreground='gray'
                  ).grid(row=1, column=1, columnspan=3, sticky='w', pady=(2, 10))
        ttk.Label(form, text='RSS feed').grid(row=2, column=0, sticky='w', padx=(0, 10))
        self.feed = ttk.Entry(form, width=50)
        self.feed.insert(0, podcast['feed'])
        self.feed.grid(row=2, column=1, sticky='ew')
        ttk.Button(form, text='Find...', command=self.on_find).grid(row=2, column=2, padx=(8, 0))
        self.test_button = ttk.Button(form, text='Test', command=self.on_test)
        self.test_button.grid(row=2, column=3, padx=(8, 0))
        self.test_result = ttk.Label(form, text='', foreground='gray')
        self.test_result.grid(row=3, column=1, columnspan=3, sticky='w', pady=(2, 10))
        self.enabled = tk.BooleanVar(value=podcast['enabled'])
        ttk.Checkbutton(form, text='Include when checking for new episodes', variable=self.enabled
                        ).grid(row=4, column=1, columnspan=3, sticky='w')

        self.add_buttons()
        rules = ttk.LabelFrame(self.body, text='Folder rules', padding=(14, 10))
        rules.pack(fill='both', expand=True, pady=(14, 0))
        ttk.Label(rules, text=RULES_HELP, wraplength=640, foreground='gray').pack(anchor='w', pady=(0, 8))
        side = ttk.Frame(rules)
        side.pack(side='right', fill='y', padx=(10, 0))
        for text, command in (('Add...', self.on_add_rule), ('Edit...', self.on_edit_rule),
                              ('Remove', self.on_remove_rule), ('Move up', lambda: self.on_move_rule(-1)),
                              ('Move down', lambda: self.on_move_rule(1))):
            ttk.Button(side, text=text, command=command).pack(fill='x', pady=(0, 6))
        frame, self.tree = scrolled_tree(rules, ('patterns',), 9)
        frame.pack(fill='both', expand=True)
        self.tree.configure(show='headings tree')
        self.tree.heading('#0', text='Folder', anchor='w')
        self.tree.heading('patterns', text='Patterns', anchor='w')
        self.tree.column('#0', width=220)
        self.tree.column('patterns', width=420)
        self.tree.bind('<Double-1>', lambda e: self.on_edit_rule())
        self._fill_rules()
        self.name.focus_set()

    def _fill_rules(self, select=None):
        self.tree.delete(*self.tree.get_children())
        for i, rule in enumerate(self.rules):
            self.tree.insert('', 'end', iid=str(i), text=rule['name'], values=('   '.join(rule['match']),))
        if select is not None and 0 <= select < len(self.rules):
            self.tree.selection_set(str(select))
            self.tree.see(str(select))

    def _selected_rule(self):
        selection = self.tree.selection()
        return int(selection[0]) if selection else None

    def on_add_rule(self):
        rule = RuleDialog(self).show()
        if rule:
            self.rules.append(rule)
            self._fill_rules(len(self.rules) - 1)

    def on_edit_rule(self):
        i = self._selected_rule()
        if i is None:
            return
        rule = RuleDialog(self, self.rules[i]).show()
        if rule:
            self.rules[i] = rule
            self._fill_rules(i)

    def on_remove_rule(self):
        i = self._selected_rule()
        if i is not None:
            del self.rules[i]
            self._fill_rules(min(i, len(self.rules) - 1))

    def on_move_rule(self, step):
        i = self._selected_rule()
        if i is None or not 0 <= i + step < len(self.rules):
            return
        self.rules[i], self.rules[i + step] = self.rules[i + step], self.rules[i]
        self._fill_rules(i + step)

    def on_find(self):
        found = FeedSearchDialog(self, self.name.get().strip()).show()
        if found:
            self.feed.delete(0, 'end')
            self.feed.insert(0, found['feed'])
            if not self.name.get().strip():
                self.name.insert(0, pc.safe_name(found['name']))
            self.test_result.configure(text='Feed of "{}" by {}.'.format(found['name'], found['author']))

    def on_test(self):
        url = self.feed.get().strip()
        if not url:
            return
        self.test_button.state(['disabled'])
        self.test_result.configure(text='Reading the feed...')
        run_in_background(self, lambda: pc.probe_feed(url), self._show_test)

    def _show_test(self, result, error):
        self.test_button.state(['!disabled'])
        if error:
            self.test_result.configure(text='Could not read the feed: {}'.format(error))
            return
        title, count, newest = result
        self.test_result.configure(text='"{}": {} episodes, newest: {}'.format(title, count, newest))
        if not self.name.get().strip() and title:
            self.name.insert(0, pc.safe_name(title))

    def ok(self):
        podcast = pc.normalize_podcast({'name': self.name.get(), 'feed': self.feed.get(),
                                        'enabled': self.enabled.get(), 'sessions': self.rules})
        error = pc.validate_podcast(podcast, self.others)
        if error:
            self.error(error)
            return
        self.result = podcast
        self.destroy()


class PodcastsTab(TaskTab):
    title = 'Podcasts'

    def __init__(self, parent, app):
        super().__init__(parent, app)
        self.engine = pc.Engine(log=self.log_cb, status=self.status_cb, progress=self.progress_cb)
        self.cfg = pc.load_config()
        self.plan = None
        self._loading = False
        self._build()
        self._loading = True
        self.archive_dir.set(self.cfg['archive_dir'])
        self._loading = False
        self._fill_watch_list()
        self.set_busy(False)

    def cancel_task(self):
        self.engine.cancel()

    # --- layout -----------------------------------------------------------------------------------

    def _build(self):
        ttk.Label(self, text='Archives every episode of the podcasts on your watch list into numbered folders.'
                  ).pack(anchor='w')

        settings = ttk.LabelFrame(self, text='Settings', padding=(14, 10))
        settings.pack(fill='x', pady=(12, 12))
        settings.columnconfigure(1, weight=1)
        ttk.Label(settings, text='Archive folder').grid(row=0, column=0, sticky='w', padx=(0, 10))
        self.archive_dir = tk.StringVar()
        self.archive_entry = ttk.Entry(settings, textvariable=self.archive_dir)
        self.archive_entry.grid(row=0, column=1, sticky='ew')
        self.browse_button = ttk.Button(settings, text='Browse...', command=self.on_browse)
        self.browse_button.grid(row=0, column=2, padx=(8, 0))
        ttk.Button(settings, text='Open', command=lambda: open_folder(self.archive_dir.get().strip())
                   ).grid(row=0, column=3, padx=(8, 0))
        self.archive_dir.trace_add('write', lambda *_: self._on_archive_dir_changed())

        watch = ttk.LabelFrame(self, text='Watch list', padding=(14, 10))
        watch.pack(fill='x', pady=(0, 12))
        side = ttk.Frame(watch)
        side.pack(side='right', fill='y', padx=(10, 0))
        self.list_buttons = []
        for text, command in (('Add...', self.on_add), ('Edit...', self.on_edit), ('Remove', self.on_remove),
                              ('Import...', self.on_import)):
            button = ttk.Button(side, text=text, command=command)
            button.pack(fill='x', pady=(0, 6))
            self.list_buttons.append(button)
        frame, self.watch_tree = scrolled_tree(watch, ('update', 'rules', 'feed'), 5)
        frame.pack(fill='both', expand=True)
        self.watch_tree.heading('#0', text='Podcast', anchor='w')
        self.watch_tree.heading('update', text='Check', anchor='center')
        self.watch_tree.heading('rules', text='Folder rules', anchor='center')
        self.watch_tree.heading('feed', text='Feed', anchor='w')
        self.watch_tree.column('#0', width=220, stretch=False)
        self.watch_tree.column('update', width=70, stretch=False, anchor='center')
        self.watch_tree.column('rules', width=110, stretch=False, anchor='center')
        self.watch_tree.column('feed', width=400)
        self.watch_tree.bind('<Double-1>', self._on_watch_double_click)
        ttk.Label(watch, text='Double-click a podcast to edit it, or its check mark to skip it when checking.',
                  foreground='gray').pack(anchor='w', pady=(6, 0))

        buttons = self.build_status_bar(self)
        self.cancel_button = ttk.Button(buttons, text='Cancel', command=self.on_cancel)
        self.cancel_button.pack(side='right')
        self.apply_button = ttk.Button(buttons, text='Download new episodes', style='Accent.TButton',
                                       command=self.on_apply)
        self.apply_button.pack(side='right', padx=(0, 8))
        self.check_button = ttk.Button(buttons, text='Check for new episodes', command=self.on_check)
        self.check_button.pack(side='right', padx=(0, 8))

        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill='both', expand=True)
        changes = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(changes, text='New episodes')
        self.summary = ttk.Label(changes, text='Click "Check for new episodes" to compare the feeds with the '
                                               'archive folder.')
        self.summary.pack(anchor='w', pady=(0, 8))
        frame, self.tree = scrolled_tree(changes, ('info', 'size'), 10)
        frame.pack(fill='both', expand=True)
        self.tree.heading('#0', text='Podcast / folder / episode', anchor='w')
        self.tree.heading('info', text='', anchor='w')
        self.tree.heading('size', text='Size', anchor='e')
        self.tree.column('#0', stretch=True, width=560)
        self.tree.column('info', stretch=False, width=220)
        self.tree.column('size', stretch=False, width=110, anchor='e')
        log = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(log, text='Log')
        self.log_view = LogView(log)
        self.log_view.pack(fill='both', expand=True)

    # --- watch list -------------------------------------------------------------------------------

    def _fill_watch_list(self, select=None):
        tree = self.watch_tree
        tree.delete(*tree.get_children())
        for i, p in enumerate(self.cfg['podcasts']):
            tree.insert('', 'end', iid=str(i), text=p['name'],
                        values=('✔' if p['enabled'] else '–', len(p['sessions']) or '–', p['feed']))
        if select is not None and 0 <= select < len(self.cfg['podcasts']):
            tree.selection_set(str(select))
            tree.see(str(select))

    def _selected(self):
        selection = self.watch_tree.selection()
        return int(selection[0]) if selection else None

    def _save_config(self, select=None):
        pc.save_config(self.cfg)
        self._fill_watch_list(select)
        if self.plan is not None:
            self._clear_plan('The watch list changed. Click "Check for new episodes" again.')

    def _on_watch_double_click(self, event):
        row = self.watch_tree.identify_row(event.y)
        if not row or self.busy:
            return
        if self.watch_tree.identify_column(event.x) == '#1':
            podcast = self.cfg['podcasts'][int(row)]
            podcast['enabled'] = not podcast['enabled']
            self._save_config(int(row))
        else:
            self.watch_tree.selection_set(row)
            self.on_edit()

    def on_add(self):
        podcast = PodcastDialog(self, None, self.cfg['podcasts']).show()
        if podcast:
            self.cfg['podcasts'].append(podcast)
            self._save_config(len(self.cfg['podcasts']) - 1)

    def on_edit(self):
        i = self._selected()
        if i is None:
            return
        old = self.cfg['podcasts'][i]
        others = self.cfg['podcasts'][:i] + self.cfg['podcasts'][i + 1:]
        podcast = PodcastDialog(self, old, others).show()
        if not podcast:
            return
        if podcast['name'] != old['name']:
            pc.rename_state(old['name'], podcast['name'])
            old_dir, new_dir = pc.podcast_dir(self.cfg, old), pc.podcast_dir(self.cfg, podcast)
            if os.path.isdir(old_dir) and not os.path.exists(new_dir) and messagebox.askyesno(
                    APP_NAME, 'Rename the folder "{}" to "{}" too?\n\nOtherwise its episodes are downloaded '
                              'again into a new folder.'.format(old_dir, new_dir), parent=self):
                try:
                    os.rename(old_dir, new_dir)
                except OSError as e:
                    messagebox.showerror(APP_NAME, 'Could not rename the folder: {}'.format(e), parent=self)
        self.cfg['podcasts'][i] = podcast
        self._save_config(i)

    def on_remove(self):
        i = self._selected()
        if i is None:
            return
        podcast = self.cfg['podcasts'][i]
        if not messagebox.askyesno(APP_NAME, 'Remove "{}" from the watch list?\n\nEpisodes already downloaded stay '
                                             'in {}.'.format(podcast['name'], pc.podcast_dir(self.cfg, podcast)),
                                   parent=self):
            return
        del self.cfg['podcasts'][i]
        pc.forget_state(podcast['name'])
        self._save_config(min(i, len(self.cfg['podcasts']) - 1))

    def on_import(self):
        path = filedialog.askopenfilename(parent=self, title='Import podcasts.json',
                                          filetypes=[('Podcast list', '*.json'), ('All files', '*')])
        if not path:
            return
        try:
            added, skipped = pc.import_legacy_config(path, self.cfg)
        except (OSError, ValueError) as e:
            messagebox.showerror(APP_NAME, 'Could not read {}: {}'.format(path, e), parent=self)
            return
        self._loading = True
        self.archive_dir.set(self.cfg['archive_dir'])
        self._loading = False
        self._save_config()
        lines = ['Added {} podcast(s){}'.format(len(added), ': ' + ', '.join(added) if added else '.')]
        if skipped:
            lines.append('\nSkipped:\n' + '\n'.join(skipped))
        messagebox.showinfo(APP_NAME, '\n'.join(lines), parent=self)

    # --- settings ---------------------------------------------------------------------------------

    def on_browse(self):
        folder = filedialog.askdirectory(parent=self, initialdir=self.archive_dir.get() or None,
                                         title='Choose the archive folder')
        if folder:
            self.archive_dir.set(os.path.normpath(folder))

    def _on_archive_dir_changed(self):
        if self._loading:
            return
        self.cfg['archive_dir'] = self.archive_dir.get().strip()
        pc.save_config(self.cfg)
        if self.plan is not None:
            self._clear_plan('The archive folder changed. Click "Check for new episodes" again.')

    # --- actions ----------------------------------------------------------------------------------

    def on_check(self):
        if not self.cfg['archive_dir']:
            messagebox.showerror(APP_NAME, 'Please choose an archive folder.', parent=self)
            return
        if not any(p['enabled'] for p in self.cfg['podcasts']):
            messagebox.showinfo(APP_NAME, 'Add a podcast to the watch list first (or turn on checking for one).',
                                parent=self)
            return
        cfg = copy.deepcopy(self.cfg)
        self._clear_plan('Checking...')
        self.run_task(lambda: self.engine.check(cfg), on_done=lambda plan: self._show_plan(plan, cfg))

    def on_apply(self):
        plan = self.plan
        if plan is None or plan.empty:
            return
        lines = []
        if plan.new:
            lines.append('Download {} episode(s), {} into\n{}.'.format(
                len(plan.new), format_size(plan.new_size) or 'size unknown', plan.archive_dir))
        if plan.move_count:
            lines.append('Move {} episode(s) to the folder their rule now gives them.'.format(plan.move_count))
        if not messagebox.askyesno(APP_NAME, '\n'.join(lines), parent=self):
            return
        cfg = self.plan_cfg
        self.run_task(lambda: self.engine.apply(cfg, plan), on_done=self._after_apply)

    # --- results ----------------------------------------------------------------------------------

    def _clear_plan(self, summary):
        self.plan = None
        self.tree.delete(*self.tree.get_children())
        self.summary.configure(text=summary)
        self.set_busy(self.busy)

    def _show_plan(self, plan, cfg):
        self.plan, self.plan_cfg = plan, cfg
        tree = self.tree
        tree.delete(*tree.get_children())
        for check in plan.checks:
            name = check.podcast['name']
            if check.error:
                tree.insert('', 'end', text=name, values=('Error: ' + check.error, ''))
                continue
            info = '{} new of {}'.format(len(check.new), len(check.episodes)) if check.new else \
                'up to date ({} episodes)'.format(len(check.episodes))
            if check.moves:
                info += ', {} to move'.format(len(check.moves))
            node = tree.insert('', 'end', text=name, open=bool(check.new or check.moves),
                               values=(info, format_size(check.new_size) if check.new_size else ''))
            new_ids = {id(e) for e in check.new}
            moves = {id(e): old for e, old in check.moves}
            folders = {}
            for ep in check.episodes:
                folders.setdefault(os.path.dirname(ep.rel), []).append(ep)
            for folder in sorted(folders):
                eps = folders[folder]
                pending = [e for e in eps if id(e) in new_ids or id(e) in moves]
                count_new = sum(1 for e in eps if id(e) in new_ids)
                folder_info = '{} episodes'.format(len(eps)) + (', {} new'.format(count_new) if count_new else '')
                if folder in check.new_folders:
                    folder_info += ', new folder'
                new_size = sum(e.length for e in eps if id(e) in new_ids)
                fnode = tree.insert(node, 'end', text=folder, open=bool(pending),
                                    values=(folder_info, format_size(new_size) if new_size else ''))
                for ep in pending:
                    if id(ep) in moves:
                        what = 'move from ' + os.path.dirname(moves[id(ep)])
                    else:
                        what = 'new, {:%Y-%m-%d}'.format(ep.date)
                    tree.insert(fnode, 'end', text=os.path.basename(ep.rel), values=(what, format_size(ep.length)))

        errors = len(plan.errors)
        if plan.empty:
            text = 'Everything is up to date.'
            self.set_status('Up to date.')
        else:
            text = '{} new episode(s) ({}).'.format(len(plan.new), format_size(plan.new_size) or 'size unknown')
            if plan.move_count:
                text += ' {} episode(s) to move.'.format(plan.move_count)
            text += ' Review the list, then click "Download new episodes".'
            self.set_status('New episodes found. Nothing has been downloaded yet.')
        if errors:
            text += ' {} feed(s) could not be read, see the log.'.format(errors)
        self.summary.configure(text=text)
        self.notebook.select(0)
        self.set_busy(False)

    def _after_apply(self, result):
        downloaded, moved, failed = result
        self._clear_plan('Download complete. Click "Check for new episodes" to check again.')
        text = 'Downloaded {} episode(s){}.'.format(downloaded, ', moved {}'.format(moved) if moved else '')
        self.set_status(text)
        if failed:
            messagebox.showwarning(APP_NAME, '{}\n\n{} episode(s) failed and will be tried again next time:\n{}'
                                   .format(text, len(failed), '\n'.join(failed[:10])), parent=self)
        else:
            messagebox.showinfo(APP_NAME, text, parent=self)

    def set_busy(self, busy):
        idle = 'disabled' if busy else '!disabled'
        for widget in (self.check_button, self.archive_entry, self.browse_button, *self.list_buttons):
            widget.state([idle])
        can_apply = not busy and self.plan is not None and not self.plan.empty
        self.apply_button.state(['!disabled' if can_apply else 'disabled'])
        self.cancel_button.state(['!disabled' if busy else 'disabled'])
