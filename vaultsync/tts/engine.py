"""Tabletop Simulator sub-app logic: backs up TTS mods together with every asset they use.

TTS only stores links to a mod's images, models, asset bundles, PDFs and sounds. This downloads every asset into
TTS's own cache folders (where and how TTS itself stores them) and packs each mod with its assets into one zip
in the backup folder. Restoring a zip, on any computer, puts everything back so the mod works without the links.

The Arkham Horror LCG (SCE) mod also downloads content packs in-game; those are saved as TTS Saved Objects and
packed into a second zip per mod (see vaultsync.tts.arkham).

The zips are compatible with the stand-alone tts-backup command line tool. Settings (tts.json) are kept in the
data folder next to the program; see vaultsync.paths.
"""
import datetime
import os
import threading
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from vaultsync import paths
from vaultsync.common import Cancelled, SyncError, format_size, load_json, save_json
from vaultsync.tts import arkham
from vaultsync.tts.downloader import AssetResult, CacheIndex, download_assets, lookup_cached
from vaultsync.tts.mods import default_mods_dir, list_workshop_mods, load_json as load_mod_json, mod_name, \
    read_index_names
from vaultsync.tts.package import PackageError, build_extras_package, build_package, restore_package
from vaultsync.tts.scanner import find_assets

CONFIG_FILE = os.path.join(paths.HOME, 'tts', 'tts.json')
EXTRAS_TITLE = 'Downloadable content'
WORKERS = 8


# --- settings -------------------------------------------------------------------------------------

def default_config():
    return {
        'mods_dir': '',  # empty: the TTS default for this OS
        'backup_dir': os.path.join(os.path.expanduser('~'), 'TTS Backups'),
        'extras': True,
        'arkham_languages': ','.join(arkham.DEFAULT_LANGUAGES),
        'skip': [],  # mods left out, by path relative to Mods/Workshop
    }


def load_config():
    cfg = default_config()
    cfg.update(load_json(CONFIG_FILE, {}))
    return cfg


def save_config(cfg):
    save_json(CONFIG_FILE, cfg)


def mods_dir(cfg):
    return Path(cfg['mods_dir']).expanduser() if cfg['mods_dir'] else default_mods_dir()


def languages(cfg):
    return [x.strip() for x in cfg['arkham_languages'].split(',') if x.strip() and x.strip().lower() != 'none']


@dataclass
class ModEntry:
    """A mod in the Workshop folder, as shown in the list."""
    key: str  # path relative to Mods/Workshop, e.g. "Campaign Games/123.json"
    name: str
    folder: str  # TTS in-game folder ("" for the top level)
    size: int  # size of the mod's JSON file
    mod: object  # vaultsync.tts.mods.ModFile


def list_mods(cfg):
    """All mods in the Workshop folder and its subfolders, with their names."""
    base = mods_dir(cfg)
    names = read_index_names(base)
    entries = []
    for mod in list_workshop_mods(base):
        name = names.get(mod.display_path)
        if not name:
            try:
                name = mod_name(load_mod_json(mod.json_path), mod.id)
            except (OSError, ValueError):
                name = mod.id
        entries.append(ModEntry(mod.display_path, name, mod.subfolder, mod.json_path.stat().st_size, mod))
    return entries


# --- results --------------------------------------------------------------------------------------

@dataclass
class ModCheck:
    """What Check found for one mod, without downloading anything."""
    entry: ModEntry
    assets: int = 0
    cached: int = 0
    script_links: int = 0  # links found only in scripts (counted in assets)
    arkham: bool = False
    packs_local: int = 0
    pack_assets: int = 0
    pack_assets_cached: int = 0
    warning: str = ''
    error: str = ''

    @property
    def to_download(self):
        return self.assets - self.cached + self.pack_assets - self.pack_assets_cached


@dataclass
class ModBackup:
    """Outcome of backing up one mod."""
    entry: ModEntry
    zips: list = field(default_factory=list)
    assets: int = 0
    missing: int = 0
    packs: int = 0
    warnings: list = field(default_factory=list)
    error: str = ''


# --- engine ---------------------------------------------------------------------------------------

class Engine:
    """Runs one operation at a time in a worker thread and reports through callbacks:
    log(line), status(text), progress(fraction or None)."""

    def __init__(self, log, status, progress):
        self._log_cb = log
        self.status = status
        self.progress = progress
        self._cancel = threading.Event()
        self._logfile = None

    def cancel(self):
        self._cancel.set()

    def _stopped(self):
        return self._cancel.is_set()

    def _check_cancelled(self):
        if self._cancel.is_set():
            raise Cancelled()

    def log(self, line):
        if self._logfile:
            self._logfile.write(line + '\n')
            self._logfile.flush()
        self._log_cb(line)

    def _start(self, name):
        self._cancel.clear()
        path = os.path.join(paths.log_dir(), 'tts-{}-{:%Y-%m-%d_%H%M%S}.log'.format(name, datetime.datetime.now()))
        self._logfile = open(path, 'w', encoding='utf-8')

    def _end(self):
        self.progress(None)
        if self._logfile:
            self._logfile.close()
            self._logfile = None

    # --- check ------------------------------------------------------------------------------------

    def check(self, cfg, entries):
        """Counts each mod's assets and how many are already cached. Downloads nothing."""
        self._start('check')
        try:
            base = mods_dir(cfg)
            index = CacheIndex(base)
            checks = []
            for i, entry in enumerate(entries):
                self._check_cancelled()
                self.status('Checking {} of {}: {}'.format(i + 1, len(entries), entry.name))
                self.progress(i / max(len(entries), 1))
                try:
                    checks.append(self._check_mod(cfg, base, index, entry))
                except Cancelled:
                    raise
                except Exception as e:  # one broken mod file must not stop the others
                    self.log('ERROR: {}: {}'.format(entry.name, e))
                    checks.append(ModCheck(entry, error=str(e) or e.__class__.__name__))
            return checks
        finally:
            self._end()

    def _check_mod(self, cfg, base, index, entry):
        data = load_mod_json(entry.mod.json_path)
        assets = find_assets(data)
        check = ModCheck(entry, assets=len(assets), script_links=sum(1 for f in assets.values() if f is None))
        # Script links that are not cached may not be assets at all, so only known types count as "to download".
        check.cached = sum(1 for url, folder in assets.items()
                           if folder is None or lookup_cached(index, url, folder))
        if cfg['extras'] and arkham.is_arkham_sce(data):
            check.arkham = True
            content = arkham.local_content(base, data)
            check.packs_local = len(content.json_files)
            pack_assets = {u: f for u, f in arkham.collect_assets(content, find_assets).items() if u not in assets}
            check.pack_assets = len(pack_assets)
            check.pack_assets_cached = sum(1 for url, folder in pack_assets.items()
                                           if folder is None or lookup_cached(index, url, folder))
        if cfg['extras'] and arkham.looks_like_unrecognised_arkham(data):
            check.warning = 'download source not recognised, downloadable content will not be backed up'
        self.log('{}: {} assets, {} cached{}'.format(
            entry.name, check.assets, check.cached,
            ', {} packs ({} assets) from an earlier run'.format(check.packs_local, check.pack_assets)
            if check.arkham else ''))
        return check

    # --- backup -----------------------------------------------------------------------------------

    def backup(self, cfg, entries):
        """Downloads every asset of each mod into the TTS folders and writes its zip(s) into the backup folder.
        Returns a ModBackup per mod; problems with one mod do not stop the others."""
        if not cfg['backup_dir']:
            raise SyncError('Please choose a backup folder.')
        self._start('backup')
        try:
            base = mods_dir(cfg)
            out_dir = Path(cfg['backup_dir']).expanduser()
            results = []
            for i, entry in enumerate(entries):
                self._check_cancelled()
                step = _Steps(self.progress, i, len(entries))
                self.log('')
                self.log('> {} ({} of {})'.format(entry.name, i + 1, len(entries)))
                try:
                    results.append(self._backup_mod(cfg, base, out_dir, entry, step))
                except Cancelled:
                    raise
                except Exception as e:  # one broken mod must not stop the others
                    self.log('ERROR: {}: {}'.format(entry.name, e))
                    results.append(ModBackup(entry, error=str(e) or e.__class__.__name__))
            return results
        finally:
            self._end()

    def _backup_mod(self, cfg, base, out_dir, entry, step):
        data = load_mod_json(entry.mod.json_path)
        result = ModBackup(entry)
        assets = find_assets(data)
        self.status('{}: downloading {} assets'.format(entry.name, len(assets)))
        mod_results = self._download(base, assets, step.part(0, 0.45))
        result.assets = len(mod_results)

        content, pack_results = None, []
        if cfg['extras'] and arkham.looks_like_unrecognised_arkham(data):
            self._warn(result, 'looks like Arkham Horror with a download menu, but its download source is not one '
                               'Vault Sync knows - its downloadable content was NOT backed up')
        if cfg['extras'] and arkham.is_arkham_sce(data):
            content = self._fetch_packs(cfg, base, data, entry, result, step.part(0.45, 0.5))
            known = {r.url for r in mod_results}
            pack_assets = {u: f for u, f in arkham.collect_assets(content, find_assets).items() if u not in known}
            self.status('{}: downloading {} assets of the packs'.format(entry.name, len(pack_assets)))
            self.log('{} assets used by the packs'.format(len(pack_assets)))
            pack_results = self._download(base, pack_assets, step.part(0.5, 0.8))
            result.packs = len(content.json_files)
            result.assets += len(pack_results)

        result.missing = sum(r.missing for r in mod_results + pack_results)
        result.missing += len(content.failed) if content else 0

        self.status('{}: writing the backup'.format(entry.name))
        packing = step.part(0.8, 1.0)
        zip_path = build_package(entry.mod, mod_results, out_dir, on_file=self._file_progress(packing, 0, 0.3))
        result.zips.append(zip_path)
        if content and content.files:
            in_mod_zip = [r.path for r in mod_results if r.path]
            result.zips.append(build_extras_package(
                entry.mod, EXTRAS_TITLE, content.files, pack_results, out_dir, base, exclude=in_mod_zip,
                on_file=self._file_progress(packing, 0.3, 1.0)))
        for path in result.zips:
            self.log('Wrote {} ({})'.format(path, format_size(path.stat().st_size)))
        return result

    def _download(self, base, assets, progress):
        counts = {}

        def on_result(done, total, r):
            counts[r.status] = counts.get(r.status, 0) + 1
            if r.missing:
                self.log('MISSING {} ({})'.format(r.url, r.error))
            progress(done / max(total, 1))

        results = download_assets(base, assets, workers=WORKERS, on_result=on_result, should_stop=self._stopped)
        self._check_cancelled()
        line = '{} downloaded, {} already cached, {} missing'.format(
            counts.get('downloaded', 0), counts.get('cached', 0), counts.get('failed', 0))
        if counts.get('skipped'):
            line += ', {} script links not usable (ignored)'.format(counts['skipped'])
        self.log(line)
        return results

    def _fetch_packs(self, cfg, base, data, entry, result, progress):
        self.status('{}: fetching the downloadable content'.format(entry.name))
        try:
            content = arkham.fetch_packs(data, base, languages(cfg), workers=WORKERS,
                                         on_progress=lambda done, total: progress(done / max(total, 1)),
                                         should_stop=self._stopped)
        except Cancelled:
            raise
        except Exception as e:  # network down, or the mod changed its format
            self._check_cancelled()
            content = arkham.local_content(base, data)
            fallback = ('using {} packs from an earlier run'.format(len(content.json_files))
                        if content.json_files else 'no packs from earlier runs either')
            self._warn(result, 'could not fetch the downloadable content ({}); {}'.format(e, fallback))
            return content
        self.log('{} packs saved to Saved Objects/{}/{}'.format(
            len(content.json_files), arkham.SAVED_OBJECTS_FOLDER, arkham.mod_version(data)))
        for problem in content.failed:
            self.log('MISSING pack {}'.format(problem))
        return content

    def _warn(self, result, message):
        result.warnings.append(message)
        self.log('WARNING: ' + message)

    def _file_progress(self, progress, start, end):
        def on_file(done, total):
            if self._cancel.is_set():
                raise Cancelled()  # the half-written zip is removed by the packer
            progress(start + (end - start) * done / max(total, 1))
        return on_file

    # --- restore ----------------------------------------------------------------------------------

    def restore(self, cfg, folder, overwrite=False):
        """Restores every backup zip in folder (and its subfolders) into the TTS folders.
        Returns (restored names, files written, list of 'zip: problem')."""
        self._start('restore')
        try:
            base = mods_dir(cfg)
            zips = sorted(Path(folder).rglob('*.zip'), key=lambda p: str(p).lower())
            if not zips:
                raise SyncError('No backup zips in {}'.format(folder))
            restored, written, problems = [], 0, []
            for i, zip_path in enumerate(zips):
                self._check_cancelled()
                label = str(zip_path.relative_to(folder))
                self.status('Restoring {} of {}: {}'.format(i + 1, len(zips), label))
                on_file = self._file_progress(lambda f, i=i: self.progress((i + f) / len(zips)), 0, 1)
                try:
                    report = restore_package(zip_path, base, overwrite, on_file=on_file)
                except Cancelled:
                    raise
                except (PackageError, zipfile.BadZipFile, OSError, ValueError) as e:
                    # One bad or foreign zip must not stop the others.
                    self.log('ERROR: {}: {}'.format(label, e))
                    problems.append('{}: {}'.format(label, e))
                    continue
                restored.append(report.manifest.get('name') or label)
                written += len(report.written)
                self.log('{}: {} files written, {} already present{}'.format(
                    label, len(report.written), len(report.skipped),
                    ', added to the TTS mod list' if report.index_updated else ''))
            return restored, written, problems
        finally:
            self._end()


class _Steps:
    """Maps the progress of one mod (0..1, in parts) onto the progress of the whole run."""

    def __init__(self, progress, index, count):
        self._progress, self._index, self._count = progress, index, count

    def part(self, start, end):
        return lambda fraction: self._progress(
            (self._index + start + (end - start) * min(max(fraction, 0), 1)) / self._count)
