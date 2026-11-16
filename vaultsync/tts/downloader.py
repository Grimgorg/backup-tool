"""Download mod assets into the TTS cache folders, the way TTS itself would."""

from __future__ import annotations

import os
import shutil
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional

from .naming import (
    alternate_urls,
    cache_stem,
    detect_folder,
    download_candidates,
    looks_like_html,
    sniff_extension,
)
from vaultsync.common import Cancelled

from .mods import ASSET_FOLDERS

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
CHUNK = 256 * 1024
TIMEOUT = 60


@dataclass
class AssetResult:
    url: str
    # Cache folder; None for a script link that could not be fetched.
    folder: Optional[str]
    # "cached", "downloaded", "failed" (a missing asset) or "skipped" (a link
    # found only in a script that could not be fetched; may not be an asset).
    status: str
    path: Optional[Path] = None
    error: str = ""

    @property
    def missing(self) -> bool:
        return self.status == "failed"


class CacheIndex:
    """Fast lookup of cached files by stem, one directory listing per folder."""

    def __init__(self, mods_dir: Path):
        self._mods_dir = mods_dir
        self._folders: Dict[str, Dict[str, str]] = {}
        self._lock = threading.Lock()

    def find(self, folder: str, stem: str) -> Optional[Path]:
        with self._lock:
            if folder not in self._folders:
                self._folders[folder] = self._scan(folder)
            name = self._folders[folder].get(stem)
        return self._mods_dir / folder / name if name else None

    def add(self, folder: str, path: Path) -> None:
        with self._lock:
            self._folders.setdefault(folder, {})[path.stem] = path.name

    def _scan(self, folder: str) -> Dict[str, str]:
        directory = self._mods_dir / folder
        if not directory.is_dir():
            return {}
        files = {}
        for entry in os.scandir(directory):
            if entry.is_file() and not entry.name.endswith(".part"):
                stem, _ = os.path.splitext(entry.name)
                files.setdefault(stem, entry.name)
        return files


def lookup_cached(index: CacheIndex, url: str, folder: Optional[str]) -> Optional[Path]:
    """Return the cached file for [url], copying it from an alternate Steam
    spelling to the name TTS expects if needed. A [folder] of None searches
    every cache folder."""
    stem = cache_stem(url)
    folders = [folder] if folder else list(ASSET_FOLDERS)
    for name in folders:
        path = index.find(name, stem)
        if path:
            return path
    for alt in alternate_urls(url):
        for name in folders:
            alt_path = index.find(name, cache_stem(alt))
            if alt_path:
                target = alt_path.with_name(stem + alt_path.suffix)
                shutil.copy2(alt_path, target)
                index.add(name, target)
                return target
    return None


def download_assets(
    mods_dir: Path,
    assets: Dict[str, Optional[str]],
    workers: int = 8,
    retries: int = 3,
    on_result: Optional[Callable[[int, int, AssetResult], None]] = None,
    should_stop: Optional[Callable[[], bool]] = None,
) -> List[AssetResult]:
    """Make sure every {url: folder} in [assets] is in the cache (folder None:
    detect the type from the download).

    Files already cached are not downloaded again. [on_result] is called
    after each asset with (done_count, total, result). When [should_stop]
    returns True, the remaining assets are not started and Cancelled is raised.
    """
    index = CacheIndex(mods_dir)
    total = len(assets)
    results: List[AssetResult] = []
    done = 0
    lock = threading.Lock()

    def work(item):
        url, folder = item
        if should_stop and should_stop():
            raise Cancelled()
        try:
            result = _ensure_asset(mods_dir, index, url, folder, retries)
        except Exception as exc:  # Never let one asset abort the whole backup.
            result = AssetResult(url, folder, "failed" if folder else "skipped", error=str(exc))
        nonlocal done
        with lock:
            done += 1
            results.append(result)
            if on_result:
                on_result(done, total, result)
        return result

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        list(pool.map(work, assets.items()))
    return results


def _ensure_asset(
    mods_dir: Path, index: CacheIndex, url: str, folder: Optional[str], retries: int
) -> AssetResult:
    cached = lookup_cached(index, url, folder)
    if cached:
        return AssetResult(url, cached.parent.name, "cached", cached)
    failed = "failed" if folder else "skipped"
    if not url.strip().lower().startswith(("http://", "https://")):
        return AssetResult(url, folder, failed, error="not a web URL")

    errors = []
    for candidate in download_candidates(url):
        for attempt in range(retries):
            try:
                path = _fetch(candidate, mods_dir, cache_stem(url), folder)
                index.add(path.parent.name, path)
                return AssetResult(url, path.parent.name, "downloaded", path)
            except urllib.error.HTTPError as exc:
                errors.append(f"{candidate}: HTTP {exc.code}")
                if exc.code in (400, 401, 403, 404, 410):
                    break  # Retrying will not help; try the next candidate.
            except NotAnAsset as exc:
                errors.append(f"{candidate}: {exc}")
                break
            except (urllib.error.URLError, OSError, ValueError) as exc:
                reason = getattr(exc, "reason", exc)
                errors.append(f"{candidate}: {reason}")
            if attempt + 1 < retries:
                time.sleep(1.5 * (attempt + 1))
    return AssetResult(url, folder, failed, error=errors[-1] if errors else "unknown")


class NotAnAsset(ValueError):
    """The server answered, but not with a file TTS could use; retrying won't help."""


def _fetch(url: str, mods_dir: Path, stem: str, folder: Optional[str]) -> Path:
    """Download [url] into Mods/<folder>/[stem]<ext> and return the path.

    A [folder] of None is decided from the file's first bytes. Writes to a
    .part file first so an interrupted download never leaves a broken file.
    """
    mods_dir.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    tmp = mods_dir / f"{stem[:150]}.{threading.get_ident()}.part"
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            head = response.read(CHUNK)
            if not head:
                raise ValueError("empty response")
            if folder != "Text" and looks_like_html(head):
                raise NotAnAsset("server returned a web page instead of the file")
            if folder is None:
                folder = detect_folder(head)
                if folder is None:
                    raise NotAnAsset("not an image, model, sound, PDF or asset bundle")
            with open(tmp, "wb") as out:
                out.write(head)
                shutil.copyfileobj(response, out, CHUNK)
        directory = mods_dir / folder
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / (stem + sniff_extension(folder, head, url))
        os.replace(tmp, target)
        return target
    finally:
        if tmp.exists():
            tmp.unlink()
