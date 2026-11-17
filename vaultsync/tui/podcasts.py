"""Podcasts tab: a watch list of podcasts whose episodes are archived into numbered folders. "Check" reads the feeds
and lists what would be downloaded, "Download" fetches it."""
import copy
import os

from rich.text import Text
from textual import work
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Checkbox, DataTable, Input, Label, Static, TextArea, Tree

from vaultsync.common import format_size, open_folder
from vaultsync.podcasts import engine as pc
from vaultsync.tui.common import Dialog, PathScreen, TaskPane, tree_label

RULES_HELP = ('Each episode goes into the folder of the first rule with a pattern that matches its title. Patterns '
              'are regular expressions and ignore upper/lower case; "^" means "the title starts with". Episodes '
              'that match no rule get a folder named after the start of their title.')


class FeedSearchScreen(Dialog):
    """Finds a podcast's RSS feed by name in the Apple Podcasts directory. Dismisses with the result or None."""

    DEFAULT_CSS = 'FeedSearchScreen DataTable { height: 14; }'

    def __init__(self, term=''):
        super().__init__()
        self.term = term
        self.results = []

    def compose(self):
        with Vertical():
            yield Label('Find a podcast', classes='title')
            yield Input(self.term, placeholder='Podcast name, then Enter', id='term')
            yield Label('Results come from the Apple Podcasts directory and work in any podcast app.',
                        classes='hint', id='info')
            yield DataTable(cursor_type='row', id='results')
            yield Label('Enter on a result uses it. Esc goes back.', classes='hint')

    def on_mount(self):
        self.query_one('#results', DataTable).add_columns('Podcast', 'By')
        if self.term:
            self.search(self.term)

    def on_input_submitted(self, event):
        term = event.value.strip()
        if term:
            self.search(term)

    @work(thread=True, exclusive=True)
    def search(self, term):
        self.app.call_from_thread(self.query_one('#info', Label).update, 'Searching...')
        try:
            results, error = pc.search_feeds(term), None
        except Exception as e:  # shown in the dialog
            results, error = [], e
        self.app.call_from_thread(self._show, results, error)

    def _show(self, results, error):
        info = self.query_one('#info', Label)
        table = self.query_one('#results', DataTable)
        table.clear()
        if error:
            info.update(Text('Search failed: {}'.format(error)))
            return
        self.results = results
        for r in results:
            table.add_row(Text(r['name']), Text(r['author']))
        if results:
            info.update('{} podcast(s) found.'.format(len(results)))
            table.focus()
        else:
            info.update('Nothing found. Try another name.')

    def on_data_table_row_selected(self, event):
        self.dismiss(self.results[event.cursor_row])


class RuleScreen(Dialog):
    """Edits one folder rule. Dismisses with {'name', 'match'} or None."""

    DEFAULT_CSS = 'RuleScreen TextArea { height: 8; }'
    BINDINGS = [Binding('ctrl+s', 'ok', 'Save', priority=True)]

    def __init__(self, rule=None):
        super().__init__()
        self.rule = rule or {'name': '', 'match': []}

    def compose(self):
        with Vertical():
            yield Label('Folder rule', classes='title')
            yield Label('Folder name (without number; the number is added automatically)')
            yield Input(self.rule['name'], id='name')
            yield Label('Patterns, one per line (e.g. "^Shadowrun" for titles that start with Shadowrun)')
            yield TextArea('\n'.join(self.rule['match']), id='patterns')
            yield Label('', classes='error', id='error')
            yield from self.compose_buttons('Save (Ctrl+S)')

    def action_ok(self):
        rule = {'name': self.query_one('#name', Input).value.strip(),
                'match': [line.strip() for line in self.query_one('#patterns', TextArea).text.splitlines()
                          if line.strip()]}
        error = pc.validate_podcast({'name': 'x', 'feed': 'http://x', 'sessions': [rule]}, [])
        if error:
            self.show_error(error)
            return
        self.dismiss(rule)


class RulesTable(DataTable):
    """The folder rules in PodcastScreen; its keys work while it has the focus."""

    BINDINGS = [Binding('a', 'screen.add_rule', 'Add rule'), Binding('e', 'screen.edit_rule', 'Edit rule'),
                Binding('delete', 'screen.remove_rule', 'Remove rule'),
                Binding('shift+up', 'screen.move_rule(-1)', 'Move up'),
                Binding('shift+down', 'screen.move_rule(1)', 'Move down')]


class PodcastScreen(Dialog):
    """Adds or edits a watch list entry: name (= folder), feed, whether it is checked, and folder rules.
    Dismisses with the podcast dict or None."""

    DEFAULT_CSS = 'PodcastScreen RulesTable { height: 8; }'
    BINDINGS = [Binding('ctrl+s', 'ok', 'Save', priority=True),
                Binding('ctrl+f', 'find', 'Find feed', priority=True),
                Binding('ctrl+t', 'test', 'Test feed', priority=True)]

    def __init__(self, podcast, others):
        super().__init__()
        self.editing = podcast is not None
        self.podcast = copy.deepcopy(podcast) if podcast else {'name': '', 'feed': '', 'enabled': True,
                                                                 'sessions': []}
        self.rules = self.podcast['sessions']
        self.others = others

    def compose(self):
        p = self.podcast
        with Vertical():
            yield Label('Edit podcast' if self.editing else 'Add podcast', classes='title')
            yield Label('Name (also the name of its folder in the archive folder)')
            yield Input(p['name'], id='name')
            yield Label('RSS feed')
            with Horizontal(classes='row'):
                yield Input(p['feed'], id='feed')
                yield Button('Find (Ctrl+F)', id='find')
                yield Button('Test (Ctrl+T)', id='test')
            yield Label('', classes='hint', id='test-result')
            yield Checkbox('Include when checking for new episodes', p['enabled'], id='enabled')
            yield Label('Folder rules', classes='title')
            yield Static(RULES_HELP, classes='hint')
            yield RulesTable(cursor_type='row', id='rules')
            yield Label('In the list: a add, e or Enter edit, Del remove, Shift+Up/Down move.', classes='hint')
            yield Label('', classes='error', id='error')
            yield from self.compose_buttons('Save (Ctrl+S)')

    def on_mount(self):
        self.query_one('#rules', RulesTable).add_columns('Folder', 'Patterns')
        self._fill_rules()
        self.query_one('#name', Input).focus()

    def on_button_pressed(self, event):
        if event.button.id == 'find':
            self.action_find()
        elif event.button.id == 'test':
            self.action_test()

    def _value(self, widget_id):
        return self.query_one('#' + widget_id, Input).value.strip()

    # --- rules ------------------------------------------------------------------------------------

    def _fill_rules(self, select=None):
        table = self.query_one('#rules', RulesTable)
        table.clear()
        for rule in self.rules:
            table.add_row(Text(rule['name']), Text('   '.join(rule['match'])))
        if select is not None and 0 <= select < len(self.rules):
            table.move_cursor(row=select)

    def _selected_rule(self):
        table = self.query_one('#rules', RulesTable)
        return table.cursor_row if table.row_count else None

    def on_data_table_row_selected(self, event):
        self.action_edit_rule()

    def action_add_rule(self):
        def added(rule):
            if rule:
                self.rules.append(rule)
                self._fill_rules(len(self.rules) - 1)

        self.app.push_screen(RuleScreen(), added)

    def action_edit_rule(self):
        i = self._selected_rule()
        if i is None:
            return

        def edited(rule):
            if rule:
                self.rules[i] = rule
                self._fill_rules(i)

        self.app.push_screen(RuleScreen(self.rules[i]), edited)

    def action_remove_rule(self):
        i = self._selected_rule()
        if i is not None:
            del self.rules[i]
            self._fill_rules(min(i, len(self.rules) - 1))

    def action_move_rule(self, step):
        i = self._selected_rule()
        if i is None or not 0 <= i + step < len(self.rules):
            return
        self.rules[i], self.rules[i + step] = self.rules[i + step], self.rules[i]
        self._fill_rules(i + step)

    # --- feed -------------------------------------------------------------------------------------

    def action_find(self):
        def found(result):
            if result:
                self.query_one('#feed', Input).value = result['feed']
                if not self._value('name'):
                    self.query_one('#name', Input).value = pc.safe_name(result['name'])
                self.query_one('#test-result', Label).update(
                    Text('Feed of "{}" by {}.'.format(result['name'], result['author'])))

        self.app.push_screen(FeedSearchScreen(self._value('name')), found)

    def action_test(self):
        url = self._value('feed')
        if url:
            self.query_one('#test-result', Label).update('Reading the feed...')
            self.test_feed(url)

    @work(thread=True, exclusive=True)
    def test_feed(self, url):
        try:
            result, error = pc.probe_feed(url), None
        except Exception as e:  # shown in the dialog
            result, error = None, e
        self.app.call_from_thread(self._show_test, result, error)

    def _show_test(self, result, error):
        label = self.query_one('#test-result', Label)
        if error:
            label.update(Text('Could not read the feed: {}'.format(error)))
            return
        title, count, newest = result
        label.update(Text('"{}": {} episodes, newest: {}'.format(title, count, newest)))
        if not self._value('name') and title:
            self.query_one('#name', Input).value = pc.safe_name(title)

    def action_ok(self):
        podcast = pc.normalize_podcast({'name': self._value('name'), 'feed': self._value('feed'),
                                        'enabled': self.query_one('#enabled', Checkbox).value,
                                        'sessions': self.rules})
        error = pc.validate_podcast(podcast, self.others)
        if error:
            self.show_error(error)
            return
        self.dismiss(podcast)


class PodcastsPane(TaskPane):
    DEFAULT_CSS = 'PodcastsPane #watch { height: 9; }'
    BINDINGS = [
        Binding('c', 'check', 'Check'),
        Binding('d', 'run', 'Download'),
        Binding('x', 'cancel', 'Cancel'),
        Binding('a', 'add', 'Add'),
        Binding('e', 'edit', 'Edit'),
        Binding('delete', 'remove', 'Remove'),
        Binding('space', 'toggle', 'On/off'),
        Binding('i', 'import', 'Import'),
        Binding('l', 'switch_results', 'Log/plan'),
        Binding('o', 'open_folder', 'Open folder', show=False),
    ]
    IDLE_ACTIONS = {'check', 'add', 'edit', 'remove', 'toggle', 'import'}

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.engine = pc.Engine(log=self.log_cb, status=self.status_cb, progress=self.progress_cb)
        self.cfg = pc.load_config()
        self.plan = self.plan_cfg = None

    def cancel_task(self):
        self.engine.cancel()

    def can_run(self):
        return self.plan is not None and not self.plan.empty

    # --- layout -----------------------------------------------------------------------------------

    def compose(self):
        yield Static('Archives every episode of the podcasts on your watch list into numbered folders.',
                     classes='intro')
        yield from self.compose_field('Archive folder', Input(
            self.cfg['archive_dir'], placeholder='Folder that gets one subfolder per podcast', id='archive-dir',
            classes='idle-only'))
        yield Label('Watch list', classes='section')
        yield DataTable(cursor_type='row', zebra_stripes=True, id='watch')
        yield from self.compose_results(
            'New episodes', Label('Press c to compare the feeds with the archive folder.', id='summary'),
            Tree('Plan', id='plan'))
        yield from self.compose_status_bar()

    def on_mount(self):
        self.query_one('#plan', Tree).show_root = False
        self.query_one('#watch', DataTable).add_columns('On', 'Podcast', 'Rules', 'Feed')
        self._fill_watch_list(0)

    def focus_default(self):
        self.query_one('#watch', DataTable).focus()

    # --- watch list -------------------------------------------------------------------------------

    def _fill_watch_list(self, select=None):
        table = self.query_one('#watch', DataTable)
        table.clear()
        for p in self.cfg['podcasts']:
            table.add_row('✔' if p['enabled'] else '–', Text(p['name']), len(p['sessions']) or '–', Text(p['feed']))
        if select is not None and 0 <= select < len(self.cfg['podcasts']):
            table.move_cursor(row=select)

    def _selected(self):
        table = self.query_one('#watch', DataTable)
        return table.cursor_row if table.row_count else None

    def _save_config(self, select=None):
        pc.save_config(self.cfg)
        self._fill_watch_list(select)
        if self.plan is not None:
            self._clear_plan('The watch list changed. Press c to check again.')

    def on_data_table_row_selected(self, event):
        if event.data_table.id == 'watch' and not self.busy:
            self.action_edit()

    def action_toggle(self):
        i = self._selected()
        if i is not None:
            podcast = self.cfg['podcasts'][i]
            podcast['enabled'] = not podcast['enabled']
            self._save_config(i)

    def action_add(self):
        def added(podcast):
            if podcast:
                self.cfg['podcasts'].append(podcast)
                self._save_config(len(self.cfg['podcasts']) - 1)

        self.app.push_screen(PodcastScreen(None, self.cfg['podcasts']), added)

    def action_edit(self):
        i = self._selected()
        if i is None:
            return
        old = self.cfg['podcasts'][i]

        def edited(podcast):
            if not podcast:
                return
            if podcast['name'] != old['name']:
                pc.rename_state(old['name'], podcast['name'])
                old_dir, new_dir = pc.podcast_dir(self.cfg, old), pc.podcast_dir(self.cfg, podcast)
                if os.path.isdir(old_dir) and not os.path.exists(new_dir):
                    self.confirm('Rename the folder "{}" to "{}" too?\n\nOtherwise its episodes are downloaded again '
                                 'into a new folder.'.format(old_dir, new_dir),
                                 lambda: self._rename_folder(old_dir, new_dir))
            self.cfg['podcasts'][i] = podcast
            self._save_config(i)

        others = self.cfg['podcasts'][:i] + self.cfg['podcasts'][i + 1:]
        self.app.push_screen(PodcastScreen(old, others), edited)

    def _rename_folder(self, old_dir, new_dir):
        try:
            os.rename(old_dir, new_dir)
        except OSError as e:
            self.message('Could not rename the folder: {}'.format(e), 'Error')

    def action_remove(self):
        i = self._selected()
        if i is None:
            return
        podcast = self.cfg['podcasts'][i]

        def remove():
            del self.cfg['podcasts'][i]
            pc.forget_state(podcast['name'])
            self._save_config(min(i, len(self.cfg['podcasts']) - 1))

        self.confirm('Remove "{}" from the watch list?\n\nEpisodes already downloaded stay in {}.'.format(
            podcast['name'], pc.podcast_dir(self.cfg, podcast)), remove)

    def action_import(self):
        def chosen(path):
            if not path:
                return
            try:
                added, skipped = pc.import_legacy_config(path, self.cfg)
            except (OSError, ValueError) as e:
                self.message('Could not read {}: {}'.format(path, e), 'Error')
                return
            self.query_one('#archive-dir', Input).value = self.cfg['archive_dir']
            self._save_config()
            lines = ['Added {} podcast(s){}'.format(len(added), ': ' + ', '.join(added) if added else '.')]
            if skipped:
                lines.append('\nSkipped:\n' + '\n'.join(skipped))
            self.message('\n'.join(lines))

        self.app.push_screen(PathScreen('Import podcasts.json', 'The podcast list of the older archive.py script.',
                                        must_exist='file'), chosen)

    # --- settings ---------------------------------------------------------------------------------

    def on_input_changed(self, event):
        if event.input.id != 'archive-dir':
            return
        value = event.value.strip()
        if value == self.cfg['archive_dir']:
            return  # also the initial value set in compose()
        self.cfg['archive_dir'] = value
        pc.save_config(self.cfg)
        if self.plan is not None:
            self._clear_plan('The archive folder changed. Press c to check again.')

    def on_input_submitted(self, event):
        if event.input.id == 'archive-dir':
            self.focus_default()

    def action_open_folder(self):
        open_folder(self.cfg['archive_dir'])

    # --- actions ----------------------------------------------------------------------------------

    def action_check(self):
        if not self.cfg['archive_dir']:
            self.message('Please enter an archive folder.')
            return
        if not any(p['enabled'] for p in self.cfg['podcasts']):
            self.message('Add a podcast to the watch list first (a), or turn on checking for one (Space).')
            return
        cfg = copy.deepcopy(self.cfg)
        self._clear_plan('Checking...')
        self.run_task(lambda: self.engine.check(cfg), on_done=lambda plan: self._show_plan(plan, cfg))

    def action_run(self):
        plan = self.plan
        if plan is None or plan.empty:
            return
        lines = []
        if plan.new:
            lines.append('Download {} episode(s), {} into\n{}.'.format(
                len(plan.new), format_size(plan.new_size) or 'size unknown', plan.archive_dir))
        if plan.move_count:
            lines.append('Move {} episode(s) to the folder their rule now gives them.'.format(plan.move_count))
        cfg = self.plan_cfg
        self.confirm('\n'.join(lines), lambda: self.run_task(lambda: self.engine.apply(cfg, plan),
                                                             on_done=self._after_apply))

    # --- results ----------------------------------------------------------------------------------

    def _clear_plan(self, summary):
        self.plan = None
        self.query_one('#plan', Tree).clear()
        self.query_one('#summary', Label).update(summary)
        self.refresh_bindings()

    def _show_plan(self, plan, cfg):
        self.plan, self.plan_cfg = plan, cfg
        tree = self.query_one('#plan', Tree)
        tree.clear()
        for check in plan.checks:
            name = check.podcast['name']
            if check.error:
                tree.root.add_leaf(tree_label(name, 'Error: ' + check.error, style='red'))
                continue
            info = '{} new of {}'.format(len(check.new), len(check.episodes)) if check.new else \
                'up to date ({} episodes)'.format(len(check.episodes))
            if check.moves:
                info += ', {} to move'.format(len(check.moves))
            node = tree.root.add(tree_label(name, info, check.new_size), expand=bool(check.new or check.moves))
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
                if not pending:
                    node.add_leaf(tree_label(folder, folder_info, new_size))
                    continue
                fnode = node.add(tree_label(folder, folder_info, new_size), expand=True)
                for ep in pending:
                    if id(ep) in moves:
                        what = 'move from ' + os.path.dirname(moves[id(ep)])
                    else:
                        what = 'new, {:%Y-%m-%d}'.format(ep.date)
                    fnode.add_leaf(tree_label(os.path.basename(ep.rel), what, ep.length))

        if plan.empty:
            text = 'Everything is up to date.'
            self.set_status('Up to date.')
        else:
            text = '{} new episode(s) ({}).'.format(len(plan.new), format_size(plan.new_size) or 'size unknown')
            if plan.move_count:
                text += ' {} episode(s) to move.'.format(plan.move_count)
            text += ' Review the list, then press d to download.'
            self.set_status('New episodes found. Nothing has been downloaded yet.')
        if plan.errors:
            text += ' {} feed(s) could not be read, see the log (l).'.format(len(plan.errors))
        self.query_one('#summary', Label).update(text)
        self.show_results('plan-tab')
        self.refresh_bindings()

    def _after_apply(self, result):
        downloaded, moved, failed = result
        self._clear_plan('Download complete. Press c to check again.')
        text = 'Downloaded {} episode(s){}.'.format(downloaded, ', moved {}'.format(moved) if moved else '')
        self.set_status(text)
        if failed:
            text += '\n\n{} episode(s) failed and will be tried again next time:\n{}'.format(
                len(failed), '\n'.join(failed[:10]))
        self.message(text)

