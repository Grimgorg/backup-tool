"""Vault Sync: keeps local copies of online collections up to date. Each kind of source is its own sub-app
with its own tab: the GOG library (vaultsync.gog) and podcasts (vaultsync.podcasts)."""
import os

APP_NAME = 'Vault Sync'
APP_ID = 'vault-sync'
__version__ = '1.0.0'

# The folder that holds app.py and the gogrepoc submodule when running from source.
SOURCE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
