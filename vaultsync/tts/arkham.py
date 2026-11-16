"""Downloadable content of the Arkham Horror LCG "SCE" mod.

The mod's in-game download menu fetches content packs (campaigns, scenarios,
player cards, language packs) from GitHub at runtime. TTS does not keep those
files, so this module downloads them itself and stores each pack as a TTS
"Saved Object". Saved Objects can be placed from the in-game Objects menu,
without internet once their assets are cached too.
"""

from __future__ import annotations

import json
import re
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from vaultsync.common import Cancelled

from .downloader import USER_AGENT
from .naming import looks_like_html
from .mods import safe_filename, saves_dir

MARKER = "Chr1Z93/SCED-downloads"
LATEST_REPO = "https://github.com/Chr1Z93/SCED-downloads/releases/latest/download/"
VERSION_REPO = "https://github.com/Chr1Z93/SCED-downloads/releases/download/{tag}/"
DEFAULT_MIRRORS = ("https://gh.llkk.cc/", "https://gh-proxy.com/")
# Folder inside Saves/Saved Objects that holds the packs.
SAVED_OBJECTS_FOLDER = "Arkham SCE Downloads"
TYPE_FOLDERS = {
    "campaign": "Campaigns",
    "scenario": "Scenarios",
    "playercards": "Player Cards",
    "language-pack": "Language Packs",
}
DEFAULT_LANGUAGES = ("German",)
TIMEOUT = 120


@dataclass
class Pack:
    name: str
    type: str
    filename: str
    boxart: str = ""

    @property
    def language(self) -> str:
        # Language packs are named "<Language> - <Part>".
        return self.name.split(" - ")[0].strip() if self.type == "language-pack" else ""


@dataclass
class ArkhamContent:
    """Result of [fetch_packs]: the saved-object files and what went wrong."""

    files: List[Path] = field(default_factory=list)
    failed: List[str] = field(default_factory=list)
    source: str = ""
    # Set when the pack list itself could not be fetched; [files] then holds
    # the packs downloaded by an earlier run, if any.
    error: str = ""

    @property
    def json_files(self) -> List[Path]:
        return [f for f in self.files if f.suffix == ".json"]


def is_arkham_sce(data: dict) -> bool:
    """True when [data] is the Arkham Horror LCG SCE mod (has the download menu)."""
    return MARKER in str(data.get("LuaScript", ""))


def looks_like_unrecognised_arkham(data: dict) -> bool:
    """True for an Arkham mod with a download menu whose source this tool does
    not know (e.g. after the mod moved its downloads). Its packs can't be
    backed up, so the user should be told instead of skipping them silently."""
    if is_arkham_sce(data):
        return False
    script = str(data.get("LuaScript", ""))
    name = str(data.get("SaveName", ""))
    return "arkham" in name.lower() and "WebRequest" in script and "library.json" in script


def mod_version(data: dict) -> str:
    """The mod's MOD_VERSION (e.g. "4.9.3"), or "unknown version"."""
    match = re.search(r'MOD_VERSION\s*=\s*"([^"]+)"', str(data.get("LuaScript", "")))
    return safe_filename(match.group(1)) if match else "unknown version"


def content_folder(mods_dir: Path, data: dict) -> Path:
    """Saved Objects folder for this mod version's packs. Each version gets its
    own folder, so two installed versions never overwrite each other's packs."""
    return saves_dir(mods_dir) / "Saved Objects" / SAVED_OBJECTS_FOLDER / mod_version(data)


def local_content(mods_dir: Path, data: dict) -> ArkhamContent:
    """Packs downloaded by an earlier run (used without downloading)."""
    folder = content_folder(mods_dir, data)
    files = sorted(
        p for p in folder.rglob("*") if p.is_file() and not p.name.endswith(".part")
    ) if folder.is_dir() else []
    return ArkhamContent(files=files, source=str(folder))


def wanted(pack: Pack, languages: Iterable[str]) -> bool:
    """Language packs only for the chosen [languages] ("all" = every one)."""
    if pack.type != "language-pack":
        return True
    chosen = {lang.strip().lower() for lang in languages}
    return "all" in chosen or pack.language.lower() in chosen


def fetch_packs(
    data: dict,
    mods_dir: Path,
    languages: Iterable[str] = DEFAULT_LANGUAGES,
    workers: int = 8,
    on_progress: Optional[Callable[[int, int], None]] = None,
    should_stop: Optional[Callable[[], bool]] = None,
) -> ArkhamContent:
    """Download the content packs this mod version offers into Saved Objects.

    Uses the same source as the mod: its pinned release when the mod version
    has one, otherwise the latest release, with the mod's mirrors as fallback.
    Packs are written as `<Saved Objects>/Arkham SCE Downloads/<Type>/<Name>.json`
    with the box art as thumbnail. Files that did not change are not rewritten.
    """
    script = str(data.get("LuaScript", ""))
    mirrors = _mirrors(script)
    repo = _source_repo(script, mirrors)
    packs = _parse_library(json.loads(_get(repo + "library.json", mirrors)))
    selected = [p for p in packs if wanted(p, languages)]
    result = ArkhamContent(source=repo)
    folder = content_folder(mods_dir, data)
    done = 0

    def work(pack: Pack) -> Tuple[Pack, List[Path], str]:
        if should_stop and should_stop():
            raise Cancelled()
        try:
            return pack, _save_pack(pack, repo, mirrors, folder), ""
        except Exception as exc:  # One broken pack must not stop the others.
            return pack, [], str(exc)

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for pack, files, error in pool.map(work, selected):
            done += 1
            result.files.extend(files)
            if error:
                result.failed.append(f"{pack.name}: {error}")
            if on_progress:
                on_progress(done, len(selected))
    result.files.sort()
    return result


def _parse_library(library) -> List[Pack]:
    """Read the pack list; raises ValueError if its format is not the expected one."""
    items = library.get("content") if isinstance(library, dict) else None
    if not isinstance(items, list):
        raise ValueError("the pack list has an unknown format (the mod may have changed)")
    packs = [
        Pack(str(item["name"]), str(item.get("type", "")), str(item["filename"]), str(item.get("boxart") or ""))
        for item in items
        if isinstance(item, dict) and item.get("filename") and item.get("name")
    ]
    if not packs:
        raise ValueError("the pack list is empty or has an unknown format")
    return packs


def _save_pack(pack: Pack, repo: str, mirrors: List[str], folder: Path) -> List[Path]:
    obj = json.loads(_get(repo + pack.filename + ".json", mirrors))
    if not isinstance(obj, dict) or "Name" not in obj:
        raise ValueError("pack file has an unknown format (expected one TTS object)")
    target_dir = folder / TYPE_FOLDERS.get(pack.type, "Other")
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{safe_filename(pack.name)}.json"
    _write_if_changed(target, json.dumps(_saved_object(obj), indent=2, ensure_ascii=False).encode("utf-8"))
    files = [target]

    thumbnail = target.with_suffix(".png")
    if pack.boxart and not thumbnail.exists():
        try:
            # TTS reads PNG or JPEG data from the .png thumbnail.
            thumbnail.write_bytes(_get(pack.boxart, []))
        except Exception:
            pass  # The thumbnail is only cosmetic.
    if thumbnail.exists():
        files.append(thumbnail)
    return files


def _saved_object(obj: dict) -> dict:
    """Wrap one object the way TTS stores files in Saved Objects."""
    return {
        "SaveName": "",
        "Date": "",
        "VersionNumber": "",
        "GameMode": "",
        "GameType": "",
        "GameComplexity": "",
        "Tags": [],
        "Gravity": 0.5,
        "PlayArea": 0.5,
        "Table": "",
        "Sky": "",
        "Note": "",
        "TabStates": {},
        "LuaScript": "",
        "LuaScriptState": "",
        "XmlUI": "",
        "ObjectStates": [obj],
    }


def _write_if_changed(path: Path, content: bytes) -> None:
    if path.exists() and path.read_bytes() == content:
        return
    tmp = path.with_name(path.name + ".part")
    tmp.write_bytes(content)
    tmp.replace(path)


def _mirrors(script: str) -> List[str]:
    match = re.search(r"FALLBACK_MIRRORS\s*=\s*\{([^}]*)\}", script)
    found = re.findall(r'"(https?://[^"]+)"', match.group(1)) if match else []
    return found or list(DEFAULT_MIRRORS)


def _source_repo(script: str, mirrors: List[str]) -> str:
    """The release the mod itself would use (see compareVersion in its script)."""
    version = re.search(r'MOD_VERSION\s*=\s*"([^"]+)"', script)
    try:
        meta = json.loads(_get(LATEST_REPO + "modversion.json", mirrors))
        pinned = (meta.get("setSpecificDownloadVersion") or [{}])[0]
        if version and version.group(1) in pinned:
            return VERSION_REPO.format(tag=pinned[version.group(1)])
    except (OSError, ValueError, KeyError, IndexError, AttributeError):
        pass  # Same as the mod: without version info, use the latest release.
    return LATEST_REPO


def _get(url: str, mirrors: List[str]) -> bytes:
    """GET [url], falling back to the mirror prefixes like the mod does."""
    last_error: Optional[Exception] = None
    for prefix in [""] + list(mirrors):
        request = urllib.request.Request(prefix + url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                body = response.read()
            if looks_like_html(body[:256]):
                raise ValueError("got a web page instead of the file")
            return body
        except (OSError, ValueError) as exc:
            last_error = exc
    raise OSError(f"could not download {url}: {last_error}")


def collect_assets(content: ArkhamContent, find_assets: Callable[[dict], Dict]) -> Dict:
    """All asset links used by the downloaded packs."""
    assets: Dict = {}
    for path in content.json_files:
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            continue
        for url, folder in find_assets(data).items():
            if assets.get(url) is None:
                assets[url] = folder
    return assets
