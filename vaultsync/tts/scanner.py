"""Find every downloadable asset URL in a TTS mod/save JSON."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

# JSON key -> Mods/ subfolder where TTS caches the file.
KEY_FOLDERS = {
    # Images (custom tiles/tokens/boards, decks, model textures, sky, table, decals, LUT)
    "ImageURL": "Images",
    "ImageSecondaryURL": "Images",
    "FaceURL": "Images",
    "BackURL": "Images",
    "DiffuseURL": "Images",
    "NormalURL": "Images",
    "SkyURL": "Images",
    "TableURL": "Images",
    "LutURL": "Images",
    # 3D models
    "MeshURL": "Models",
    "ColliderURL": "Models",
    # Unity asset bundles
    "AssetbundleURL": "Assetbundles",
    "AssetbundleSecondaryURL": "Assetbundles",
    # PDFs
    "PDFUrl": "PDF",
    # Music player
    "CurrentAudioURL": "Audio",
}

# CustomUIAssets entries carry a numeric Type instead of a specific key.
UI_ASSET_TYPES = {0: "Images", 1: "Assetbundles"}

# Text fields that may contain links the mod loads at runtime.
SCRIPT_KEYS = ("LuaScript", "LuaScriptState", "XmlUI")

_URL_IN_TEXT = re.compile(r"""https?://[^\s"'<>()\[\]{},;\\]+""")
_STEAM_FILE = re.compile(
    r"^https?://(cloud-\d+\.steamusercontent\.com|steamusercontent-a\.akamaihd\.net)/ugc/\d+/[0-9A-Fa-f]+/?$"
)
_ASSET_EXTENSION = re.compile(
    r"\.(png|jpe?g|gif|webp|bmp|obj|unity3d|pdf|mp3|ogg|wav|flac)(\?[^/]*)?$", re.I
)


def find_assets(data: Any, include_scripts: bool = True) -> Dict[str, Optional[str]]:
    """Return {url: folder} for all assets referenced anywhere in [data].

    The walk is recursive, so assets inside bags, decks, states and child
    objects are found too. The URL strings are returned exactly as written
    in the JSON, because TTS derives the cache file name from that text.

    With [include_scripts], links that only appear inside Lua scripts or XML
    UI are added too. Their folder is None: the type is only known once the
    file is downloaded, and some of these links may not be assets at all.
    """
    found: Dict[str, Optional[str]] = {}
    script_texts: List[str] = []
    _walk(data, found, script_texts)
    if include_scripts:
        for text in script_texts:
            for url in _URL_IN_TEXT.findall(text):
                url = url.rstrip(".")
                if url not in found and _looks_like_asset(url):
                    found[url] = None
    return found


def _looks_like_asset(url: str) -> bool:
    # Scripts also contain links to web pages and APIs; only take links that
    # clearly point at a file, so we don't report hundreds of false "missing".
    return bool(_STEAM_FILE.match(url) or _ASSET_EXTENSION.search(url))


def _add(found: Dict[str, Optional[str]], url: Any, folder: str) -> None:
    if isinstance(url, str) and url.strip() and found.get(url) is None:
        found[url] = folder


def _walk(node: Any, found: Dict[str, Optional[str]], script_texts: List[str]) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if key in KEY_FOLDERS:
                _add(found, value, KEY_FOLDERS[key])
            elif key in SCRIPT_KEYS and isinstance(value, str) and "http" in value:
                script_texts.append(value)
            elif key == "CustomUIAssets" and isinstance(value, list):
                for asset in value:
                    if isinstance(asset, dict):
                        folder = UI_ASSET_TYPES.get(asset.get("Type"), "Images")
                        _add(found, asset.get("URL"), folder)
            elif key == "AudioLibrary" and isinstance(value, list):
                for track in value:
                    if isinstance(track, dict):
                        _add(found, track.get("Item1"), "Audio")
            if isinstance(value, (dict, list)):
                _walk(value, found, script_texts)
    elif isinstance(node, list):
        for item in node:
            _walk(item, found, script_texts)
