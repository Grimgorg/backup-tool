"""Builds Vault Sync with PyInstaller for the operating system it runs on, into dist/:

  Windows   dist/Vault Sync.exe    (single file)
  macOS     dist/Vault Sync.app    (app bundle)
  Linux     dist/vault-sync        (single file)

The version shown in the app comes from version.py (a test version such as 1.0.3-dev+abc1234), or from the
VAULT_SYNC_VERSION environment variable, which the Build workflow sets.

Needs: python -m pip install -r requirements.txt pyinstaller, and the gogrepoc submodule
(git submodule update --init).
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

import version  # noqa: E402
from vaultsync import APP_ID, APP_NAME  # noqa: E402

VERSION_MODULE = os.path.join(ROOT, 'vaultsync', '_version.py')


def main():
    gogrepoc = os.path.join(ROOT, 'gogrepoc')
    if not os.path.isfile(os.path.join(gogrepoc, 'gogrepoc.py')):
        sys.exit('gogrepoc is missing. Run: git submodule update --init')
    build = os.path.join(ROOT, 'build')
    name = APP_ID if sys.platform.startswith('linux') else APP_NAME
    args = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--windowed', '--name', name,
            '--distpath', os.path.join(ROOT, 'dist'), '--workpath', build, '--specpath', build,
            '--paths', gogrepoc, '--hidden-import', 'gogrepoc', '--collect-data', 'sv_ttk',
            '--exclude-module', 'PyQt5',
            # The window icon is read at runtime from vaultsync/assets (see make_icon.py).
            '--add-data', '{}{}vaultsync/assets'.format(os.path.join(ROOT, 'vaultsync', 'assets', 'window.png'),
                                                        os.pathsep),
            '--icon', os.path.join(ROOT, 'vaultsync', 'assets',
                                   'icon.icns' if sys.platform == 'darwin' else 'icon.ico')]
    if sys.platform == 'darwin':
        args += ['--onedir', '--osx-bundle-identifier', 'io.github.grimgorg.vaultsync']
    else:
        args += ['--onefile']
    args.append(os.path.join(ROOT, 'app.py'))
    app_version = os.environ.get('VAULT_SYNC_VERSION') or version.compute()
    print('Building {} {}'.format(APP_NAME, app_version))
    # The module only exists during the build, so running from source keeps showing the -dev version.
    with open(VERSION_MODULE, 'w', encoding='utf-8') as f:
        f.write('# Written by build.py.\n__version__ = {!r}\n'.format(app_version))
    try:
        subprocess.check_call(args)
    finally:
        os.remove(VERSION_MODULE)


if __name__ == '__main__':
    main()
