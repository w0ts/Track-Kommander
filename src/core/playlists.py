"""Read Traktor's playlist tree out of ``collection.nml``.

Traktor stores playlists separately from the collection, as a tree of folders
and lists under ``<PLAYLISTS>``. Each entry references a track by its
``PRIMARYKEY``, which is the same ``/:``-separated location string used in
``<LOCATION>`` but flattened into one attribute and prefixed with the volume::

    Macintosh HD/:Users/:mots/:Music/:Tracks/:Artist - Title.mp3

Two node types matter:

* ``PLAYLIST`` — a fixed list of tracks. Exportable.
* ``SMARTLIST`` — a saved search. It has no stored entries at all: Traktor
  evaluates it live from a ``SEARCH_EXPRESSION``. These are reported with
  ``smart=True`` and the ``query`` string, which :mod:`core.smartlist` runs
  against the collection to work out what they contain.
"""

from __future__ import annotations

import logging
import os
import threading
import unicodedata
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from core.runtime import in_traktor

log = logging.getLogger(__name__)


@dataclass
class Playlist:
    """One Traktor playlist, with its tracks in the order the user arranged."""

    name: str
    path: tuple[str, ...] = ()  # enclosing folders, outermost first
    paths: list[str] = field(default_factory=list)
    smart: bool = False
    query: str = ""
    """A smart playlist's saved search, for :mod:`core.smartlist`."""

    @property
    def display(self) -> str:
        """ "Folder / Sub / Name" — the tree position, flattened."""
        return " / ".join(self.key)

    @property
    def key(self) -> tuple[str, ...]:
        """The tree position: enclosing folders, then the name. Unique where
        the name alone is not — Traktor allows one name in two folders."""
        return (*self.path, self.name)

    def __len__(self) -> int:
        return len(self.paths)


#: How an export names a playlist: its tree position, folders outermost first
#: and the name last. A bare string is a playlist at the top level.
PlaylistKey = str | tuple[str, ...]


def key_parts(key: PlaylistKey) -> tuple[tuple[str, ...], str]:
    """(enclosing folders, name) for *key*."""
    if isinstance(key, str):
        return (), key
    *folders, name = key
    return tuple(folders), name


def _primarykey_to_path(key: str) -> str:
    """``Macintosh HD/:Users/:x/:a.mp3`` → ``/Users/x/a.mp3``.

    The leading component is the volume name, which on macOS is the boot
    volume (dropped) and on Windows a drive letter (kept).

    The result is spelled as the platform spells a path — backslashes on
    Windows — because that is how :func:`core.traktor._nml_build_path`
    spells the collection's, and the two are compared as strings.
    """
    if not key:
        return ""
    parts = key.split("/:")
    volume, rest = parts[0], parts[1:]
    if not rest:
        return volume
    # Traktor encodes a literal ":" in a filename as "//", because ":" is the
    # HFS path separator. core.traktor._nml_build_path decodes it for the
    # collection; a playlist naming the same file has to agree, or the track
    # resolves to a path that does not exist and drops out of the export.
    # Only the last component is a filename, which is where the encoding is
    # applied — matching _nml_build_path, which decodes FILE and not DIR.
    rest = [*rest[:-1], rest[-1].replace("//", ":")]
    tail = "/".join(rest)
    # Windows: "C:" stays as a drive prefix. macOS: the volume name is the
    # mount point, absolute for the boot disk and /Volumes/<name> otherwise.
    if len(volume) == 2 and volume[1] == ":" and volume[0].isalpha():
        return os.path.normpath(f"{volume}/{tail}")
    return os.path.normpath(f"/{tail}")


_file_cache: dict[tuple[str, str], tuple[tuple[int, int], object]] = {}
_file_cache_lock = threading.Lock()


def cached_by_file[T](tag: str, path: Path | str, compute: Callable[[], T]) -> T:
    """``compute()``, remembered until *path* changes size or mtime.

    For results derived from ``collection.nml``: a real one runs to tens of
    megabytes and is read on the GUI thread (at startup, on every reload,
    and at every export), yet it only changes when Traktor saves. The
    derived result is cached rather than the parsed tree, which for a large
    collection costs far more memory than the answer. Callers get the same
    object back each time and must treat it as read-only.

    A file that cannot be stat'ed is not cached, so its error surfaces from
    *compute* every time.
    """
    try:
        st = os.stat(path)
    except OSError:
        return compute()
    stamp = (st.st_mtime_ns, st.st_size)
    slot = (tag, str(path))
    with _file_cache_lock:
        hit = _file_cache.get(slot)
    if hit is not None and hit[0] == stamp:
        return cast(T, hit[1])
    value = compute()
    with _file_cache_lock:
        _file_cache[slot] = (stamp, value)
    return value


_native: Callable[[], list[Playlist] | None] | None = None


def set_native_playlists(source: Callable[[], list[Playlist] | None] | None) -> None:
    """Read the playlist tree from *source* (Traktor's memory) from now on; None: the NML.

    Inside Traktor, the integrated app registers a source that dumps the tree
    Traktor holds (``workers.native_collection``): current before Traktor has
    saved, and the same lists, order, entries and smartlist queries as the NML
    (measured 2026-11-24: 60 lists, 299 entries, identical). *source* returns
    None when it has nothing to offer right now; the NML is read then.
    """
    global _native
    _native = source


def parse_playlists(nml_path: Path) -> list[Playlist]:
    """Every playlist, depth-first, in Traktor's own order.

    From Traktor's memory when a native source is registered, else from the
    NML, cached per file version (see :func:`cached_by_file`). Inside Traktor
    the NML is never read (User, 2026-11-24): no memory read, no playlists.
    Treat the result as read-only.
    """
    source = _native
    if source is not None:
        try:
            lists = source()
        except Exception:
            log.exception("native playlist source failed")
            lists = None
        if lists is not None:
            return lists
    if in_traktor():
        log.error(
            "inside Traktor, playlists unavailable from Traktor's memory (collection.nml not read)"
        )
        return []
    return cached_by_file("playlists", nml_path, lambda: _parse_playlists(nml_path))


def parse_native_playlists(text: str) -> list[Playlist]:
    """libtk's playlist dump (``tk_playlists_dump``) → what :func:`_parse_playlists` gives.

    Lines: ``L <2|3> <folders joined by \\x1f> <name>`` starts a playlist (2) or
    smartlist (3), ``Q <query>`` is a smartlist's saved search, ``E <path>`` a
    playlist entry. Paths come out NFC, as the NML spells them.
    """
    out: list[Playlist] = []
    current: Playlist | None = None
    for line in text.splitlines():
        tag, _, rest = line.partition("\t")
        if tag == "L":
            fields = rest.split("\t")
            if len(fields) != 3:
                current = None
                continue
            kind, folders, name = fields
            current = Playlist(
                name=name,
                path=tuple(f for f in folders.split("\x1f") if f),
                smart=kind == "3",
            )
            out.append(current)
        elif current is None:
            continue
        elif tag == "Q":
            current.query = rest
        elif tag == "E" and rest:
            current.paths.append(unicodedata.normalize("NFC", rest))
    return out


def _parse_playlists(nml_path: Path) -> list[Playlist]:
    try:
        root = ET.parse(str(nml_path)).getroot()
    except (OSError, ET.ParseError) as exc:
        raise RuntimeError(f"cannot read playlists: {exc}") from exc

    container = root.find("PLAYLISTS")
    if container is None:
        return []

    out: list[Playlist] = []

    def walk(node_parent: ET.Element, folders: tuple[str, ...]) -> None:
        for node in node_parent.findall("NODE"):
            kind = node.get("TYPE") or ""
            name = node.get("NAME") or ""
            if kind == "FOLDER":
                sub = node.find("SUBNODES")
                if sub is not None:
                    # "$ROOT" is Traktor's invisible top level, not a folder
                    # the user made, so it never appears in a display path.
                    nested = folders if name == "$ROOT" else (*folders, name)
                    walk(sub, nested)
            elif kind == "SMARTLIST":
                smart = node.find("SMARTLIST")
                expr = smart.find("SEARCH_EXPRESSION") if smart is not None else None
                out.append(
                    Playlist(
                        name=name,
                        path=folders,
                        smart=True,
                        query=(expr.get("QUERY") or "") if expr is not None else "",
                    )
                )
            elif kind == "PLAYLIST":
                entries = node.find("PLAYLIST")
                paths: list[str] = []
                if entries is not None:
                    for entry in entries.findall("ENTRY"):
                        pk = entry.find("PRIMARYKEY")
                        if pk is None:
                            continue
                        p = _primarykey_to_path(pk.get("KEY") or "")
                        if p:
                            paths.append(p)
                out.append(Playlist(name=name, path=folders, paths=paths))

    walk(container, ())
    log.debug("found %d playlists in %s", len(out), nml_path)
    return out
