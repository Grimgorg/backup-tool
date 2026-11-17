"""Pieces shared by the tabs: dialogs (Dialog, MessageScreen, PathScreen) and TaskPane, the base class of a tab that
runs one background task at a time.

Workers report through thread-safe callbacks (log_cb, status_cb, progress_cb, ask_cb); their events are queued and
applied on the UI thread every 50 ms, so a busy worker never blocks the screen."""
import os
import queue
import threading

from rich.text import Text
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, ProgressBar, RichLog, Static, TabbedContent, TabPane

from vaultsync.common import Cancelled, format_size

MAX_LOG_LINES = 20000


def log_text(line):
    """A log line, colored by what it reports."""
    lower = line.lower()
    if line.startswith('> '):
        style = 'dodger_blue2'
    elif 'error' in lower or 'traceback' in lower or 'fatal' in lower or 'fehler' in lower:
        style = 'red'
    elif 'warn' in lower:
        style = 'dark_orange'
    else:
        style = ''
    return Text(line, style=style)


def tree_label(name, info, size=0, style='dim'):
    """A label for a plan tree: name, then info and size in a quieter style."""
    label = Text(name)
    if info:
        label.append('   ' + info, style=style)
    if size:
        label.append('   ' + format_size(size), style='bold')
    return label


# --- dialogs --------------------------------------------------------------------------------------

class Dialog(ModalScreen):
    """A centered box over the app. Subclasses compose a Vertical with the classes they need: .title, .hint,
    .error, .row (a line of widgets) and .buttons (right-aligned buttons, see compose_buttons)."""

    DEFAULT_CSS = '''
    Dialog { align: center middle; }
    Dialog > Vertical {
        width: 96; max-width: 95%; height: auto; max-height: 90%; padding: 1 2; overflow-y: auto;
        border: thick $primary; background: $surface;
    }
    Dialog .title { text-style: bold; margin-bottom: 1; }
    Dialog .hint { color: $text-muted; margin-bottom: 1; }
    Dialog .error { color: $error; }
    Dialog .row { height: auto; }
    Dialog .row Input { width: 1fr; }
    Dialog .row Button { margin-left: 1; }
    Dialog .buttons { height: auto; margin-top: 1; align-horizontal: right; }
    Dialog .buttons Button { margin-left: 1; }
    '''
    BINDINGS = [Binding('escape', 'cancel', 'Cancel')]

    @staticmethod
    def compose_buttons(ok_label='OK', cancel_label='Cancel'):
        with Horizontal(classes='buttons'):
            yield Button(ok_label, variant='primary', id='ok')
            if cancel_label:
                yield Button(cancel_label, id='cancel')

    # Subclasses handle their other buttons in their own on_button_pressed; Textual calls this one too (handlers
    # run for every class in the MRO), so OK and Cancel are only handled here.
    def on_button_pressed(self, event):
        if event.button.id == 'ok':
            event.stop()
            self.action_ok()
        elif event.button.id == 'cancel':
            event.stop()
            self.action_cancel()

    def action_ok(self):
        self.dismiss(True)

    def action_cancel(self):
        self.dismiss(None)

    def show_error(self, text):
        self.query_one('#error', Label).update(Text(text))


class MessageScreen(Dialog):
    """A message with OK, or a question with Yes/No. Dismisses with True for OK/Yes."""

    DEFAULT_CSS = '''
    MessageScreen > Vertical { width: 80; }
    MessageScreen VerticalScroll { height: auto; max-height: 20; }
    '''
    BINDINGS = [Binding('y', 'ok', 'Yes', show=False), Binding('n', 'cancel', 'No', show=False)]

    def __init__(self, text, title='', question=False):
        super().__init__()
        self.text, self.title_text, self.question = text, title, question

    def compose(self):
        with Vertical():
            if self.title_text:
                yield Label(self.title_text, classes='title')
            with VerticalScroll():
                yield Static(Text(self.text))
            yield from self.compose_buttons('Yes' if self.question else 'OK', 'No' if self.question else None)

    def on_mount(self):
        self.query_one('#ok', Button).focus()

    def action_cancel(self):
        self.dismiss(False)


class PathScreen(Dialog):
    """Asks for a folder or file path (terminals have no file picker). Dismisses with the path or None."""

    def __init__(self, title, hint='', value='', must_exist='dir'):
        super().__init__()
        self.title_text, self.hint, self.value, self.must_exist = title, hint, value, must_exist

    def compose(self):
        with Vertical():
            yield Label(self.title_text, classes='title')
            if self.hint:
                yield Label(self.hint, classes='hint')
            yield Input(self.value, id='path')
            yield Label('', classes='error', id='error')
            yield from self.compose_buttons()

    def on_input_submitted(self, event):
        event.stop()
        self.action_ok()

    def action_ok(self):
        path = os.path.expanduser(self.query_one('#path', Input).value.strip().strip('"'))
        if not path:
            self.show_error('Please enter a path.')
        elif self.must_exist == 'dir' and not os.path.isdir(path):
            self.show_error('There is no folder {}.'.format(path))
        elif self.must_exist == 'file' and not os.path.isfile(path):
            self.show_error('There is no file {}.'.format(path))
        else:
            self.dismiss(os.path.normpath(path))


# --- task pane ------------------------------------------------------------------------------------

class TaskPane(Vertical):
    """A tab that runs one background task at a time.

    Engines get self.log_cb, self.status_cb, self.progress_cb and self.ask_cb, which are safe to call from any
    thread; start work with run_task(func, on_done). Subclasses compose their settings, then compose_results()
    and compose_status_bar(). Widgets with the class "idle-only" are disabled while a task runs. Key bindings
    for the actions in IDLE_ACTIONS are greyed out while busy, "run" also while can_run() is False and "cancel"
    while idle."""

    DEFAULT_CSS = '''
    TaskPane { padding: 1 1 0 1; }
    TaskPane .intro { color: $text-muted; margin-bottom: 1; }
    TaskPane .hint { color: $text-muted; }
    TaskPane .field { height: auto; }
    TaskPane .field > Label { width: 18; padding: 1 1 0 0; }
    TaskPane .field > Input { width: 1fr; }
    TaskPane .field > Input.short { width: 30; }
    TaskPane .field > Checkbox { width: auto; }
    TaskPane .section { text-style: bold; margin-top: 1; }
    TaskPane #results { height: 1fr; margin-top: 1; }
    TaskPane #summary { margin-bottom: 1; }
    TaskPane .status-bar { height: 1; margin-top: 1; }
    TaskPane .status-bar Label { width: 1fr; }
    TaskPane .status-bar ProgressBar { width: 40; }
    '''
    # Esc leaves a text field, so the tab's one-letter keys work again.
    BINDINGS = [Binding('escape', 'leave_field', 'Leave field', show=False)]
    IDLE_ACTIONS = set()

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.events = queue.Queue()
        self.worker = None

    def on_mount(self):
        # Textual runs the on_mount of every class in the MRO, so subclasses don't call this one.
        self.set_interval(0.05, self._poll_events)

    # --- layout -----------------------------------------------------------------------------------

    @staticmethod
    def compose_field(label, *widgets):
        with Horizontal(classes='field'):
            yield Label(label)
            yield from widgets

    @staticmethod
    def compose_results(title, *widgets):
        """The results tabs: [title] with widgets (a summary, a plan tree, ...) and the log."""
        with TabbedContent(id='results'):
            with TabPane(title, id='plan-tab'):
                yield from widgets
            with TabPane('Log', id='log-tab'):
                yield RichLog(max_lines=MAX_LOG_LINES, id='log')

    @staticmethod
    def compose_status_bar():
        with Horizontal(classes='status-bar'):
            yield Label('Ready.', id='status')
            yield ProgressBar(total=1.0, show_eta=False, show_percentage=False, id='progress')

    @property
    def log_view(self):
        return self.query_one('#log', RichLog)

    def focus_default(self):
        """Focuses the pane's main widget when its tab is shown; subclasses pick it."""

    def action_leave_field(self):
        self.focus_default()

    def show_results(self, tab):
        """Shows 'plan-tab' or 'log-tab'."""
        self.query_one('#results', TabbedContent).active = tab

    def action_switch_results(self):
        results = self.query_one('#results', TabbedContent)
        results.active = 'log-tab' if results.active == 'plan-tab' else 'plan-tab'

    def message(self, text, title=''):
        self.app.push_screen(MessageScreen(text, title))

    def confirm(self, text, then):
        """Asks a Yes/No question and calls then() on Yes."""
        self.app.push_screen(MessageScreen(text, question=True), lambda yes: then() if yes else None)

    # --- keys -------------------------------------------------------------------------------------

    def can_run(self):
        """Whether the "run" action (the tab's main action after a check) has something to do."""
        return False

    def check_action(self, action, parameters):
        if action in self.IDLE_ACTIONS:
            return None if self.busy else True  # None: shown, but greyed out
        if action == 'run':
            return True if not self.busy and self.can_run() else None
        if action == 'cancel':
            return True if self.busy else None
        return True

    # --- worker -----------------------------------------------------------------------------------

    # Callbacks for engines; safe to call from any thread.
    def log_cb(self, line):
        self.events.put(('log', line))

    def status_cb(self, text):
        self.events.put(('status', text))

    def progress_cb(self, value):
        self.events.put(('progress', value))

    def ask_cb(self, prompt, context):
        done = threading.Event()
        holder = {}
        self.events.put(('ask', prompt, context, holder, done))
        done.wait()
        return holder.get('answer')

    def ask_screen(self, prompt, context):
        """Returns a screen that dismisses with the answer to a question from the worker; override to answer
        ask_cb(). None answers None."""
        return None

    @property
    def busy(self):
        return self.worker is not None and self.worker.is_alive()

    def cancel_task(self):
        """Asks the running task to stop; subclasses forward this to their engine."""

    def action_cancel(self):
        if self.busy:
            self.set_status('Cancelling...')
            self.cancel_task()

    def set_busy(self, busy):
        for widget in self.query('.idle-only'):
            widget.disabled = busy
        self.refresh_bindings()

    def run_task(self, task, on_done):
        def target():
            try:
                result = task()
                self.events.put(('done', on_done, result, None))
            except Cancelled:
                self.events.put(('done', None, None, 'Cancelled. Nothing was deleted.'))
            except Exception as e:  # shown to the user, details are in the log
                self.events.put(('log', 'ERROR: {}'.format(e)))
                self.events.put(('done', None, None, str(e) or e.__class__.__name__))

        self.worker = threading.Thread(target=target, daemon=True)
        self.worker.start()
        self.set_busy(True)

    def _poll_events(self):
        log_lines = []
        try:
            while True:
                event = self.events.get_nowait()
                kind = event[0]
                if kind == 'log':
                    log_lines.append(event[1])
                    continue
                self.write_log(log_lines)
                log_lines = []
                if kind == 'status':
                    self.set_status(event[1])
                elif kind == 'progress':
                    self.set_progress(event[1])
                elif kind == 'ask':
                    self._ask(*event[1:])
                elif kind == 'done':
                    self._finish(*event[1:])
        except queue.Empty:
            pass
        self.write_log(log_lines)

    def write_log(self, lines):
        for line in lines:
            self.log_view.write(log_text(line))

    def _ask(self, prompt, context, holder, done):
        screen = self.ask_screen(prompt, context)
        if screen is None:
            done.set()
            return

        def answered(answer):
            holder['answer'] = answer
            done.set()

        self.app.push_screen(screen, answered)

    def _finish(self, on_done, result, error):
        self.worker = None
        self.set_busy(False)
        self.set_progress(0)
        if self.app.closing:
            self.app.task_finished()
            return
        if error:
            self.set_status(error)
            if not error.startswith('Cancelled'):
                self.message(error, 'Error')
        elif on_done:
            on_done(result)

    def set_status(self, text):
        self.query_one('#status', Label).update(Text(text))

    def set_progress(self, value):
        bar = self.query_one('#progress', ProgressBar)
        if value is None:
            bar.update(total=None)  # indeterminate (animated)
        else:
            bar.update(total=1.0, progress=value)
