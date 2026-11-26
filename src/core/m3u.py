"""Plain ``.m3u8`` playlists alongside the Pioneer database.

``export.pdb`` is what a CDJ reads, and nothing else understands it. A car
stereo, a media player, a controller with a USB host port, another DJ's
software — all of them read a text playlist, and most of them read nothing
else. Writing both costs a few kilobytes and makes the same stick useful
outside a Pioneer booth.

``.m3u8`` rather than ``.m3u``: the ``8`` is the promise that the file is
UTF-8, which is the only way an accented artist name survives. A plain
``.m3u`` is officially the local code page, and players guess.

Paths are written **relative** to the playlist file. A stick mounts at a
different place on every machine — ``/Volumes/DJ`` here, ``E:\\`` there — so an
absolute path is wrong everywhere but the machine that wrote it.

A playlist sits in the folders it has in Traktor (``PLAYLISTS/Gigs/Set.m3u8``),
so its paths climb one ``..`` per folder.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from core.playlists import PlaylistKey, key_parts
from core.usb_export import sanitise, write_if_changed

log = logging.getLogger(__name__)

#: Where the playlists go on the stick. A folder rather than the root so the
#: stick's top level stays as a player expects to find it.
M3U_DIRNAME = "PLAYLISTS"

EXTENSION = ".m3u8"

#: The file's first line, which is what a player recognises it by.
_FIRST_LINE = "#EXTM3U"

#: Its second, in every playlist written here. A comment to a player; to us,
#: the difference between a file of ours that can be cleaned up and one
#: somebody else put on the stick.
MARKER = "# Written by Track Kommander"

#: How much of a file is read to look for the marker: it is at the top.
_MARKER_WITHIN = 512


def marked(path: Path, marker: str) -> bool:
    """Does the file at *path* carry *marker* near its top?

    False for a file that is not there or cannot be read: not knowing is
    not grounds for deleting it.
    """
    try:
        with Path(path).open("rb") as fh:
            return marker.encode("utf-8") in fh.read(_MARKER_WITHIN)
    except OSError:
        return False


def is_ours(path: Path) -> bool:
    """Was the ``.m3u8`` at *path* written by this application?"""
    return marked(path, MARKER)


def _entry(device_path: str, playlist_dir: str) -> str:
    """One track's path, relative to a playlist sitting in *playlist_dir*.

    *device_path* is the player-facing absolute path (``/Contents/x.mp3``),
    so the relative form is just the right number of ``..`` in front of it.
    """
    rel = device_path.lstrip("/")
    depth = len([p for p in playlist_dir.split("/") if p])
    return "../" * depth + rel


def render(tracks: list, indices: list[int], *, playlist_dir: str = M3U_DIRNAME) -> str:
    """One playlist's file contents.

    *tracks* is the export's track list; *indices* picks the members, in the
    order they should play — a set's order is the point of it, so it is never
    sorted. A track that never reached the device has no ``device_path`` and
    is skipped rather than written as a broken line.
    """
    lines = [_FIRST_LINE, MARKER]
    for i in indices:
        if i < 0 or i >= len(tracks):
            continue
        t = tracks[i]
        if not getattr(t, "device_path", ""):
            continue
        artist = (t.artist or "").strip()
        title = (t.title or Path(t.source).stem).strip()
        label = f"{artist} - {title}" if artist else title
        # A newline in a tag would end the entry early and make the next line
        # look like a path.
        label = label.replace("\n", " ").replace("\r", " ")
        lines.append(f"#EXTINF:{int(t.duration_s)},{label}")
        lines.append(_entry(t.device_path, playlist_dir))
    return "\n".join(lines) + "\n"


#: What :func:`render` gives for a playlist with no track on the stick.
_NO_TRACKS = f"{_FIRST_LINE}\n{MARKER}\n"


def filename_for(name: str, taken: set[str], *, extension: str = EXTENSION) -> str:
    """A safe, unique filename for a playlist called *name*.

    *taken* is what the directory already holds from this export. Two names
    can still meet in one folder once they are made safe (``a:b`` and
    ``a_b``), so the second gets a suffix rather than quietly overwriting the
    first.
    """
    stem = sanitise(name) or "playlist"
    candidate = stem + extension
    n = 2
    while candidate.lower() in taken:
        candidate = f"{stem} ({n}){extension}"
        n += 1
    taken.add(candidate.lower())
    return candidate


def folder_for(out_dir: Path, key: PlaylistKey) -> Path:
    """The directory under *out_dir* for the playlist at *key*: its Traktor
    folders, each made safe for the stick."""
    folders, _name = key_parts(key)
    return out_dir.joinpath(*(sanitise(f) or "folder" for f in folders))


def target_for(
    out_dir: Path, key: PlaylistKey, taken: dict[Path, set[str]], *, extension: str = EXTENSION
) -> Path:
    """Where the playlist at *key* is written. *taken* is per directory."""
    folder = folder_for(out_dir, key)
    _folders, name = key_parts(key)
    return folder / filename_for(name, taken.setdefault(folder, set()), extension=extension)


def remove_flat_leftovers(
    out_dir: Path, written: list[Path], *, ours: Callable[[Path], bool] = is_ours
) -> int:
    """Delete the top-level copy of a playlist that is now in a folder.

    A playlist moved into a folder in Traktor is written to that folder, and
    the file from before would sit at the top level as a stale second copy.
    One this export wrote at the top level itself is kept.

    Only a file that says it was written here (*ours*) is removed. The name
    alone is not enough: a playlist somebody else put on the stick can have
    the same one. That leaves copies from before the marker existed where
    they are — they say nothing, so they are not ours to delete.
    """
    top = {p.name.lower() for p in written if p.parent == out_dir}
    removed = 0
    for path in written:
        if path.parent == out_dir or path.name.lower() in top:
            continue
        flat = out_dir / path.name
        if flat.is_file() and ours(flat):
            flat.unlink()
            removed += 1
    return removed


def write_playlists(
    dest: Path, tracks: list, playlists: dict[PlaylistKey, list[int]]
) -> list[Path]:
    """Write every playlist under ``<dest>/PLAYLISTS/``. Returns what it wrote.

    An empty playlist — or one whose every track failed to copy — is skipped:
    a file with nothing but its header in it is noise on the stick.
    """
    if not playlists:
        return []

    dest = Path(dest)
    out_dir = dest / M3U_DIRNAME
    out_dir.mkdir(parents=True, exist_ok=True)

    written: list[Path] = []
    changed = 0
    taken: dict[Path, set[str]] = {}
    for key, members in playlists.items():
        folder = folder_for(out_dir, key)
        body = render(tracks, members, playlist_dir=folder.relative_to(dest).as_posix())
        if body == _NO_TRACKS:
            continue
        target = target_for(out_dir, key, taken)
        target.parent.mkdir(parents=True, exist_ok=True)
        # Encoded as is, so a Windows build does not write CRLF, which some
        # hardware players show as a stray character at the end of a path.
        changed += write_if_changed(target, body.encode("utf-8"))
        written.append(target)

    removed = remove_flat_leftovers(out_dir, written)
    log.info(
        "wrote %d m3u8 playlist(s) → %s, %d changed (%d flat leftover(s) removed)",
        len(written),
        out_dir,
        changed,
        removed,
    )
    return written
