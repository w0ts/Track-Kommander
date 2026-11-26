"""Traktor's music folders (Preferences ▸ File Management): from its memory inside Traktor,
else from ``Traktor Settings.tsi``. TK's DIRECTORY (SHORT) column and add-ons use them.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

# Inside Traktor: reads one of Traktor's settings from its memory (None when unreadable).
# Registered by the UI at start (workers.native_settings.setting): core never imports workers.
_native_setting: Callable[[str], str | None] | None = None


def set_native_setting(reader: Callable[[str], str | None] | None) -> None:
    """Read Traktor's settings through *reader* inside Traktor (None: nothing to read)."""
    global _native_setting
    _native_setting = reader


def _mac_path(value: str) -> Path | None:
    """Traktor's "Volume:Users:me:Music:Tracks:" as a path (a Windows
    Traktor's "C:\\Users\\me\\Music\\" as it is)."""
    if re.match(r"^[A-Za-z]:[\\/]", value):
        return Path(value)
    parts = [p for p in value.split(":") if p]
    if len(parts) < 2:
        return None
    on_boot_volume = Path("/", parts[1]).exists() or parts[0] in ("Macintosh HD", "MacOS")
    return Path("/", *parts[1:]) if on_boot_volume else Path("/Volumes", *parts)


def _folders(value: str) -> list[Path]:
    """Traktor's ";"-joined "Browser.Dir.Music" value as paths. The settings file spells each
    one "Volume:Users:me:Music:Tracks:"; Traktor's memory holds plain paths ("/Users/me/Music/
    Tracks", measured inside Traktor 2026-11-24)."""
    folders = (Path(v) if v.startswith("/") else _mac_path(v) for v in value.split(";") if v)
    return [f for f in folders if f is not None]


def traktor_music_folders() -> list[Path]:
    """Traktor's music folders (Preferences ▸ File Management), in its order.

    Inside Traktor from its memory, never from ``Traktor Settings.tsi`` (User, 2026-11-24)."""
    import xml.etree.ElementTree as ET

    from core.runtime import in_traktor
    from core.traktor import find_traktor_nml

    if in_traktor():
        value = _native_setting("Browser.Dir.Music") if _native_setting else None
        return _folders(value) if value else []
    nml = find_traktor_nml()
    tsi = nml.parent / "Traktor Settings.tsi" if nml else None
    if tsi is None or not tsi.exists():
        return []
    try:
        root_el = ET.parse(tsi).getroot()
    except (ET.ParseError, OSError):
        return []
    entry = next((e for e in root_el.iter("Entry") if e.get("Name") == "Browser.Dir.Music"), None)
    if entry is None:
        return []
    return _folders(entry.get("Value") or "")
