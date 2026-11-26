"""Functions the Library tab's areas share and that need no widget.

They read the filesystem, the mounted volumes and the collection file, and
nothing else, so the export, scan and playlist code can use them without
importing one another. Split from ``ui.library`` when that file outgrew a
single screen of concerns.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

from PySide6.QtCore import QStorageInfo, Qt


def _tick(on: bool) -> Qt.CheckState:
    return Qt.CheckState.Checked if on else Qt.CheckState.Unchecked


# These run once per track on the GUI thread, so each asks the filesystem
# once: an exists()/is_file() check before stat() doubled the syscalls.


def _mtime_or_zero(path: str) -> float:
    """Modification time of *path*, or 0 when it is missing or unreadable."""
    try:
        return os.stat(path).st_mtime
    except OSError:
        return 0.0


def _total_size(paths) -> int:
    """Bytes in the regular files among *paths*; the rest count as nothing."""
    total = 0
    for path in paths:
        try:
            st = os.stat(path)
        except OSError:
            continue
        if stat.S_ISREG(st.st_mode):
            total += st.st_size
    return total


def _read_export_collection(nml: str) -> tuple[dict, list[str], int]:
    """(track by path, paths in collection order, entries skipped) from *nml*.

    Every ``ENTRY`` in ``<COLLECTION>`` as a :class:`core.rekordbox.Track`,
    for the device export. Kept apart from the export itself so the result
    can be cached per collection version.
    """
    import xml.etree.ElementTree as ET

    from core.rekordbox import parse_entry
    from core.traktor import _nml_build_path

    root = ET.parse(nml).getroot()
    collection = root.find("COLLECTION")
    entries = collection.findall("ENTRY") if collection is not None else []
    by_path: dict = {}
    order: list[str] = []
    skipped = 0
    for entry in entries:
        loc = entry.find("LOCATION")
        if loc is None:
            skipped += 1
            continue
        path = _nml_build_path(loc)
        if not path:
            skipped += 1
            continue
        by_path[path] = parse_entry(entry, path)
        order.append(path)
    return by_path, order, skipped


def _removable_volumes() -> list[tuple[str, str]]:
    """(name, root path) of every mounted volume a stick could be.

    The boot volume and anything read-only or not ready are left out; on
    macOS that leaves what is under /Volumes, on Windows every drive but the
    system one.
    """
    boot = QStorageInfo.root().rootPath()
    found = []
    for vol in QStorageInfo.mountedVolumes():
        root = vol.rootPath()
        if not vol.isValid() or not vol.isReady() or vol.isReadOnly():
            continue
        if root == boot or not root.startswith("/Volumes/") and root.startswith("/"):
            continue
        name = vol.displayName() or vol.name() or Path(root).name or root
        found.append((name, root))
    return sorted(found, key=lambda nr: nr[0].lower())
