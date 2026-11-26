"""Traktor playlist ``.nml`` files alongside the Pioneer database.

Traktor has no device database to write. What it reads from a stick is a
playlist ``.nml`` — browser, right-click, *Import Playlist* — the same shape
its own *Export Playlist* produces: a small ``<COLLECTION>`` holding each
track's ``ENTRY`` and a ``<PLAYLISTS>`` tree that lists them by
``PRIMARYKEY``.

Each ``ENTRY`` is copied from ``collection.nml`` as it stands, so cues, the
beatgrid, key and tags arrive exactly as Traktor stored them — unlike the
Pioneer side, nothing is converted and no offset is applied. Only the
``LOCATION`` changes, to point at the copy on the stick, and every
``PRIMARYKEY`` is built from that same ``LOCATION`` so the two always agree.

The audio is the one already copied for the players: no second copy.

``LOCATION`` names the volume the file is on. On macOS that is the stick's
name (``VOLUME="DJSTICK"``, with ``DIR`` from the stick's root); on Windows it
is the drive letter. A stick that keeps its name is found on another Mac;
otherwise Traktor's own relocate finds the files.

Each file sits in the folders its playlist has in Traktor
(``Traktor/Gigs/Set.nml``). Only the file moves: ``LOCATION`` counts from the
stick's root, so it is the same whatever folder the playlist is in.
"""

from __future__ import annotations

import copy
import logging
import os
import re
import sys
import uuid
import xml.etree.ElementTree as ET
from collections.abc import Callable
from pathlib import Path

from core.m3u import marked, remove_flat_leftovers, target_for
from core.playlists import PlaylistKey, key_parts
from core.usb_export import write_if_changed

log = logging.getLogger(__name__)

#: Where the playlists go on the stick.
NML_DIRNAME = "Traktor"

EXTENSION = ".nml"

#: Used when the source collection cannot say which NML version it is.
DEFAULT_VERSION = "20"

#: In every playlist written here, straight after the XML declaration. A
#: comment, so it is no part of the document a parser hands on; to us, the
#: difference between a file of ours and one exported from Traktor and
#: copied to the stick by hand. See :func:`core.m3u.remove_flat_leftovers`.
MARKER = "<!-- Written by Track Kommander -->"

_HEADER = '<?xml version="1.0" encoding="UTF-8" standalone="no" ?>\n' + MARKER + "\n"


def is_ours(path: Path) -> bool:
    """Was the ``.nml`` at *path* written by this application?"""
    return marked(path, MARKER)


# ── Where the stick is ──────────────────────────────────────────────────────


def volume_of(
    dest: Path,
    *,
    is_mount: Callable[[Path], bool] = os.path.ismount,
    windows: bool = sys.platform == "win32",
    anchor: str | None = None,
    listdir: Callable[[Path], list[str]] = os.listdir,
) -> tuple[Path, str]:
    """(volume root, VOLUME token) for a folder on the stick.

    On Windows the token is the drive letter (``E:``) and the root is the
    drive's. Elsewhere it is the name of the mount point that holds *dest*
    (``/Volumes/DJSTICK`` → ``DJSTICK``), spelled as the filesystem lists it:
    macOS opens ``/Volumes/Key`` for a stick named ``KEY``, and a remembered
    destination can carry that spelling, but Traktor matches the name as
    written.
    """
    dest = Path(dest)
    if windows:
        drive = (anchor if anchor is not None else dest.anchor).rstrip("\\/")
        return Path(drive + "/"), drive
    mount = dest
    while not is_mount(mount) and mount.parent != mount:
        mount = mount.parent
    return mount, _listed_name(mount, listdir) or "Macintosh HD"


def _listed_name(path: Path, listdir: Callable[[Path], list[str]]) -> str:
    """*path*'s last component as its parent lists it, else as given."""
    try:
        listed = listdir(path.parent)
    except OSError:
        return path.name
    if path.name in listed:
        return path.name
    folded = [n for n in listed if n.casefold() == path.name.casefold()]
    return folded[0] if len(folded) == 1 else path.name


def location_of(file_on_stick: Path, mount: Path, volume: str) -> dict[str, str]:
    """The ``LOCATION`` attributes for *file_on_stick*.

    ``DIR`` is Traktor's ``/:``-separated form, from the volume's root. A
    literal ``:`` in a name is ``//`` in Traktor's encoding; stick names are
    plain ASCII without one, but the rule is kept so a name never breaks it.
    """
    rel = Path(file_on_stick).relative_to(mount)
    folders = [p.replace(":", "//") for p in rel.parent.parts]
    directory = "/:" + "".join(f"{p}/:" for p in folders)
    return {
        "DIR": directory,
        "FILE": rel.name.replace(":", "//"),
        "VOLUME": volume,
        "VOLUMEID": volume,
    }


def _primary_key(loc: dict[str, str]) -> str:
    return loc["VOLUME"] + loc["DIR"] + loc["FILE"]


# ── The source collection ───────────────────────────────────────────────────


def entries_by_path(nml_path: Path | None) -> tuple[dict[str, ET.Element], str]:
    """(path → collection ``ENTRY``, NML version) from ``collection.nml``.

    Paths are resolved the same way the export resolves them, so they match
    each exported track's ``source``. Inside Traktor they are made from its
    memory instead (:mod:`core.native_export`): collection.nml is never read there.
    """
    from core.runtime import in_traktor

    if in_traktor():
        from core.collection_source import collection_cues, collection_metadata
        from core.native_export import entries

        return entries(collection_metadata(None), collection_cues(None)), DEFAULT_VERSION
    if nml_path is None or not Path(nml_path).is_file():
        return {}, DEFAULT_VERSION
    from core.traktor import _nml_build_path

    root = ET.parse(nml_path).getroot()
    out: dict[str, ET.Element] = {}
    for entry in root.iterfind("COLLECTION/ENTRY"):
        loc = entry.find("LOCATION")
        path = _nml_build_path(loc) if loc is not None else ""
        if path:
            out[str(Path(path))] = entry
    return out, root.get("VERSION", DEFAULT_VERSION)


# ── One playlist ────────────────────────────────────────────────────────────


def _entry_for(track, entries: dict[str, ET.Element], loc: dict[str, str]) -> ET.Element:
    """The collection's ENTRY for *track*, pointing at the stick copy."""
    source = entries.get(str(Path(track.source)))
    if source is not None:
        entry = copy.deepcopy(source)
    else:
        entry = ET.Element("ENTRY")
        if track.title:
            entry.set("TITLE", track.title)
        if track.artist:
            entry.set("ARTIST", track.artist)
    old = entry.find("LOCATION")
    location = ET.Element("LOCATION", loc)
    if old is None:
        entry.insert(0, location)
    else:
        entry[list(entry).index(old)] = location
    return entry


def render(
    tracks: list,
    indices: list[int],
    name: str,
    *,
    entries: dict[str, ET.Element],
    mount: Path,
    volume: str,
    dest: Path,
    version: str = DEFAULT_VERSION,
) -> bytes:
    """One playlist file's bytes.

    *indices* pick members of *tracks* in play order. A track that never
    reached the stick has no ``device_path`` and is left out; one listed
    twice is kept twice in the playlist but held once in the collection.
    """
    root = ET.Element("NML", VERSION=version)
    ET.SubElement(root, "HEAD", COMPANY="www.native-instruments.com", PROGRAM="Traktor")
    ET.SubElement(root, "MUSICFOLDERS")
    collection = ET.SubElement(root, "COLLECTION")

    keys: list[str] = []
    seen: set[str] = set()
    for i in indices:
        if not 0 <= i < len(tracks):
            continue
        track = tracks[i]
        if not getattr(track, "device_path", ""):
            continue
        on_stick = Path(dest) / track.device_path.lstrip("/")
        loc = location_of(on_stick, mount, volume)
        key = _primary_key(loc)
        keys.append(key)
        if key not in seen:
            seen.add(key)
            collection.append(_entry_for(track, entries, loc))
    collection.set("ENTRIES", str(len(seen)))

    ET.SubElement(root, "SETS", ENTRIES="0")
    playlists = ET.SubElement(root, "PLAYLISTS")
    folder = ET.SubElement(playlists, "NODE", TYPE="FOLDER", NAME="$ROOT")
    subnodes = ET.SubElement(folder, "SUBNODES", COUNT="1")
    node = ET.SubElement(subnodes, "NODE", TYPE="PLAYLIST", NAME=name)
    playlist = ET.SubElement(
        node, "PLAYLIST", ENTRIES=str(len(keys)), TYPE="LIST", UUID=uuid.uuid4().hex
    )
    for key in keys:
        item = ET.SubElement(playlist, "ENTRY")
        ET.SubElement(item, "PRIMARYKEY", TYPE="TRACK", KEY=key)
    ET.SubElement(root, "INDEXING")

    # Explicit end tags, as Traktor writes them: whether its parser takes
    # "<X />" is not something to find out at a gig.
    body = ET.tostring(root, encoding="unicode", short_empty_elements=False)
    return (_HEADER + body).encode("utf-8")


#: The playlist's UUID, new on every render; not a change to the playlist.
_UUID = re.compile(rb'UUID="[0-9a-fA-F-]*"')


def _without_uuid(body: bytes) -> bytes:
    return _UUID.sub(b'UUID=""', body)


# ── All of them ─────────────────────────────────────────────────────────────


def write_playlists(
    dest: Path,
    tracks: list,
    playlists: dict[PlaylistKey, list[int]],
    nml_path: Path | None,
    *,
    is_mount: Callable[[Path], bool] = os.path.ismount,
) -> list[Path]:
    """Write every playlist under ``<dest>/Traktor/``. Returns what it wrote.

    A playlist with nothing on the stick is skipped. Files for playlists this
    export does not name are left alone, as the ``.m3u8`` ones are.
    """
    if not playlists:
        return []
    dest = Path(dest)
    entries, version = entries_by_path(nml_path)
    mount, volume = volume_of(dest, is_mount=is_mount)

    out_dir = dest / NML_DIRNAME
    written: list[Path] = []
    changed = 0
    taken: dict[Path, set[str]] = {}
    for key, members in playlists.items():
        if not any(0 <= i < len(tracks) and getattr(tracks[i], "device_path", "") for i in members):
            continue
        _folders, name = key_parts(key)
        body = render(
            tracks,
            members,
            name,
            entries=entries,
            mount=mount,
            volume=volume,
            dest=dest,
            version=version,
        )
        target = target_for(out_dir, key, taken, extension=EXTENSION)
        target.parent.mkdir(parents=True, exist_ok=True)
        changed += write_if_changed(target, body, ignore=_without_uuid)
        written.append(target)

    removed = remove_flat_leftovers(out_dir, written, ours=is_ours)
    log.info(
        "wrote %d Traktor playlist(s) → %s, %d changed (volume %r, %d flat leftover(s) removed)",
        len(written),
        out_dir,
        changed,
        volume,
        removed,
    )
    return written
