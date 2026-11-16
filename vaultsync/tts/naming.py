"""Reproduce how TTS names the files in its asset cache."""

from __future__ import annotations

import re
from typing import List, Optional

_NON_ALNUM = re.compile(r"[^A-Za-z0-9]")
_LEGACY_STEAM = re.compile(r"^https?://cloud-\d+\.steamusercontent\.com/", re.I)
_AKAMAI_STEAM = re.compile(r"^https?://steamusercontent-a\.akamaihd\.net/", re.I)

AKAMAI_PREFIX = "https://steamusercontent-a.akamaihd.net/"
LEGACY_PREFIX = "http://cloud-3.steamusercontent.com/"


def cache_stem(url: str) -> str:
    """File name (without extension) TTS uses for [url]: only letters and digits."""
    return _NON_ALNUM.sub("", url)


def download_candidates(url: str) -> List[str]:
    """URLs to try, in order, to fetch [url].

    Steam retired the old cloud-N.steamusercontent.com hosts; the same files
    are served from the akamai host, so old mods still download.
    """
    url = url.strip()
    candidates = [url]
    if _LEGACY_STEAM.match(url):
        candidates.insert(0, _LEGACY_STEAM.sub(AKAMAI_PREFIX, url))
    return candidates


def alternate_urls(url: str) -> List[str]:
    """Other spellings of the same Steam file that may already be cached."""
    url = url.strip()
    if _LEGACY_STEAM.match(url):
        return [_LEGACY_STEAM.sub(AKAMAI_PREFIX, url)]
    if _AKAMAI_STEAM.match(url):
        return [_AKAMAI_STEAM.sub(LEGACY_PREFIX, url)]
    return []


def sniff_extension(folder: str, head: bytes, url: str) -> str:
    """Pick the extension TTS would use, based on the first bytes of the file."""
    if folder == "Images":
        if head.startswith(b"\xff\xd8\xff"):
            return ".jpg"
        return ".png"
    if folder == "Models":
        return ".obj"
    if folder == "Assetbundles":
        return ".unity3d"
    if folder == "PDF":
        return ".pdf"
    if folder == "Text":
        return ".txt"
    if folder == "Audio":
        if head.startswith(b"OggS"):
            return ".ogg"
        if head.startswith(b"RIFF") and head[8:12] == b"WAVE":
            return ".wav"
        if head.startswith(b"fLaC"):
            return ".flac"
        if head[4:8] == b"ftyp":
            return ".m4a"
        if head.startswith(b"ID3") or head[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
            return ".mp3"
        lowered = url.lower().split("?")[0]
        for ext in (".ogg", ".wav", ".mp3", ".flac", ".m4a"):
            if lowered.endswith(ext):
                return ext
        return ".mp3"
    return ""


def detect_folder(head: bytes) -> Optional[str]:
    """Guess the cache folder from a file's first bytes, for assets whose
    type is not known in advance (links found in scripts). None = not an asset."""
    if head.startswith((b"\x89PNG", b"\xff\xd8\xff", b"GIF8")) or (
        head.startswith(b"RIFF") and head[8:12] == b"WEBP"
    ):
        return "Images"
    if head.startswith((b"UnityFS", b"UnityWeb", b"UnityRaw")):
        return "Assetbundles"
    if head.startswith(b"%PDF"):
        return "PDF"
    if head.startswith((b"OggS", b"ID3", b"fLaC", b"\xff\xfb", b"\xff\xf3")) or (
        head.startswith(b"RIFF") and head[8:12] == b"WAVE"
    ):
        return "Audio"
    if _looks_like_obj(head):
        return "Models"
    return None


def _looks_like_obj(head: bytes) -> bool:
    try:
        text = head[:4096].decode("ascii")
    except UnicodeDecodeError:
        return False
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    vertices = sum(1 for line in lines if line.startswith(("v ", "vt ", "vn ", "f ")))
    return vertices >= 3 and vertices >= len(lines) // 2


def looks_like_html(head: bytes) -> bool:
    """True for error/login pages that some hosts return instead of the file."""
    start = head.lstrip()[:64].lower()
    return start.startswith(b"<!doctype html") or start.startswith(b"<html")
