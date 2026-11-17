"""Where Vault Sync keeps its user data: one folder next to the program, so the program and its data can be moved or
copied together.

  build         <folder of vault-sync(.exe)>/vault-sync-data
  from source   <repository>/vault-sync-data   (git-ignored)

Inside: gog/ (settings, login token, manifests), podcasts/ (watch list, folder numbering), tts/ (settings), app.json
and logs/. Setting VAULT_SYNC_HOME uses that folder instead (tests, CI).

Versions before 2.0 kept the data in the usual per-OS folders; migrate_system_folders() moves it here once.
"""
import os
import shutil
import sys

from vaultsync import APP_ID, APP_NAME, SOURCE_ROOT

DATA_FOLDER_NAME = 'vault-sync-data'


def program_dir():
    """The folder that holds the build, or the repository when running from source."""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return SOURCE_ROOT


def _home():
    override = os.environ.get('VAULT_SYNC_HOME')
    return os.path.abspath(override) if override else os.path.join(program_dir(), DATA_FOLDER_NAME)


HOME = _home()
LOG_DIR = os.path.join(HOME, 'logs')


def home_dir():
    """HOME, created if missing."""
    os.makedirs(HOME, exist_ok=True)
    return HOME


def log_dir():
    """LOG_DIR, created if missing."""
    os.makedirs(LOG_DIR, exist_ok=True)
    return LOG_DIR


def check_writable():
    """None if the data folder can be created and written, else a message for the user."""
    probe = os.path.join(HOME, '.write-test')
    try:
        os.makedirs(HOME, exist_ok=True)
        with open(probe, 'w', encoding='utf-8') as f:
            f.write('ok')
        os.remove(probe)
        return None
    except OSError as e:
        return ('{} keeps its data next to the program, in {}, but cannot write there ({}).\nMove the program to a '
                'folder you can write to, or set VAULT_SYNC_HOME to another folder.'.format(APP_NAME, HOME, e))


# --- data of versions before 2.0 ------------------------------------------------------------------

def _xdg(var, default):
    value = os.environ.get(var)
    # The XDG spec says relative paths are invalid and must be ignored.
    return value if value and os.path.isabs(value) else os.path.join(os.path.expanduser('~'), *default.split('/'))


def system_dirs(platform=None):
    """(settings, data, logs) folders that versions before 2.0 used on this OS."""
    platform = platform or sys.platform
    home = os.path.expanduser('~')
    if platform == 'win32':
        roaming = os.environ.get('APPDATA') or os.path.join(home, 'AppData', 'Roaming')
        local = os.environ.get('LOCALAPPDATA') or os.path.join(home, 'AppData', 'Local')
        return (os.path.join(roaming, APP_NAME), os.path.join(local, APP_NAME),
                os.path.join(local, APP_NAME, 'Logs'))
    if platform == 'darwin':
        support = os.path.join(home, 'Library', 'Application Support', APP_NAME)
        return support, support, os.path.join(home, 'Library', 'Logs', APP_NAME)
    return (os.path.join(_xdg('XDG_CONFIG_HOME', '.config'), APP_ID),
            os.path.join(_xdg('XDG_DATA_HOME', '.local/share'), APP_ID),
            os.path.join(_xdg('XDG_STATE_HOME', '.local/state'), APP_ID, 'logs'))


def _move_tree(src, dst):
    """Moves every file of src into dst (same relative paths), never replacing a file in dst. Then removes the
    folders of src that are empty. Returns (moved, kept): how many files were moved and how many stayed in src."""
    moved = kept = 0
    for root, _, files in os.walk(src):
        target_root = os.path.join(dst, os.path.relpath(root, src))
        for name in files:
            target = os.path.join(target_root, name)
            if os.path.lexists(target):
                kept += 1
                continue
            try:
                os.makedirs(target_root, exist_ok=True)
                shutil.move(os.path.join(root, name), target)
                moved += 1
            except OSError:
                kept += 1
    for root, _, _ in sorted(os.walk(src), key=lambda entry: -len(entry[0])):  # deepest folders first
        try:
            os.rmdir(root)
        except OSError:
            pass  # not empty
    return moved, kept


def migrate_system_folders(old_dirs=None):
    """Moves the data of versions before 2.0 from the per-OS folders into HOME, the first time this version runs
    (HOME doesn't exist yet). Not with VAULT_SYNC_HOME. Returns lines for the log; empty if there was nothing."""
    if old_dirs is None:
        if os.environ.get('VAULT_SYNC_HOME') or os.path.exists(HOME):
            return []
        old_dirs = system_dirs()
    settings, data, logs = old_dirs
    lines = []
    # Logs first: on Windows they sit inside the data folder and belong in HOME/logs, not HOME/Logs.
    for src, dst in ((logs, LOG_DIR), (data, HOME), (settings, HOME)):
        if not os.path.isdir(src):
            continue
        moved, kept = _move_tree(src, dst)
        if moved:
            lines.append('Moved {} file(s) of an earlier version from {} to {}.'.format(moved, src, dst))
        if kept:
            lines.append('Left {} file(s) in {} (already present here, or could not be moved).'.format(kept, src))
    return lines
