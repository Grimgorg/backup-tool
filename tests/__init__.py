# Tests keep all app data in a throwaway folder; this must happen before vaultsync.paths is imported.
import os
import tempfile

os.environ['VAULT_SYNC_HOME'] = tempfile.mkdtemp(prefix='vault-sync-test-')
