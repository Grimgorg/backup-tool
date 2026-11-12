"""Where Vault Sync keeps its user data, following each operating system's conventions.

             settings (CONFIG_DIR)               data: login, manifests, state (DATA_DIR)   logs (LOG_DIR)
  Windows    %APPDATA%\\Vault Sync                %LOCALAPPDATA%\\Vault Sync                  %LOCALAPPDATA%\\Vault Sync\\Logs
  macOS      ~/Library/Application Support/Vault Sync (both)                                ~/Library/Logs/Vault Sync
  Linux      $XDG_CONFIG_HOME/vault-sync         $XDG_DATA_HOME/vault-sync                  $XDG_STATE_HOME/vault-sync/logs
             (~/.config/vault-sync)              (~/.local/share/vault-sync)                (~/.local/state/vault-sync/logs)

Setting VAULT_SYNC_HOME puts everything into that one folder instead (portable use, tests).
Each sub-app uses its own subfolder (gog/, podcasts/) in these folders.
"""
import os
import sys

from vaultsync import APP_ID, APP_NAME


def _xdg(var, default):
    value = os.environ.get(var)
    # The XDG spec says relative paths are invalid and must be ignored.
    return value if value and os.path.isabs(value) else os.path.join(os.path.expanduser('~'), *default.split('/'))


def _dirs():
    override = os.environ.get('VAULT_SYNC_HOME')
    if override:
        base = os.path.abspath(override)
        return os.path.join(base, 'config'), os.path.join(base, 'data'), os.path.join(base, 'logs')
    home = os.path.expanduser('~')
    if sys.platform == 'win32':
        roaming = os.environ.get('APPDATA') or os.path.join(home, 'AppData', 'Roaming')
        local = os.environ.get('LOCALAPPDATA') or os.path.join(home, 'AppData', 'Local')
        # Settings roam with the user profile; large or machine-specific data stays local.
        return (os.path.join(roaming, APP_NAME), os.path.join(local, APP_NAME),
                os.path.join(local, APP_NAME, 'Logs'))
    if sys.platform == 'darwin':
        support = os.path.join(home, 'Library', 'Application Support', APP_NAME)
        return support, support, os.path.join(home, 'Library', 'Logs', APP_NAME)
    return (os.path.join(_xdg('XDG_CONFIG_HOME', '.config'), APP_ID),
            os.path.join(_xdg('XDG_DATA_HOME', '.local/share'), APP_ID),
            os.path.join(_xdg('XDG_STATE_HOME', '.local/state'), APP_ID, 'logs'))


CONFIG_DIR, DATA_DIR, LOG_DIR = _dirs()


def _ensure(path, is_dir):
    os.makedirs(path if is_dir else os.path.dirname(path), exist_ok=True)
    return path


def config_dir(*parts):
    """A folder under CONFIG_DIR, created if missing."""
    return _ensure(os.path.join(CONFIG_DIR, *parts), True)


def data_dir(*parts):
    """A folder under DATA_DIR, created if missing."""
    return _ensure(os.path.join(DATA_DIR, *parts), True)


def log_dir():
    return _ensure(LOG_DIR, True)
