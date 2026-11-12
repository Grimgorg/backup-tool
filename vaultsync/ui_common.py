"""UI pieces shared by the sub-app tabs: theme and platform helpers, modal dialogs, the log view and TaskTab,
the base class of a tab that runs one background task at a time."""
import ctypes
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import font as tkfont
from tkinter import messagebox, ttk

import sv_ttk

from vaultsync import APP_NAME
from vaultsync.common import Cancelled, format_size  # noqa: F401  (format_size is re-exported for the tabs)

MAX_LOG_LINES = 20000


# --- platform helpers -----------------------------------------------------------------------------

def system_dark_mode():
    try:
        if sys.platform == 'win32':
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r'Software\Microsoft\Windows\CurrentVersion\Themes\Personalize') as key:
                return winreg.QueryValueEx(key, 'AppsUseLightTheme')[0] == 0
        if sys.platform == 'darwin':
            out = subprocess.run(['defaults', 'read', '-g', 'AppleInterfaceStyle'],
                                 capture_output=True, text=True, timeout=5).stdout
            return 'dark' in out.lower()
        out = subprocess.run(['gsettings', 'get', 'org.gnome.desktop.interface', 'color-scheme'],
                             capture_output=True, text=True, timeout=5).stdout
        return 'dark' in out.lower()
    except (OSError, subprocess.SubprocessError):
        return False


def set_dpi_awareness():
    if sys.platform == 'win32':
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass


def set_dark_title_bar(window, dark):
    if sys.platform != 'win32':
        return
    try:
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
        value = ctypes.c_int(1 if dark else 0)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(value), ctypes.sizeof(value))
    except (AttributeError, OSError):
        pass


def open_folder(path):
    if not os.path.isdir(path):
        return
    if sys.platform == 'win32':
        os.startfile(path)
    elif sys.platform == 'darwin':
        subprocess.Popen(['open', path])
    else:
        subprocess.Popen(['xdg-open', path])


def heading_font(size=20):
    f = tkfont.nametofont('TkDefaultFont').copy()
    f.configure(size=size, weight='bold')
    return f


def mono_font():
    return tkfont.nametofont('TkFixedFont')


def style_text(widget):
    # sv_ttk only styles ttk widgets; tk.Text needs the colors set by hand.
    if sv_ttk.get_theme() == 'dark':
        widget.configure(background='#1c1c1c', foreground='#e6e6e6', insertbackground='#e6e6e6',
                         selectbackground='#2f5c8f', highlightthickness=0, borderwidth=8)
    else:
        widget.configure(background='#fafafa', foreground='#1c1c1c', insertbackground='#1c1c1c',
                         highlightthickness=0, borderwidth=8)


def run_in_background(widget, func, on_done):
    """Runs func() in a thread and then calls on_done(result, error) on the UI thread, unless widget is gone."""
    outcome = {}

    def target():
        try:
            outcome['result'] = func()
        except Exception as e:  # handed to on_done
            outcome['error'] = e

    thread = threading.Thread(target=target, daemon=True)
    thread.start()

    def poll():
        try:
            if thread.is_alive():
                widget.after(100, poll)
            elif widget.winfo_exists():
                on_done(outcome.get('result'), outcome.get('error'))
        except tk.TclError:  # the dialog was closed meanwhile
            pass

    try:
        widget.after(100, poll)
    except tk.TclError:
        pass


# --- dialogs --------------------------------------------------------------------------------------

class Dialog(tk.Toplevel):
    """Modal dialog base: subclasses fill self.body and set self.result in ok()."""

    def __init__(self, parent, title, resizable=False):
        super().__init__(parent)
        self.withdraw()
        self.title(title)
        self.transient(parent.winfo_toplevel())
        self.resizable(resizable, resizable)
        self.result = None
        self.body = ttk.Frame(self, padding=20)
        self.body.pack(fill='both', expand=True)
        self.protocol('WM_DELETE_WINDOW', self.cancel)
        self.bind('<Escape>', lambda e: self.cancel())

    def add_buttons(self, ok_text='OK', parent=None):
        buttons = ttk.Frame(parent or self.body)
        buttons.pack(side='bottom', fill='x', pady=(16, 0))
        ttk.Button(buttons, text='Cancel', command=self.cancel).pack(side='right')
        ttk.Button(buttons, text=ok_text, style='Accent.TButton', command=self.ok).pack(side='right', padx=(0, 8))
        return buttons

    def bind_return(self):
        self.bind('<Return>', lambda e: self.ok())

    def show(self):
        self.update_idletasks()
        parent = self.master.winfo_toplevel()
        x = parent.winfo_rootx() + (parent.winfo_width() - self.winfo_reqwidth()) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - self.winfo_reqheight()) // 3
        self.geometry('+{}+{}'.format(max(x, 0), max(y, 0)))
        self.deiconify()
        set_dark_title_bar(self, sv_ttk.get_theme() == 'dark')
        try:
            self.wait_visibility()  # grabbing an unmapped window fails on X11
            self.grab_set()
        except tk.TclError:
            pass
        self.wait_window()
        return self.result

    def error(self, text):
        messagebox.showerror(APP_NAME, text, parent=self)

    def ok(self):
        self.destroy()

    def cancel(self):
        self.result = None
        self.destroy()


class LogView(ttk.Frame):
    """Read-only, colored log text with a scrollbar."""

    def __init__(self, parent):
        super().__init__(parent)
        self.text = tk.Text(self, wrap='none', font=mono_font(), relief='flat', state='disabled')
        style_text(self.text)
        scroll = ttk.Scrollbar(self, orient='vertical', command=self.text.yview)
        self.text.configure(yscrollcommand=scroll.set)
        self.text.pack(side='left', fill='both', expand=True)
        scroll.pack(side='right', fill='y')
        self.text.tag_configure('warning', foreground='#d08700')
        self.text.tag_configure('error', foreground='#e5484d')
        self.text.tag_configure('command', foreground='#3b82f6')

    def append(self, lines):
        t = self.text
        t.configure(state='normal')
        at_end = t.yview()[1] > 0.999
        for line in lines:
            lower = line.lower()
            tag = ()
            if line.startswith('> '):
                tag = ('command',)
            elif 'error' in lower or 'traceback' in lower or 'fatal' in lower or 'fehler' in lower:
                tag = ('error',)
            elif 'warn' in lower:
                tag = ('warning',)
            t.insert('end', line + '\n', tag)
        excess = int(t.index('end-1c').split('.')[0]) - MAX_LOG_LINES
        if excess > 0:
            t.delete('1.0', '{}.0'.format(excess + 1))
        if at_end:
            t.see('end')
        t.configure(state='disabled')


# --- task tab -------------------------------------------------------------------------------------

class TaskTab(ttk.Frame):
    """A tab that runs one background task at a time. Workers report through thread-safe callbacks
    (self.log_cb, self.status_cb, self.progress_cb, self.ask_cb) that are applied on the UI thread.
    Subclasses build their widgets, call build_status_bar() and implement set_busy(busy)."""

    title = ''

    def __init__(self, parent, app):
        super().__init__(parent, padding=(4, 12, 4, 0))
        self.app = app
        self.events = queue.Queue()
        self.worker = None
        self.log_view = None
        self.after(50, self._poll_events)

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

    def ask_dialog(self, prompt, context):
        """Shows a question from the worker; override to answer ask_cb()."""
        return None

    def build_status_bar(self, parent):
        """Packs the status line and progress bar at the bottom of parent; returns the frame on the right
        side for the tab's action buttons (pack them with side='right')."""
        bottom = ttk.Frame(parent)
        bottom.pack(side='bottom', fill='x', pady=(12, 0))
        buttons = ttk.Frame(bottom)
        buttons.pack(side='right')
        status_box = ttk.Frame(bottom)
        status_box.pack(side='left', fill='x', expand=True, padx=(0, 16))
        self.status = ttk.Label(status_box, text='Ready.')
        self.status.pack(anchor='w')
        self.progress = ttk.Progressbar(status_box, mode='determinate', maximum=1.0)
        self.progress.pack(fill='x', pady=(6, 0))
        return buttons

    @property
    def busy(self):
        return self.worker is not None and self.worker.is_alive()

    def cancel_task(self):
        """Asks the running task to stop; subclasses forward this to their engine."""

    def on_cancel(self):
        if self.busy:
            self.set_status('Cancelling...')
            self.cancel_task()

    def set_busy(self, busy):
        raise NotImplementedError

    def run_task(self, task, on_done):
        self.set_busy(True)

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
                    self.log_view.append(log_lines)
                    log_lines = []
                if kind == 'status':
                    self.set_status(event[1])
                elif kind == 'progress':
                    self.set_progress(event[1])
                elif kind == 'ask':
                    _, prompt, context, holder, done = event
                    holder['answer'] = self.ask_dialog(prompt, context)
                    done.set()
                elif kind == 'done':
                    self._finish(*event[1:])
        except queue.Empty:
            pass
        if log_lines:
            self.log_view.append(log_lines)
        self.after(50, self._poll_events)

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
                messagebox.showerror(APP_NAME, error, parent=self)
        elif on_done:
            on_done(result)

    def set_status(self, text):
        self.status.configure(text=text)

    def set_progress(self, value):
        if value is None:
            if str(self.progress.cget('mode')) != 'indeterminate':
                self.progress.configure(mode='indeterminate')
                self.progress.start(15)
        else:
            if str(self.progress.cget('mode')) != 'determinate':
                self.progress.stop()
                self.progress.configure(mode='determinate')
            self.progress.configure(value=value)
