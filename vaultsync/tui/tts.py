"""Tabletop Simulator tab: lists the TTS mods, "Check" counts what is already cached, "Back up" downloads every asset
and writes one zip per mod into the backup folder, "Restore" puts backups back into TTS."""
import copy
import os

from rich.text import Text
from textual import work
from textual.binding import Binding
from textual.widgets import Checkbox, DataTable, Input, Label, Static

from vaultsync.common import format_size, open_folder
from vaultsync.tts import engine as tts
from vaultsync.tts.mods import default_mods_dir
from vaultsync.tui.common import PathScreen, TaskPane

INTRO = ('Backs up Tabletop Simulator mods with every image, model, sound and PDF they use, so they still work '
         'when the original links are gone.')
EXTRAS_HELP = ('Arkham Horror LCG (SCE): also back up the packs of its in-game download menu. They are saved as '
               'Saved Objects (in TTS: Objects > Saved Objects > {}) and can be large.'
               .format(tts.arkham.SAVED_OBJECTS_FOLDER))


class TtsPane(TaskPane):
    DEFAULT_CSS = '''
    TtsPane #languages-label { width: auto; padding: 1 1 0 4; }
    TtsPane .field .hint { padding: 1 0 0 2; }
    TtsPane #mods { height: 1fr; }
    '''
    BINDINGS = [
        Binding('c', 'check', 'Check'),
        Binding('d', 'run', 'Back up'),
        Binding('x', 'cancel', 'Cancel'),
        Binding('space', 'toggle', 'Include/skip'),
        Binding('a', 'include_all', 'All'),
        Binding('n', 'include_none', 'None'),
        Binding('r', 'restore', 'Restore'),
        Binding('f5', 'reload', 'Reload'),
        Binding('l', 'switch_results', 'Log/mods'),
        Binding('o', 'open_folder', 'Open folder', show=False),
    ]
    IDLE_ACTIONS = {'check', 'toggle', 'include_all', 'include_none', 'restore', 'reload'}

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.engine = tts.Engine(log=self.log_cb, status=self.status_cb, progress=self.progress_cb)
        self.cfg = tts.load_config()
        self.entries = []
        self.checks = {}
        self.results = {}
        self._reload_timer = None
        self._list_request = 0

    def cancel_task(self):
        self.engine.cancel()

    def can_run(self):
        return bool(self._included())

    # --- layout -----------------------------------------------------------------------------------

    def compose(self):
        c = self.cfg
        yield Static(INTRO, classes='intro')
        yield from self.compose_field('TTS Mods folder', Input(
            c['mods_dir'], placeholder='Empty: the TTS default, {}'.format(default_mods_dir()), id='mods_dir',
            classes='idle-only'))
        yield from self.compose_field('Backup folder', Input(
            c['backup_dir'], placeholder='One zip per mod; mods in TTS folders go into the same subfolder',
            id='backup_dir', classes='idle-only'))
        yield from self.compose_field(
            'Extras', Checkbox('Back up downloadable content', c['extras'], id='extras', classes='idle-only'),
            Label('Language packs', id='languages-label'),
            Input(c['arkham_languages'], id='arkham_languages', classes='idle-only short'),
            Label('German,French / all / none', classes='hint'))
        yield Static(EXTRAS_HELP, classes='hint')
        yield from self.compose_results('Mods', Label('', id='summary'),
                                        DataTable(cursor_type='row', zebra_stripes=True, id='mods'))
        yield from self.compose_status_bar()

    def on_mount(self):
        self.query_one('#mods', DataTable).add_columns('Back up', 'Mod', 'TTS folder', 'Assets', 'Status')
        self.reload_mods()

    def focus_default(self):
        self.query_one('#mods', DataTable).focus()

    def _folder(self, key):
        value = self.cfg[key].strip()
        if key == 'mods_dir' and not value:
            return str(default_mods_dir())
        return os.path.expanduser(value)

    def action_open_folder(self):
        open_folder(self._folder('backup_dir'))

    # --- settings ---------------------------------------------------------------------------------

    def _on_settings_changed(self):
        values = {key: self.query_one('#' + key, Input).value.strip()
                  for key in ('mods_dir', 'backup_dir', 'arkham_languages')}
        values['extras'] = self.query_one('#extras', Checkbox).value
        if all(self.cfg[key] == value for key, value in values.items()):
            return  # also the initial values set in compose()
        old_mods_dir = self.cfg['mods_dir']
        self.cfg.update(values)
        tts.save_config(self.cfg)
        self.checks, self.results = {}, {}
        if self.cfg['mods_dir'] != old_mods_dir:
            # Wait until typing pauses instead of reading the folder on every key press.
            if self._reload_timer:
                self._reload_timer.stop()
            self._reload_timer = self.set_timer(0.6, self.reload_mods)
        else:
            self._fill_table()

    def on_input_changed(self, event):
        self._on_settings_changed()

    def on_checkbox_changed(self, event):
        self._on_settings_changed()

    # --- mod list ---------------------------------------------------------------------------------

    def action_reload(self):
        self.reload_mods()

    def reload_mods(self):
        self._reload_timer = None
        self._list_request += 1
        self.query_one('#summary', Label).update('Reading the mod list...')
        self._read_mods(copy.deepcopy(self.cfg), self._list_request)

    @work(thread=True, group='mods')
    def _read_mods(self, cfg, request):
        try:
            entries, error = tts.list_mods(cfg), None
        except Exception as e:  # shown in the summary
            entries, error = [], e
        self.app.call_from_thread(self._show_mods, entries, error, request)

    def _show_mods(self, entries, error, request):
        if request != self._list_request:
            return  # an older read finished after a newer one was started
        self.entries = entries
        self.checks, self.results = {}, {}
        self._fill_table()
        if error:
            self.query_one('#summary', Label).update(Text('Could not read the mods: {}'.format(error)))

    def _included(self):
        skip = set(self.cfg['skip'])
        return [e for e in self.entries if e.key not in skip]

    def _fill_table(self, select=None):
        table = self.query_one('#mods', DataTable)
        row = table.cursor_row if select is None else select
        table.clear()
        skip = set(self.cfg['skip'])
        for entry in self.entries:
            assets, status = self._describe(entry)
            table.add_row('–' if entry.key in skip else '✔', Text(entry.name), Text(entry.folder), assets,
                          Text(status, style='red' if status.startswith('Error') else ''), key=entry.key)
        if self.entries:
            table.move_cursor(row=min(row, len(self.entries) - 1))
            self.query_one('#summary', Label).update(
                '{} mods, {} included. Press c to see what is missing, d to download it and write the '
                'backups.'.format(len(self.entries), len(self._included())))
        else:
            self.query_one('#summary', Label).update(Text('No mods found in {}.'.format(
                os.path.join(self._folder('mods_dir'), 'Workshop'))))
        self.refresh_bindings()

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

    def on_data_table_row_selected(self, event):
        if not self.busy:
            self.action_toggle()

    def action_toggle(self):
        table = self.query_one('#mods', DataTable)
        if not table.row_count:
            return
        key = self.entries[table.cursor_row].key
        skip = self.cfg['skip']
        if key in skip:
            skip.remove(key)
        else:
            skip.append(key)
        tts.save_config(self.cfg)
        self._fill_table()

    def _include_all(self, include):
        self.cfg['skip'] = [] if include else [e.key for e in self.entries]
        tts.save_config(self.cfg)
        self._fill_table()

    def action_include_all(self):
        self._include_all(True)

    def action_include_none(self):
        self._include_all(False)

    # --- actions ----------------------------------------------------------------------------------

    def action_check(self):
        entries = self._included()
        if not entries:
            self.message('Include at least one mod first (Space, or a for all).')
            return
        cfg = copy.deepcopy(self.cfg)
        self.results = {}
        self.run_task(lambda: self.engine.check(cfg, entries), on_done=self._after_check)

    def _after_check(self, checks):
        self.checks = {c.entry.key: c for c in checks}
        self._fill_table()
        to_download = sum(c.to_download for c in checks)
        text = '{} asset(s) to download.'.format(to_download) if to_download else 'All assets are cached.'
        if any(c.arkham for c in checks):
            text += ' Downloadable packs are fetched during the backup.'
        self.set_status(text + ' Nothing has been downloaded yet.')

    def action_run(self):
        entries = self._included()
        if not entries:
            return
        if not self.cfg['backup_dir']:
            self.message('Please enter a backup folder.')
            return
        text = ('Back up {} mod(s) into\n{}?\n\nMissing assets are downloaded into the TTS folders first. Existing '
                'backups of these mods are replaced.'.format(len(entries), self.cfg['backup_dir']))
        if self.cfg['extras']:
            text += ('\n\nArkham Horror (SCE) downloadable content can be tens of GB; the backup needs about the '
                     'same space again.')
        cfg = copy.deepcopy(self.cfg)

        def start():
            self.show_results('log-tab')
            self.run_task(lambda: self.engine.backup(cfg, entries), on_done=self._after_backup)

        self.confirm(text, start)

    def _after_backup(self, results):
        self.results = {r.entry.key: r for r in results}
        self._fill_table()
        self.show_results('plan-tab')
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
        self.message('\n\n'.join([text] + details))

    def action_restore(self):
        target = self._folder('mods_dir')

        def chosen(folder):
            if not folder:
                return
            cfg = copy.deepcopy(self.cfg)

            def start():
                self.show_results('log-tab')
                self.run_task(lambda: self.engine.restore(cfg, folder), on_done=self._after_restore)

            self.confirm('Restore every backup in\n{}\ninto\n{}?\n\nFiles that already exist are kept.'.format(
                folder, target), start)

        self.app.push_screen(PathScreen('Restore backups', 'The folder with the backup zips.',
                                        self._folder('backup_dir')), chosen)

    def _after_restore(self, result):
        restored, written, problems = result
        text = 'Restored {} backup(s), {} file(s) written.'.format(len(restored), written)
        self.set_status(text)
        self.reload_mods()
        if problems:
            text += '\n\nSkipped:\n' + '\n'.join(problems[:10])
        self.message(text)
