"""Vault Sync: keeps local copies of online collections up to date. Each kind of source is its own sub-app
with its own tab in the console app (vaultsync.tui): the GOG library (vaultsync.gog), podcasts (vaultsync.podcasts)
and Tabletop Simulator mod backups (vaultsync.tts)."""
import os

APP_NAME = 'Vault Sync'
APP_ID = 'vault-sync'

# The folder that holds app.py and the gogrepoc submodule when running from source.
SOURCE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _source_version():
    try:
        with open(os.path.join(SOURCE_ROOT, 'VERSION'), encoding='utf-8') as f:
            return f.read().strip() + '-dev'
    except OSError:
        return '0.0-dev'


# build.py writes the version of each build into _version.py (see version.py); from source it is "<VERSION>-dev".
try:
    from vaultsync._version import __version__
except ImportError:
    __version__ = _source_version()
