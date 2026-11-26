"""Write the user's Traktor collection — the one way every tool does it.

``collection.nml`` is the user's whole library: tracks, cues, grids,
playlists. Two things must hold whenever it is written.

**It is replaced, never written over.** The new content goes to a sibling
and is moved into place (:mod:`core.atomic_write`), so a crash or a full disk
half-way leaves the collection as it was.

**The copy taken first is never the only one.** Each write keeps the
collection as it found it, under a name with the time in it, and the newest
:data:`KEEP_BACKUPS` are kept. A single ``collection.nml.bak`` was overwritten
by whichever tool ran next — hotcues, then date playlists, and the original
was gone; run a tool twice after a bad first run and the backup was the bad
file.
"""

from __future__ import annotations

import logging
import shutil
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from core.atomic_write import replacing

log = logging.getLogger(__name__)

#: How many dated backups stay beside the collection.
KEEP_BACKUPS = 5

_BACKUP_SUFFIX = ".bak"


def _stamp() -> str:
    """Now, in a form that sorts by name and is legal in a file name."""
    return time.strftime("%Y-%m-%d_%H-%M-%S")


def backups_of(nml_path: Path) -> list[Path]:
    """The dated backups of *nml_path*, oldest first.

    Only the ones written here. A plain ``collection.nml.bak`` from an
    earlier version is the user's to keep or delete.
    """
    nml_path = Path(nml_path)
    return sorted(nml_path.parent.glob(f"{nml_path.name}.*{_BACKUP_SUFFIX}"), key=_order)


def _order(backup: Path) -> tuple[str, int]:
    """Sort key: the time, then the counter two writes in one second get."""
    stamp = backup.name.removesuffix(_BACKUP_SUFFIX).rsplit(".", 1)[-1]
    time_part, _, counter = stamp.partition("~")
    return time_part, int(counter) if counter.isdigit() else 1


def _new_backup_path(nml_path: Path) -> Path:
    stamp = _stamp()
    path = nml_path.with_name(f"{nml_path.name}.{stamp}{_BACKUP_SUFFIX}")
    counter = 1
    while path.exists():
        counter += 1
        path = nml_path.with_name(f"{nml_path.name}.{stamp}~{counter}{_BACKUP_SUFFIX}")
    return path


def _drop_old_backups(nml_path: Path) -> None:
    for old in backups_of(nml_path)[:-KEEP_BACKUPS]:
        try:
            old.unlink()
        except OSError:
            # One backup too many is not worth failing a write that worked.
            log.warning("could not remove old backup %s", old, exc_info=True)


def write_collection(tree: ET.ElementTree[ET.Element], nml_path: Path) -> Path:
    """Write *tree* as the collection at *nml_path*; return the backup made.

    The backup is taken first. If it cannot be, nothing is written: without
    a copy of the original there is no way back. Raises :class:`OSError` in
    either case, with the collection as it was.
    """
    from core import profile

    if not profile.collection_writes_allowed():
        raise PermissionError("the beta never writes to Traktor's collection")
    nml_path = Path(nml_path)
    backup = _new_backup_path(nml_path)
    shutil.copy2(nml_path, backup)
    with (
        replacing(nml_path, keep_mode=True) as part,
        open(part, "w", encoding="utf-8", newline="\n", errors="xmlcharrefreplace") as out,
    ):
        # Opened here rather than by name, for the line ends: Traktor ends
        # its lines with a line feed on Windows too, and a file opened the
        # platform's way gets CR LF there. Otherwise as ElementTree would
        # open it itself. It reformats whitespace but produces valid XML.
        tree.write(out, encoding="unicode", xml_declaration=True)
    _drop_old_backups(nml_path)
    log.info("wrote %s (was kept as %s)", nml_path, backup.name)
    return backup
