"""Locate the Tabletop Simulator data folders and the mod files inside them."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Dict, List, Optional

# Asset cache folders inside Mods/, in the order TTS shows them.
ASSET_FOLDERS = ("Images", "Models", "Assetbundles", "Audio", "PDF", "Text")

WORKSHOP_INDEX = "WorkshopFileInfos.json"
SAVES_INDEX = "SaveFileInfos.json"
# JSON files TTS keeps next to mods/saves that are not mods themselves.
INDEX_FILES = {WORKSHOP_INDEX, SAVES_INDEX}


def default_mods_dir() -> Path:
    """Return the platform's default TTS Mods folder (it may not exist)."""
    home = Path.home()
    if sys.platform == "darwin":
        return home / "Library" / "Tabletop Simulator" / "Mods"
    if sys.platform.startswith("win"):
        return home / "Documents" / "My Games" / "Tabletop Simulator" / "Mods"
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg) if xdg else home / ".local" / "share"
    return base / "Tabletop Simulator" / "Mods"


def saves_dir(mods_dir: Path) -> Path:
    """TTS keeps saves in a folder next to Mods/."""
    return mods_dir.parent / "Saves"


def workshop_dir(mods_dir: Path) -> Path:
    return mods_dir / "Workshop"


@dataclass
class ModFile:
    """A mod or save JSON file that can be backed up."""

    json_path: Path
    # "workshop" for files in Mods/Workshop, "save" for anything else.
    kind: str
    # The file stem: the Workshop id for subscribed mods, otherwise a name.
    id: str
    # Folder inside Workshop/ or Saves/ (TTS in-game folders), "" for the top level.
    subfolder: str = ""

    @property
    def thumbnail(self) -> Path:
        return self.json_path.with_suffix(".png")

    @property
    def archive_folder(self) -> str:
        """Folder of this file inside a backup package, e.g. "Workshop/Campaign Games"."""
        top = "Workshop" if self.kind == "workshop" else "Saves"
        return f"{top}/{self.subfolder}" if self.subfolder else top

    @property
    def display_path(self) -> str:
        """Path relative to Workshop/ (or Saves/), as shown to the user."""
        return f"{self.subfolder}/{self.json_path.name}" if self.subfolder else self.json_path.name


def resolve_mod(arg: str, mods_dir: Path) -> ModFile:
    """Turn a CLI argument (workshop id or path to a JSON file) into a [ModFile].

    A bare id is looked up in Workshop/ and all its subfolders.
    Raises FileNotFoundError when nothing matches.
    """
    workshop = workshop_dir(mods_dir)
    if arg.isdigit():
        for mod in list_workshop_mods(mods_dir):
            if mod.id == arg:
                return mod
        raise FileNotFoundError(f"No mod with id {arg} in {workshop}")

    path = Path(arg).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"File not found: {arg}")
    for root, kind in ((workshop, "workshop"), (saves_dir(mods_dir), "save")):
        subfolder = _subfolder_of(path, root)
        if subfolder is not None:
            return ModFile(path, kind, path.stem, subfolder)
    return ModFile(path, "save", path.stem)


def list_workshop_mods(mods_dir: Path) -> List[ModFile]:
    """All mod JSON files in Mods/Workshop and its subfolders.

    Sorted with top-level mods first, then by folder and id.
    """
    workshop = workshop_dir(mods_dir)
    if not workshop.is_dir():
        return []
    root = workshop.resolve()
    mods = []
    for path in workshop.rglob("*.json"):
        if path.name in INDEX_FILES or not path.is_file():
            continue
        subfolder = _subfolder_of(path.resolve(), root) or ""
        mods.append(ModFile(path, "workshop", path.stem, subfolder))
    return sorted(
        mods,
        key=lambda m: (m.subfolder != "", m.subfolder.lower(), not m.id.isdigit(), len(m.id), m.id.lower()),
    )


def read_index_names(mods_dir: Path) -> Dict[str, str]:
    """Map path relative to Workshop/ (e.g. "Campaign Games/123.json") -> mod
    name, from TTS's WorkshopFileInfos.json."""
    workshop = workshop_dir(mods_dir)
    try:
        entries = json.loads((workshop / WORKSHOP_INDEX).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    root = workshop.resolve()
    names = {}
    for entry in entries if isinstance(entries, list) else []:
        # TTS writes paths like ".../Mods//Workshop/x.json"; normalise first.
        directory = os.path.normpath(str(entry.get("Directory", "")).replace("\\", "/"))
        subfolder = _subfolder_of(Path(directory), root)
        key = Path(directory).name if subfolder is None else (
            f"{subfolder}/{Path(directory).name}" if subfolder else Path(directory).name
        )
        names[key] = entry.get("Name", "")
    return names


def load_json(path: Path) -> dict:
    """Load a TTS JSON file (TTS sometimes writes a UTF-8 BOM)."""
    return json.loads(path.read_text(encoding="utf-8-sig"))


def mod_name(data: dict, fallback: str) -> str:
    name = data.get("SaveName") or data.get("Nickname") or ""
    return str(name).strip() or fallback


def safe_filename(name: str, max_length: int = 100) -> str:
    """Make [name] usable as a file name on macOS, Windows and Linux."""
    cleaned = "".join(
        "_" if c in '<>:"/\\|?*' or ord(c) < 32 else c for c in name
    )
    cleaned = " ".join(cleaned.split()).strip(" .")
    return cleaned[:max_length].rstrip(" .") or "mod"


def optional_dir(path: Optional[str]) -> Path:
    return Path(path).expanduser().resolve() if path else default_mods_dir()


def _subfolder_of(path: Path, root: Path) -> Optional[str]:
    """Folder of [path] relative to [root] ("" for directly inside it), or
    None when [path] is not inside [root]."""
    try:
        relative = path.parent.relative_to(root.resolve())
    except ValueError:
        return None
    text = PurePosixPath(*relative.parts).as_posix() if relative.parts else ""
    return "" if text == "." else text
