"""GOG tab: keeps a local folder an exact mirror of a GOG library (offline installers and extras), using gogrepoc.
"Check" previews what would be downloaded and deleted, "Apply" carries it out."""
import re
import webbrowser

from rich.text import Text
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, Checkbox, Input, Label, Static, Tree

from vaultsync.common import format_size, open_folder
from vaultsync.gog import engine as gog
from vaultsync.tui.common import Dialog, TaskPane, tree_label

OPTIONS = (('extras', 'Include extras (manuals, soundtracks, artwork...)'), ('full_scan', 'Full library scan'),
           ('verify', 'Verify files after download'), ('sort', 'Sort into Installer / Extras folders'))


class LoginScreen(Dialog):
    """Asks for the GOG account. Dismisses with (user, password) or None."""

    DEFAULT_CSS = 'LoginScreen > Vertical { width: 60; }'

    def compose(self):
        with Vertical():
            yield Label('Log in to GOG', classes='title')
            yield Label('Use your GOG account. Google and Discord sign-ins are not supported.', classes='hint')
            yield Label('E-mail or username')
            yield Input(id='user')
            yield Label('Password')
            yield Input(password=True, id='password')
            yield Label('', classes='error', id='error')
            yield from self.compose_buttons('Log in')

    def on_input_submitted(self, event):
        if event.input.id == 'user':
            self.query_one('#password', Input).focus()
        else:
            self.action_ok()

    def action_ok(self):
        user, password = self.query_one('#user', Input).value.strip(), self.query_one('#password', Input).value
        if user and password:
            self.dismiss((user, password))
        else:
            self.show_error('Please enter both.')


class PromptScreen(Dialog):
    """Shows a question gogrepoc asks on its console (two-step code, sign-in URL, ...). Dismisses with the answer,
    or None to cancel the task."""

    DEFAULT_CSS = 'PromptScreen VerticalScroll { height: auto; max-height: 12; margin-bottom: 1; }'

    def __init__(self, prompt, context):
        super().__init__()
        self.prompt, self.context = prompt, context
        urls = re.findall(r'https?://\S+', context)
        self.url = urls[-1] if urls else None

    def compose(self):
        with Vertical():
            yield Label('GOG asks for input', classes='title')
            yield Label('gogrepoc needs an answer to continue:', classes='hint')
            with VerticalScroll():
                yield Static(Text(self.context))
            if self.url:
                yield Button('Open sign-in page in browser', id='open')
            yield Label(Text(self.prompt))
            yield Input(id='answer')
            yield from self.compose_buttons()

    def on_mount(self):
        self.query_one('#answer', Input).focus()

    def on_button_pressed(self, event):
        if event.button.id == 'open':
            webbrowser.open(self.url)

    def on_input_submitted(self, event):
        self.action_ok()

    def action_ok(self):
        self.dismiss(self.query_one('#answer', Input).value)


class GogPane(TaskPane):
    DEFAULT_CSS = '''
    GogPane #top { height: auto; }
    GogPane #top .intro { width: 1fr; }
    GogPane #account { width: auto; }
    GogPane #os-label { width: auto; padding: 1 1 0 2; }
    GogPane .field .hint { padding: 1 0 0 2; }
    GogPane #options { height: auto; layout: grid; grid-size: 2; grid-rows: 3; }
    '''
    BINDINGS = [
        Binding('c', 'check', 'Check'),
        Binding('d', 'run', 'Apply'),
        Binding('x', 'cancel', 'Cancel'),
        Binding('i', 'login', 'Log in'),
        Binding('l', 'switch_results', 'Log/plan'),
        Binding('o', 'open_folder', 'Open folder', show=False),
    ]
    IDLE_ACTIONS = {'check', 'login'}

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.engine = gog.Engine(log=self.log_cb, status=self.status_cb, progress=self.progress_cb, ask=self.ask_cb)
        self.settings = gog.load_settings()
        self.plan = None

    def cancel_task(self):
        self.engine.cancel()

    def can_run(self):
        return self.plan is not None and not self.plan.empty

    def ask_screen(self, prompt, context):
        return PromptScreen(prompt, context)

    # --- layout -----------------------------------------------------------------------------------

    def compose(self):
        s = self.settings
        with Horizontal(id='top'):
            yield Static('Mirrors your GOG library (offline installers and extras) into a local folder.',
                         classes='intro')
            yield Label('', id='account')
        yield from self.compose_field('Output folder', Input(s['save_dir'], id='save_dir', classes='idle-only'))
        yield from self.compose_field(
            'Languages', Input(' '.join(s['langs']), id='langs', classes='idle-only short'),
            Label('Operating systems', id='os-label'), Input(' '.join(s['os']), id='os', classes='idle-only short'),
            Label('e.g. "en de"', classes='hint'))
        with Horizontal(id='options'):
            for key, text in OPTIONS:
                yield Checkbox(text, s[key], id=key, classes='idle-only')
        yield from self.compose_results(
            'Changes', Label('Press c to compare your GOG library with the output folder.', id='summary'),
            Tree('Plan', id='plan'))
        yield from self.compose_status_bar()

    def on_mount(self):
        self.query_one('#plan', Tree).show_root = False
        self._refresh_account()
        # A sync that ended without cleaning up (crash, power loss) leaves its moved-aside files in !orphaned.
        if gog.Engine.needs_recovery(self.settings):
            settings = dict(self.settings)
            self.call_after_refresh(self.run_task, lambda: self.engine.recover(settings), lambda _: None)

    def focus_default(self):
        self.query_one('#plan', Tree).focus()

    def _refresh_account(self):
        self.query_one('#account', Label).update('Logged in to GOG' if gog.is_logged_in() else
                                                 'Not logged in (press i)')

    # --- settings ---------------------------------------------------------------------------------

    def _read_settings(self):
        s = dict(self.settings)
        s['save_dir'] = self.query_one('#save_dir', Input).value.strip()
        s['langs'] = self.query_one('#langs', Input).value.split()
        s['os'] = self.query_one('#os', Input).value.split()
        for key, _ in OPTIONS:
            s[key] = self.query_one('#' + key, Checkbox).value
        return s

    def _on_settings_changed(self):
        settings = self._read_settings()
        if settings == self.settings:
            return  # also the initial values set in compose()
        self.settings = settings
        gog.save_settings(settings)
        if self.plan is not None:
            # The preview no longer matches what Apply would do.
            self._clear_plan('Settings changed. Press c to check again.')

    def on_input_changed(self, event):
        self._on_settings_changed()

    def on_checkbox_changed(self, event):
        self._on_settings_changed()

    def _validated_settings(self):
        s = self.settings
        if not s['save_dir']:
            self.message('Please enter an output folder.')
            return None
        if not s['langs'] or not s['os']:
            self.message('Please enter at least one language and one operating system.')
            return None
        return dict(s)

    def action_open_folder(self):
        open_folder(self.settings['save_dir'])

    # --- actions ----------------------------------------------------------------------------------

    def action_login(self):
        def entered(credentials):
            if credentials:
                self.run_task(lambda: self.engine.login(*credentials), on_done=lambda _: self._refresh_account())

        self.app.push_screen(LoginScreen(), entered)

    def action_check(self):
        if not gog.is_logged_in():
            self.message('Please log in to GOG first (press i).')
            return
        settings = self._validated_settings()
        if settings:
            self._clear_plan('Checking...')
            self.run_task(lambda: self.engine.check(settings), on_done=self._show_plan)

    def action_run(self):
        plan = self.plan
        if plan is None or plan.empty:
            return
        settings = self._validated_settings()
        if not settings:
            return
        lines = []
        if plan.downloads:
            lines.append('Download {} file(s), {}.'.format(len(plan.downloads), format_size(plan.download_size)))
        if plan.deletions:
            lines.append('Delete {} item(s), {}.'.format(len(plan.deletions), format_size(plan.deletion_size)))
        lines.append('\nOld files are only deleted once all downloads are complete.')
        self.confirm('\n'.join(lines), lambda: self.run_task(lambda: self.engine.apply(settings, plan),
                                                             on_done=self._after_apply))

    # --- results ----------------------------------------------------------------------------------

    def _clear_plan(self, summary):
        self.plan = None
        self.query_one('#plan', Tree).clear()
        self.query_one('#summary', Label).update(summary)
        self.refresh_bindings()

    def _show_plan(self, plan):
        self.plan = plan
        self.query_one('#plan', Tree).clear()
        if plan.empty:
            self.query_one('#summary', Label).update('Everything is up to date. The output folder mirrors your GOG '
                                                     'library.')
            self.set_status('Up to date.')
        else:
            self.query_one('#summary', Label).update(
                '{} file(s) to download ({}), {} item(s) to delete ({}). Review the list, then press d to '
                'apply.'.format(len(plan.downloads), format_size(plan.download_size), len(plan.deletions),
                                format_size(plan.deletion_size)))
            self.set_status('Changes found. Nothing has been changed yet.')
            self._add_section('Download', plan.downloads, plan.download_size)
            self._add_section('Delete', plan.deletions, plan.deletion_size)
        self.show_results('plan-tab')
        self.refresh_bindings()

    def _add_section(self, title, items, total):
        tree = self.query_one('#plan', Tree)
        section = tree.root.add(tree_label(title, '{} file(s)'.format(len(items)), total), expand=True)
        games = {}
        for item in items:
            games.setdefault(item.game, []).append(item)
        for game, game_items in sorted(games.items()):
            node = section.add(tree_label(game, '', sum(i.size or 0 for i in game_items)), expand=True)
            for item in game_items:
                node.add_leaf(tree_label(item.file, '', item.size or 0))

    def _after_apply(self, _):
        self._clear_plan('Sync complete. Press c to compare again.')
        self.message('Sync complete. The output folder now mirrors your GOG library.')
