"""Pack a mod and its cached assets into one zip, and unpack it again."""

from __future__ import annotations

import json
import os
import time
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Callable, Iterable, List, Optional, Tuple

from vaultsync import __version__
from .downloader import AssetResult
from .mods import (
    ASSET_FOLDERS,
    SAVES_INDEX,
    WORKSHOP_INDEX,
    ModFile,
    load_json,
    mod_name,
    safe_filename,
    saves_dir,
)

MANIFEST = "manifest.json"
FORMAT = "tts-backup"
FORMAT_VERSION = 1
ALLOWED_TOP = set(ASSET_FOLDERS) | {"Workshop", "Saves"}
# Already-compressed formats gain nothing from deflate, so store them as is.
STORED_SUFFIXES = {".png", ".jpg", ".jpeg", ".unity3d", ".mp3", ".ogg", ".m4a", ".flac", ".pdf"}


class PackageError(Exception):
    pass


def build_package(
    mod: ModFile, results: List[AssetResult], out_dir: Path,
    on_file: Optional[Callable[[int, int], None]] = None,
) -> Path:
    """Write `<name> (<id>).zip` to [out_dir] and return its path.

    Mods in a TTS subfolder go to the same subfolder of [out_dir]. Zip paths
    mirror the Mods folder, so restoring is just unzipping into it. Assets
    that could not be found are listed under "missing" in the manifest.
    """
    data = load_json(mod.json_path)
    name = mod_name(data, mod.id)
    label = f"{name} ({mod.id})" if mod.id.isdigit() else name
    zip_path = _zip_path(out_dir, mod.subfolder, label)

    entries = [(mod.json_path, f"{mod.archive_folder}/{mod.json_path.name}")]
    if mod.thumbnail.is_file():
        entries.append((mod.thumbnail, f"{mod.archive_folder}/{mod.thumbnail.name}"))
    manifest = {
        "kind": mod.kind,
        "id": mod.id,
        "name": name,
        "subfolder": mod.subfolder,
        "json": entries[0][1],
    }
    _write_package(zip_path, manifest, entries, results, on_file)
    return zip_path


def build_extras_package(
    mod: ModFile,
    title: str,
    files: List[Path],
    results: List[AssetResult],
    out_dir: Path,
    mods_dir: Path,
    exclude: Iterable[Path] = (),
    on_file: Optional[Callable[[int, int], None]] = None,
) -> Path:
    """Write `<mod name> - <title>.zip` with extra content of [mod]: [files]
    from the Saves folder (e.g. Saved Objects) plus the assets they use.

    Assets in [exclude] (already in the mod's own zip) are left out.
    """
    name = mod_name(load_json(mod.json_path), mod.id)
    zip_path = _zip_path(out_dir, mod.subfolder, f"{name} - {title}")
    root = saves_dir(mods_dir)
    entries = [(path, "Saves/" + path.relative_to(root).as_posix()) for path in files]
    skip = {Path(p) for p in exclude}
    manifest = {
        "kind": "extras",
        "id": mod.id,
        "name": f"{name} - {title}",
        "subfolder": mod.subfolder,
        "json": None,
    }
    _write_package(zip_path, manifest, entries, [r for r in results if r.path not in skip], on_file)
    return zip_path


def _zip_path(out_dir: Path, subfolder: str, label: str) -> Path:
    if subfolder:
        out_dir = out_dir.joinpath(*(safe_filename(p) for p in subfolder.split("/")))
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir / f"{safe_filename(label, 120)}.zip"


def _write_package(
    zip_path: Path, manifest: dict, entries: List[Tuple[Path, str]], results: List[AssetResult],
    on_file: Optional[Callable[[int, int], None]] = None,
) -> None:
    """Add the asset files of [results] to [entries] and write the zip with
    its manifest. Writes to a .part file first, so a failed run never leaves
    a half-written zip with the final name. [on_file](done, total) is called
    after each file; it may raise to stop (the .part file is removed)."""
    entries = list(entries)
    missing = []
    skipped = []
    seen = {arcname for _, arcname in entries}
    for result in sorted(results, key=lambda r: (r.folder or "", r.url)):
        if result.path and result.path.is_file():
            arcname = f"{result.path.parent.name}/{result.path.name}"
            if arcname not in seen:
                seen.add(arcname)
                entries.append((result.path, arcname))
        elif result.missing:
            missing.append({"url": result.url, "folder": result.folder, "error": result.error})
        else:
            skipped.append({"url": result.url, "error": result.error})

    manifest = {
        "format": FORMAT,
        "format_version": FORMAT_VERSION,
        "tool_version": __version__,
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        **manifest,
        "files": [arcname for _, arcname in entries],
        "missing": missing,
        "skipped_script_links": skipped,
    }

    tmp = zip_path.with_name(zip_path.name + ".part")
    try:
        with zipfile.ZipFile(tmp, "w", allowZip64=True) as archive:
            archive.writestr(MANIFEST, json.dumps(manifest, indent=2, ensure_ascii=False))
            for number, (source, arcname) in enumerate(entries, 1):
                compress = (
                    zipfile.ZIP_STORED
                    if source.suffix.lower() in STORED_SUFFIXES
                    else zipfile.ZIP_DEFLATED
                )
                archive.write(source, arcname, compress_type=compress)
                if on_file:
                    on_file(number, len(entries))
        os.replace(tmp, zip_path)
    finally:
        if tmp.exists():
            tmp.unlink()


@dataclass
class RestoreReport:
    manifest: dict
    written: List[str] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)
    index_updated: bool = False


def read_manifest(zip_path: Path) -> dict:
    with zipfile.ZipFile(zip_path) as archive:
        return _manifest(archive)


def restore_package(
    zip_path: Path, mods_dir: Path, overwrite: bool = False,
    on_file: Optional[Callable[[int, int], None]] = None,
) -> RestoreReport:
    """Extract a backup into [mods_dir] (saves go to the Saves folder next to it).

    Existing files are kept unless [overwrite] is True. The mod is also added
    to TTS's mod/save list so it shows up in the game.
    """
    with zipfile.ZipFile(zip_path) as archive:
        manifest = _manifest(archive)
        report = RestoreReport(manifest)
        members = [i for i in archive.infolist() if not i.is_dir() and i.filename != MANIFEST]
        for number, info in enumerate(members, 1):
            if on_file:
                on_file(number - 1, len(members))
            target = _target_path(info.filename, mods_dir)
            if target.exists() and not overwrite:
                report.skipped.append(info.filename)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_name(target.name + ".part")
            try:
                with archive.open(info) as src, open(tmp, "wb") as dst:
                    while True:
                        chunk = src.read(1024 * 1024)
                        if not chunk:
                            break
                        dst.write(chunk)
                os.replace(tmp, target)
            finally:
                if tmp.exists():
                    tmp.unlink()
            report.written.append(info.filename)

    json_name = manifest.get("json")
    if json_name:
        report.index_updated = _register(
            _target_path(json_name, mods_dir), manifest.get("name", ""), manifest.get("kind"), mods_dir
        )
    return report


def _manifest(archive: zipfile.ZipFile) -> dict:
    try:
        manifest = json.loads(archive.read(MANIFEST).decode("utf-8"))
    except KeyError:
        raise PackageError("not a tts-backup package (no manifest.json)")
    if manifest.get("format") != FORMAT:
        raise PackageError("not a tts-backup package (unknown format)")
    if manifest.get("format_version", 0) > FORMAT_VERSION:
        raise PackageError("package was made by a newer version of tts-backup")
    return manifest


def _target_path(arcname: str, mods_dir: Path) -> Path:
    """Map a zip entry to its destination, refusing anything outside the
    known TTS folders (protects against malicious zips).

    Workshop/ and Saves/ entries may sit in subfolders (TTS in-game folders);
    asset cache entries are always directly inside their folder.
    """
    path = PurePosixPath(arcname)
    parts = path.parts
    max_depth = 2 if parts and parts[0] in ASSET_FOLDERS else 8
    if (
        path.is_absolute()
        or "\\" in arcname
        or not 2 <= len(parts) <= max_depth
        or parts[0] not in ALLOWED_TOP
        or any(p in ("", ".", "..") or ":" in p for p in parts[1:])
    ):
        raise PackageError(f"refusing unsafe path in package: {arcname!r}")
    if parts[0] == "Saves":
        return saves_dir(mods_dir).joinpath(*parts[1:])
    return mods_dir.joinpath(*parts)


def _register(json_path: Path, name: str, kind: Optional[str], mods_dir: Path) -> bool:
    """Add [json_path] to WorkshopFileInfos.json / SaveFileInfos.json if it
    is not listed yet. Returns True when the index was changed."""
    if kind == "workshop":
        index = mods_dir / "Workshop" / WORKSHOP_INDEX
    else:
        index = saves_dir(mods_dir) / SAVES_INDEX
    try:
        entries = json.loads(index.read_text(encoding="utf-8-sig")) if index.exists() else []
    except ValueError:
        return False  # Never clobber an index we cannot read.
    if not isinstance(entries, list):
        return False
    wanted = os.path.normcase(os.path.normpath(str(json_path)))
    for entry in entries:
        directory = str(entry.get("Directory", "")).replace("\\", "/")
        if os.path.normcase(os.path.normpath(directory)) == wanted:
            return False
    entries.append(
        {
            "Directory": json_path.as_posix(),
            "Name": name or json_path.stem,
            "UpdateTime": int(time.time()),
        }
    )
    tmp = index.with_name(index.name + ".part")
    tmp.write_text(json.dumps(entries, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, index)
    return True
