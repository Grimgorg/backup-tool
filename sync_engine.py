# Sync logic of GoG Sync (formerly sync.ps1): runs gogrepoc as a child process, previews what a sync would
# download and delete, and applies it while keeping the export folder an exact mirror of the library.
# Files to delete are first moved to !orphaned, deleted once all downloads are complete, and moved back if
# anything fails or the sync is cancelled.
import codecs
import contextlib
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass, field

import organize

FROZEN = getattr(sys, 'frozen', False)
# gogrepoc keeps its token, manifest and log in the working directory, which is the folder of the exe
# (or of this script when run from source).
DATA_DIR = os.path.dirname(os.path.abspath(sys.executable if FROZEN else __file__))
GOGREPOC_DIR = os.path.join(DATA_DIR, 'gogrepoc')
TOKEN_FILE = os.path.join(DATA_DIR, 'gog-token.dat')
MANIFEST_FILE = os.path.join(DATA_DIR, 'gog-manifest.dat')
SETTINGS_FILE = os.path.join(DATA_DIR, 'settings.json')
LOG_DIR = os.path.join(DATA_DIR, 'logs')
ORPHAN_DIR_NAME = '!orphaned'

organize.MANIFEST = MANIFEST_FILE

DEFAULT_SETTINGS = {
    'save_dir': r'D:\GOG Export',
    'os': ['windows'],
    'langs': ['en', 'de'],
    'extras': True,
    'full_scan': True,
    'verify': False,
    'sort': True,
    # Files not needed for a fresh install (glob patterns, matched case-insensitively):
    #  - incremental "old version to new version" patches; the full installer is already the latest version
    #  - Witcher 3 language-pack patches (GOG lists them as extras, so -lang doesn't filter them); keeps only DE
    'skip_files': ['patch_*_to_*', 'patch_witcher3_[!d]?_goty_*'],
}

# Console lines of gogrepoc look like "17:37:06 | message"; prompts from input() have no prefix.
LOG_PREFIX = re.compile(r'^\d\d:\d\d:\d\d \| ?')
GAME_LINE = re.compile(r'^\{(.+)\}$')
DOWNLOAD_LINE = re.compile(r'^\s+(download|working|error)\s+(.+)$')
ORPHAN_DIR_LINE = re.compile(r"^orphaning dir\s+'(.+)'$")
ORPHAN_FILE_LINE = re.compile(r"^orphaning file '(.+?)\\(.+)'( as it has been marked for change\.)?$")
DETAILS_LINE = re.compile(r'^\(\s*(\d+) / (\d+)\) fetching game details for (.+?)\.*$')
RATE_LINE = re.compile(r'^\s*[\d.]+MB\s+([\d.]+)MB/s')
REMAINING_LINE = re.compile(r'^([\d.]+)GB remaining$')

WHOLE_FOLDER = '(whole folder)'
CREATE_SUSPENDED = 0x00000004


def load_settings():
    settings = dict(DEFAULT_SETTINGS)
    try:
        with open(SETTINGS_FILE, encoding='utf-8') as f:
            settings.update(json.load(f))
    except FileNotFoundError:
        pass
    return settings


def save_settings(settings):
    with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
        json.dump(settings, f, indent=2)


def is_logged_in():
    return os.path.isfile(TOKEN_FILE)


def format_size(b):
    if b is None:
        return ''
    for unit, factor in (('GB', 1024 ** 3), ('MB', 1024 ** 2), ('KB', 1024)):
        if b >= factor:
            return '%.2f %s' % (b / factor, unit)
    return '%d B' % b


class _Job:
    """Windows job object for one gogrepoc run. It kills the processes in it when the app ends in any way
    (also when it is killed or crashes), so gogrepoc never keeps running unseen, and terminate() also
    reaches processes gogrepoc's launcher started (the venv python.exe is a launcher, for one)."""
    _kernel32 = None

    def __init__(self):
        import ctypes
        from ctypes import wintypes
        if _Job._kernel32 is None:
            k = ctypes.WinDLL('kernel32', use_last_error=True)
            k.CreateJobObjectW.restype = wintypes.HANDLE
            k.CreateJobObjectW.argtypes = (wintypes.LPVOID, wintypes.LPCWSTR)
            k.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD)
            k.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
            k.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)
            k.CloseHandle.argtypes = (wintypes.HANDLE,)
            _Job._kernel32 = k
        self._ctypes = ctypes
        self.handle = self._kernel32.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        info = _JobLimits()
        info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self._kernel32.SetInformationJobObject(self.handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
            raise ctypes.WinError(ctypes.get_last_error())

    def start(self, proc):
        """Adds proc, which was created with CREATE_SUSPENDED, and lets it run. Assigning it before it runs
        makes every process it starts a member of the job too."""
        try:
            if not self._kernel32.AssignProcessToJobObject(self.handle, int(proc._handle)):
                raise self._ctypes.WinError(self._ctypes.get_last_error())
        finally:
            self._ctypes.windll.ntdll.NtResumeProcess(self._ctypes.c_void_p(int(proc._handle)))

    def terminate(self):
        if self.handle:
            self._kernel32.TerminateJobObject(self.handle, 1)

    def close(self):
        if self.handle:
            self._kernel32.CloseHandle(self.handle)
            self.handle = None


def _job_limits_struct():
    import ctypes
    from ctypes import wintypes

    class IoCounters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in (
            'ReadOperationCount', 'WriteOperationCount', 'OtherOperationCount',
            'ReadTransferCount', 'WriteTransferCount', 'OtherTransferCount')]

    class BasicLimits(ctypes.Structure):
        _fields_ = [('PerProcessUserTimeLimit', ctypes.c_int64), ('PerJobUserTimeLimit', ctypes.c_int64),
                    ('LimitFlags', wintypes.DWORD), ('MinimumWorkingSetSize', ctypes.c_size_t),
                    ('MaximumWorkingSetSize', ctypes.c_size_t), ('ActiveProcessLimit', wintypes.DWORD),
                    ('Affinity', ctypes.c_size_t), ('PriorityClass', wintypes.DWORD),
                    ('SchedulingClass', wintypes.DWORD)]

    class ExtendedLimits(ctypes.Structure):  # JOBOBJECT_EXTENDED_LIMIT_INFORMATION
        _fields_ = [('BasicLimitInformation', BasicLimits), ('IoInfo', IoCounters),
                    ('ProcessMemoryLimit', ctypes.c_size_t), ('JobMemoryLimit', ctypes.c_size_t),
                    ('PeakProcessMemoryUsed', ctypes.c_size_t), ('PeakJobMemoryUsed', ctypes.c_size_t)]

    return ExtendedLimits


_JobLimits = _job_limits_struct()


class Cancelled(Exception):
    pass


class SyncError(Exception):
    pass


@dataclass
class Item:
    game: str
    file: str
    size: int = None


@dataclass
class Plan:
    downloads: list = field(default_factory=list)
    deletions: list = field(default_factory=list)

    @property
    def download_size(self):
        return sum(i.size or 0 for i in self.downloads)

    @property
    def deletion_size(self):
        return sum(i.size or 0 for i in self.deletions)

    @property
    def empty(self):
        return not self.downloads and not self.deletions


class Engine:
    """Runs one operation at a time in a worker thread and reports through callbacks:
    log(line), status(text), progress(fraction or None), ask(prompt, context) -> answer or None."""

    def __init__(self, log, status, progress, ask):
        self._log_cb = log
        self.status = status
        self.progress = progress
        self.ask = ask
        self._job = None
        self._cancelled = False
        self._logfile = None

    # --- plumbing ---------------------------------------------------------------------------------

    def cancel(self):
        self._cancelled = True
        job = self._job
        if job:
            job.terminate()

    def _check_cancelled(self):
        if self._cancelled:
            raise Cancelled()

    def log(self, line):
        if self._logfile:
            self._logfile.write(line + '\n')
            self._logfile.flush()
        self._log_cb(line)

    @contextlib.contextmanager
    def _session(self, name):
        self._cancelled = False
        os.makedirs(LOG_DIR, exist_ok=True)
        path = os.path.join(LOG_DIR, '{}-{:%Y-%m-%d_%H%M%S}.log'.format(name, datetime.datetime.now()))
        with open(path, 'w', encoding='utf-8') as self._logfile:
            try:
                yield
            finally:
                self.progress(None)
                self._logfile = None

    def _gogrepoc(self, args, on_line=None, stdin_text=None, quiet=False):
        """Runs gogrepoc with args and returns its console messages (timestamp prefix removed).
        on_line(message) can consume a line and return True to keep it out of the log view."""
        self._check_cancelled()
        if FROZEN:
            cmd = [sys.executable, '--gogrepoc'] + args
        else:
            cmd = [sys.executable, os.path.join(DATA_DIR, 'gog_sync.py'), '--gogrepoc'] + args
        env = dict(os.environ, PYTHONUNBUFFERED='1', PYTHONIOENCODING='utf-8')
        self.log('> gogrepoc ' + ' '.join(args))
        job = _Job()
        proc = subprocess.Popen(
            cmd, cwd=DATA_DIR, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW | CREATE_SUSPENDED)
        self._job = job
        try:
            job.start(proc)
        except OSError:
            job.terminate()
            proc.wait()
            job.close()
            self._job = None
            raise
        if self._cancelled:  # cancel() ran before self._job was set
            job.terminate()
        if stdin_text is not None:
            proc.stdin.write(stdin_text.encode('utf-8'))
            proc.stdin.flush()

        messages = []
        decoder = codecs.getincrementaldecoder('utf-8')('replace')
        pending = ''
        try:
            while True:
                chunk = proc.stdout.read1(65536)
                if not chunk:
                    break
                pending += decoder.decode(chunk)
                *lines, pending = pending.replace('\r\n', '\n').split('\n')
                for line in lines:
                    message = LOG_PREFIX.sub('', line)
                    messages.append(message)
                    if self._logfile:
                        self._logfile.write(line + '\n')
                    if not (on_line and on_line(message)) and not quiet:
                        self._log_cb(line)
                if pending.endswith(': ') and not LOG_PREFIX.match(pending):
                    # gogrepoc waits for input (two-step code, login URL, manifest question).
                    answer = self.ask(pending.strip(), '\n'.join(messages[-8:]))
                    self.log(pending)
                    pending = ''
                    if answer is None:
                        self.cancel()
                        break
                    proc.stdin.write((answer + '\n').encode('utf-8'))
                    proc.stdin.flush()
        finally:
            proc.wait()
            self._job = None
            job.close()
            if self._logfile:
                self._logfile.flush()
        self._check_cancelled()
        if proc.returncode != 0:
            raise SyncError('gogrepoc {} failed (exit code {}), see the log.'.format(args[0], proc.returncode))
        return messages

    def _organize(self, command, save_dir):
        organize.output = self.log
        try:
            getattr(organize, 'cmd_' + command)(save_dir, False)
        finally:
            organize.output = print

    @staticmethod
    def _filter_args(settings):
        return ['-os'] + list(settings['os']) + ['-lang'] + list(settings['langs'])

    def _download_args(self, settings):
        args = ['download', settings['save_dir']] + self._filter_args(settings)
        if settings['skip_files']:
            args += ['-skipfiles'] + list(settings['skip_files'])
        if not settings['extras']:
            args.append('-skipextras')
        return args

    @staticmethod
    def _orphan_dir(settings):
        return os.path.join(settings['save_dir'], ORPHAN_DIR_NAME)

    # --- !orphaned handling -----------------------------------------------------------------------

    def _restore_orphans(self, settings):
        """Moves everything in !orphaned back to where clean took it from: !orphaned\\<game> is either a
        whole game folder or holds single files of <game>."""
        save_dir, orphan_dir = settings['save_dir'], self._orphan_dir(settings)
        if not os.path.isdir(orphan_dir):
            return
        if not any(files for _, _, files in os.walk(orphan_dir)):
            # gogrepoc creates the folder even for "clean -dryrun".
            shutil.rmtree(orphan_dir)
            return
        self.log('restoring files from ' + orphan_dir)
        for game in os.listdir(orphan_dir):
            src_game = os.path.join(orphan_dir, game)
            dst_game = os.path.join(save_dir, game)
            if not os.path.isdir(src_game):
                continue
            if not os.path.exists(dst_game):
                os.rename(src_game, dst_game)
                continue
            for name in os.listdir(src_game):
                dst = os.path.join(dst_game, name)
                if os.path.exists(dst):
                    self.log('WARNING: {} already exists, leaving {} in place'.format(dst, os.path.join(src_game, name)))
                    continue
                os.rename(os.path.join(src_game, name), dst)
        if not any(files for _, _, files in os.walk(orphan_dir)):
            shutil.rmtree(orphan_dir)

    def _delete_orphans(self, settings):
        orphan_dir = self._orphan_dir(settings)
        if os.path.isdir(orphan_dir):
            self.log('deleting ' + orphan_dir)
            shutil.rmtree(orphan_dir)

    # --- operations -------------------------------------------------------------------------------

    @staticmethod
    def needs_recovery(settings):
        """True if a sync was interrupted (crash, power loss) while files were moved aside."""
        return os.path.isdir(Engine._orphan_dir(settings))

    def recover(self, settings):
        with self._session('recover'):
            self.status('Restoring files from an interrupted sync...')
            self.progress(None)
            self._restore_orphans(settings)
            if settings['sort']:
                self.status('Sorting files...')
                self._organize('sort', settings['save_dir'])
            self.status('Restored the files of an interrupted sync. Nothing was deleted.')

    def login(self, username, password):
        with self._session('login'):
            self.status('Logging in...')
            self.progress(None)
            # The child reads the password from stdin instead of the console (see run_gogrepoc).
            self._gogrepoc(['login', username], stdin_text=password + '\n')
            if not is_logged_in():
                raise SyncError('Login failed, see the log.')
            self.status('Logged in.')

    def check(self, settings):
        """Updates the manifest and returns the Plan of what apply() would download and delete."""
        save_dir = settings['save_dir']
        with self._session('check'):
            os.makedirs(save_dir, exist_ok=True)
            self._restore_orphans(settings)
            self.status('Preparing folders...')
            self._organize('flatten', save_dir)
            try:
                self.status('Scanning the GOG library...')
                self.progress(0)

                def on_update_line(message):
                    m = DETAILS_LINE.match(message)
                    if m:
                        done, total = int(m.group(1)), int(m.group(2))
                        self.progress(done / total)
                        self.status('Scanning the GOG library: {} ({}/{})'.format(m.group(3), done, total))
                    return False

                update_args = ['update'] + self._filter_args(settings) + ['-installers', 'standalone']
                if settings['full_scan']:
                    update_args.append('-full')
                self._gogrepoc(update_args, on_line=on_update_line)

                self.status('Comparing the library with the local folder...')
                self.progress(None)
                plan = Plan(downloads=self._planned_downloads(settings),
                            deletions=self._planned_deletions(settings))
                return plan
            finally:
                self._restore_orphans(settings)
                if settings['sort']:
                    self.status('Sorting files...')
                    self._organize('sort', save_dir)

    def apply(self, settings, plan):
        save_dir = settings['save_dir']
        with self._session('sync'):
            self._restore_orphans(settings)
            self.status('Preparing folders...')
            self._organize('flatten', save_dir)
            cleaned = success = False
            try:
                # Moves the files to delete into !orphaned; they are only deleted after a complete download.
                self.status('Moving old files aside...')
                cleaned = True
                self._gogrepoc(['clean', save_dir])

                if plan.downloads:
                    self._download(settings, plan)
                if settings['verify']:
                    self.status('Verifying files...')
                    self.progress(None)
                    args = ['verify', save_dir] + self._filter_args(settings)
                    if settings['skip_files']:
                        args += ['-skipfiles'] + list(settings['skip_files'])
                    if not settings['extras']:
                        args.append('-skipextras')
                    self._gogrepoc(args)

                # gogrepoc exits with 0 even when single files fail, so check that nothing is left to download.
                self.status('Checking downloads...')
                self.progress(None)
                remaining = self._planned_downloads(settings)
                if remaining:
                    raise SyncError('{} file(s) did not download completely: {}'.format(
                        len(remaining), ', '.join(i.file for i in remaining[:5])))
                success = True
            finally:
                if cleaned and success:
                    self.status('Deleting old files...')
                    self._delete_orphans(settings)
                else:
                    if cleaned:
                        self.log('Sync incomplete, moving the old files back.')
                    self._restore_orphans(settings)
                if settings['sort']:
                    self.status('Sorting files...')
                    self._organize('sort', save_dir)
            self.status('Done. The local folder now mirrors the GOG library.')

    def _download(self, settings, plan):
        total = plan.download_size or 1
        rate = [0.0]

        def on_line(message):
            m = RATE_LINE.match(message)
            if m:
                rate[0] += float(m.group(1))
                return True
            m = REMAINING_LINE.match(message)
            if m:
                left = float(m.group(1)) * 1024 ** 3
                text = 'Downloading: {} of {} left'.format(format_size(left), format_size(total))
                if rate[0] > 0:
                    eta = datetime.timedelta(seconds=int(left / (rate[0] * 1024 ** 2)))
                    text += ', {:.1f} MB/s, about {} remaining'.format(rate[0], eta)
                self.status(text)
                self.progress(max(0.0, min(1.0, 1 - left / total)))
                rate[0] = 0.0
                return True
            return False

        self.status('Downloading...')
        self.progress(0)
        self._gogrepoc(self._download_args(settings), on_line=on_line)

    def _planned_downloads(self, settings):
        # "download -dryrun" prints "{<game folder>}" before each game, then one line per file that is
        # missing ("download"), half finished ("working") or in an inconsistent state ("error").
        sizes = _manifest_sizes()
        items, game = [], None
        for message in self._gogrepoc(self._download_args(settings) + ['-dryrun'], quiet=True):
            m = GAME_LINE.match(message)
            if m:
                game = m.group(1)
                continue
            m = DOWNLOAD_LINE.match(message)
            if m:
                name = m.group(2)
                items.append(Item(game, name, sizes.get((game, name))))
        return items

    def _planned_deletions(self, settings):
        # "clean -dryrun" prints "orphaning dir  '<game>'" or "orphaning file '<game>\<file>'".
        save_dir = settings['save_dir']
        items = []
        for message in self._gogrepoc(['clean', save_dir, '-dryrun'], quiet=True):
            m = ORPHAN_DIR_LINE.match(message)
            if m:
                items.append(Item(m.group(1), WHOLE_FOLDER, _tree_size(os.path.join(save_dir, m.group(1)))))
                continue
            m = ORPHAN_FILE_LINE.match(message)
            if m:
                path = os.path.join(save_dir, m.group(1), m.group(2))
                items.append(Item(m.group(1), m.group(2), os.path.getsize(path) if os.path.isfile(path) else None))
        return items


def _manifest_sizes():
    sizes = {}
    try:
        games = organize.load_manifest()
    except (OSError, ValueError, SyntaxError):
        return sizes
    for game in games:
        folder = game.get('folder_name') or game['title']
        for key in ('downloads', 'galaxyDownloads', 'sharedDownloads', 'extras'):
            for d in game.get(key) or []:
                if d.get('name') and d.get('size') is not None:
                    sizes[(folder, d['name'])] = d['size']
    return sizes


def _tree_size(path):
    return sum(os.path.getsize(os.path.join(root, f)) for root, _, files in os.walk(path) for f in files)


def run_gogrepoc(argv):
    """Entry point of the child process: runs gogrepoc with argv like its own __main__ block does."""
    import getpass
    # The GUI pipes the password through stdin; getpass would read from the console instead.
    getpass.getpass = lambda prompt='Password: ', stream=None: sys.stdin.readline().rstrip('\r\n')
    if not FROZEN:
        sys.path.insert(0, GOGREPOC_DIR)
    import gogrepoc

    wakelock = gogrepoc.Wakelock()
    try:
        wakelock.take_wakelock()
        gogrepoc.main(gogrepoc.process_argv(['gogrepoc.py'] + argv))
        gogrepoc.info('exiting...')
    except KeyboardInterrupt:
        gogrepoc.info('exiting...')
        sys.exit(1)
    except SystemExit:
        raise
    except Exception:
        gogrepoc.log_exception('fatal...')
        sys.exit(1)
    finally:
        wakelock.release_wakelock()
