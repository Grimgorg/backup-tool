"""Builds Vault Sync with PyInstaller for the operating system it runs on, into dist/:

  Windows   dist/Vault Sync.exe    (single file)
  macOS     dist/Vault Sync.app    (app bundle)
  Linux     dist/vault-sync        (single file)

Needs: python -m pip install -r requirements.txt pyinstaller, and the gogrepoc submodule
(git submodule update --init).
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from vaultsync import APP_ID, APP_NAME  # noqa: E402


def main():
    gogrepoc = os.path.join(ROOT, 'gogrepoc')
    if not os.path.isfile(os.path.join(gogrepoc, 'gogrepoc.py')):
        sys.exit('gogrepoc is missing. Run: git submodule update --init')
    build = os.path.join(ROOT, 'build')
    name = APP_ID if sys.platform.startswith('linux') else APP_NAME
    args = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--windowed', '--name', name,
            '--distpath', os.path.join(ROOT, 'dist'), '--workpath', build, '--specpath', build,
            '--paths', gogrepoc, '--hidden-import', 'gogrepoc', '--collect-data', 'sv_ttk',
            '--exclude-module', 'PyQt5']
    if sys.platform == 'darwin':
        args += ['--onedir', '--osx-bundle-identifier', 'io.github.grimgorg.vaultsync']
    else:
        args += ['--onefile']
    args.append(os.path.join(ROOT, 'app.py'))
    subprocess.check_call(args)


if __name__ == '__main__':
    main()
