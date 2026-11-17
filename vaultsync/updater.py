"""Finds new releases of Vault Sync on GitHub and installs them over the running build.

Only release builds update themselves (frozen builds with a plain x.y.z version). Builds made from pull requests or
locally (x.y.z-dev+...) and running from source never check, so a test build is never replaced by a release.

How a build replaces itself while it is running: the running program can be renamed on every OS (Windows locks it
against deleting and overwriting, not against renaming). So the current build is renamed to "<name>.old", the new
build is moved to the original place, and the new build is started in the same terminal (restart_in_terminal()).
The next start removes the ".old" leftovers (cleanup_previous()).

Every OS gets one file, vault-sync (vault-sync.exe on Windows). The release files are named
vault-sync-<os>-<arch>-<version>; versions before 2.0 (window app) used VaultSync-..., so they never install a
2.x console build by themselves.
"""
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass

from vaultsync import APP_NAME, __version__
from vaultsync.common import Cancelled, SyncError

REPO = 'Grimgorg/backup-tool'
LATEST_URL = 'https://api.github.com/repos/{}/releases/latest'.format(REPO)
RELEASES_PAGE = 'https://github.com/{}/releases'.format(REPO)
USER_AGENT = 'VaultSync/{} updater'.format(__version__)
OLD_SUFFIX = '.old'
VERSION_RE = re.compile(r'^v?(\d+)\.(\d+)\.(\d+)$')


@dataclass
class Release:
    version: str
    notes: str
    page: str
    asset_name: str
    asset_url: str
    size: int


def parse_version(text):
    """(major, minor, patch) of a release version like "1.0.3" or "v1.0.3"; None for anything else (e.g. dev)."""
    match = VERSION_RE.match((text or '').strip())
    return tuple(int(part) for part in match.groups()) if match else None


def asset_prefix(system=None, machine=None):
    """Start of the release file name for this OS and CPU, as named by the Build workflow; None if unsupported."""
    system = system or sys.platform
    machine = (machine or platform.machine()).lower()
    if system == 'win32' and machine in ('amd64', 'x86_64'):
        return 'vault-sync-windows-x64-'
    if system == 'darwin' and machine == 'arm64':
        return 'vault-sync-macos-arm64-'
    if system.startswith('linux') and machine in ('x86_64', 'amd64'):
        return 'vault-sync-linux-x64-'
    return None


def can_self_update():
    """True for a release build on a supported OS: frozen, with a plain x.y.z version."""
    return bool(getattr(sys, 'frozen', False) and parse_version(__version__) and asset_prefix())


def check(current=__version__, timeout=15):
    """The latest release if it is newer than [current] and has a build for this OS, else None.
    Raises OSError/ValueError when GitHub can't be reached or answers unexpectedly."""
    request = urllib.request.Request(LATEST_URL, headers={'User-Agent': USER_AGENT,
                                                           'Accept': 'application/vnd.github+json'})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = json.load(response)
    return pick_release(data, current)


def pick_release(data, current, prefix=None):
    """Turns a GitHub "latest release" answer into a Release if it is newer than [current]."""
    latest, mine = parse_version(data.get('tag_name')), parse_version(current)
    if not latest or not mine or latest <= mine or data.get('draft') or data.get('prerelease'):
        return None
    prefix = prefix or asset_prefix()
    for asset in data.get('assets') or []:
        name = asset.get('name') or ''
        if prefix and name.startswith(prefix):
            return Release('.'.join(map(str, latest)), (data.get('body') or '').strip(), data.get('html_url') or
                           RELEASES_PAGE, name, asset['browser_download_url'], int(asset.get('size') or 0))
    return None


# --- installing -----------------------------------------------------------------------------------

def install_target():
    """The file that is replaced: the running build."""
    return os.path.abspath(sys.executable)


def download(release, folder, progress=None, cancelled=lambda: False, timeout=60):
    """Downloads the release file into [folder]; progress(done_bytes, total_bytes). Returns its path."""
    path = os.path.join(folder, release.asset_name)
    request = urllib.request.Request(release.asset_url, headers={'User-Agent': USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response, open(path, 'wb') as out:
        total = int(response.headers.get('Content-Length') or release.size or 0)
        done = 0
        while True:
            if cancelled():
                raise Cancelled()
            chunk = response.read(256 * 1024)
            if not chunk:
                break
            out.write(chunk)
            done += len(chunk)
            if progress:
                progress(done, total)
    return path


def extract(archive, folder, target):
    """Unpacks [archive] into [folder] and returns the path of the new build inside it (same name as [target])."""
    name = os.path.basename(target)
    if archive.endswith('.tar.gz'):
        with tarfile.open(archive) as tar:
            # The "data" filter refuses links and paths that point outside the folder (Python 3.12+).
            tar.extractall(folder, **({'filter': 'data'} if hasattr(tarfile, 'data_filter') else {}))
    else:
        with zipfile.ZipFile(archive) as z:
            for member in z.namelist():
                if os.path.isabs(member) or '..' in member.replace('\\', '/').split('/'):
                    raise SyncError('The update file contains an unsafe path: {}'.format(member))
            z.extractall(folder)
    new = os.path.join(folder, name)
    if not os.path.isfile(new):
        raise SyncError('The update file does not contain "{}".'.format(name))
    if sys.platform != 'win32':
        os.chmod(new, os.stat(new).st_mode | 0o755)
    return new


def replace(new, target):
    """Puts [new] in place of the running [target]; the old build becomes "<target>.old" until the next start."""
    old = target + OLD_SUFFIX
    _remove(old)
    os.rename(target, old)
    try:
        shutil.move(new, target)
    except Exception:
        os.rename(old, target)  # put the working build back
        raise


def restart_in_terminal(target, args=()):
    """Runs the build at [target] in this terminal, after the app has ended its screen. On macOS and Linux it
    replaces this process and doesn't return. Windows can't do that with a console program (the shell would take
    the terminal back while the new build still runs), so it waits for the new build and returns its exit code."""
    if sys.platform == 'win32':
        return subprocess.call([target, *args])
    os.execv(target, [target, *args])


def install(release, progress=None, cancelled=lambda: False):
    """Downloads [release] and puts it in place of this build. Returns the target path to restart.
    The running build keeps working until the caller restarts; nothing is changed if a step fails."""
    target = install_target()
    if not os.access(os.path.dirname(target), os.W_OK):
        raise SyncError('No permission to replace {}. Download the new version from {} instead.'.format(
            target, release.page))
    # The work folder sits next to the target, so the final move is a rename on the same drive.
    work = tempfile.mkdtemp(prefix='.{}-update-'.format(APP_NAME.replace(' ', '')),
                            dir=os.path.dirname(target))
    try:
        archive = download(release, work, progress, cancelled)
        if cancelled():
            raise Cancelled()
        new = extract(archive, os.path.join(work, 'new'), target)
        replace(new, target)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return target


def cleanup_previous():
    """Removes the build replaced by the last update (and leftovers of an interrupted one)."""
    if not getattr(sys, 'frozen', False):
        return
    target = install_target()
    _remove(target + OLD_SUFFIX)
    folder = os.path.dirname(target)
    prefix = '.{}-update-'.format(APP_NAME.replace(' ', ''))
    try:
        for name in os.listdir(folder):
            if name.startswith(prefix):
                shutil.rmtree(os.path.join(folder, name), ignore_errors=True)
    except OSError:
        pass


def _remove(path):
    try:
        if os.path.isdir(path) and not os.path.islink(path):
            shutil.rmtree(path)
        elif os.path.lexists(path):
            os.remove(path)
    except OSError:
        pass  # e.g. still running on Windows right after the update; removed at a later start
