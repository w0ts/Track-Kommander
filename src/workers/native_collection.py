"""Traktor's collection, playlists and cue points read from its memory (integrated mode).

Inside Traktor, libtk exports dumps of what Traktor holds in memory:
``tk_collection_dump(file)`` (every track, its path and tags),
``tk_playlists_dump(file)`` (the playlist tree with its entries and smartlist
queries) and ``tk_cues_dump(file)`` (every track's BPM and cue points). Each
writes TSV in a few milliseconds; :class:`NativeSnapshot` keeps the latest one
parsed, for :mod:`core.collection_source` and :mod:`core.playlists`.

A dump is a call into Traktor's C++, so it only runs on Traktor's main thread
(where TK's Qt code runs in integrated mode). Main-thread readers get a fresh
dump when the snapshot is older than :data:`MAX_AGE_S`; other threads (a scan's
workers) get the snapshot as it stands, or None before the first dump — the
NML is read then.
"""

from __future__ import annotations

import ctypes
import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path

from core.collection_source import Snapshot, parse_native_dump
from core.cue_points import TrackCues, parse_native_cues
from core.playlists import Playlist, parse_native_playlists
from workers.native_lib import process_symbol

log = logging.getLogger(__name__)

MAX_AGE_S = 2.0


def _process_dump(symbol: str) -> Callable[[str], int] | None:
    """libtk's dump function *symbol*, if this process is a patched Traktor."""
    fn = process_symbol(symbol)
    if fn is None:
        return None
    fn.argtypes = [ctypes.c_char_p]
    fn.restype = ctypes.c_long
    return lambda path: int(fn(path.encode()))


class NativeSnapshot[T]:
    """The latest parsed dump; callable as a native source (None = read the NML)."""

    def __init__(
        self,
        dump: Callable[[str], int],
        file: Path,
        parse: Callable[[str], T],
        *,
        what: str = "collection",
        clock: Callable[[], float] = time.monotonic,
        on_main: Callable[[], bool] = lambda: threading.current_thread() is threading.main_thread(),
    ) -> None:
        self._dump = dump
        self._file = file
        self._parse = parse
        self._what = what
        self._clock = clock
        self._on_main = on_main
        self._lock = threading.Lock()
        self._snapshot: T | None = None
        self._taken = float("-inf")

    def refresh(self) -> T | None:
        """Dump now (main thread only). None if Traktor would not."""
        started = self._clock()
        if self._dump(str(self._file)) < 0:
            log.warning("Traktor refused the %s dump", self._what)
            return None
        try:
            snap = self._parse(self._file.read_text(encoding="utf-8", errors="replace"))
        except OSError as exc:
            log.warning("cannot read the %s dump %s: %s", self._what, self._file, exc)
            return None
        with self._lock:
            self._snapshot, self._taken = snap, self._clock()
        log.info(
            "%s from Traktor's memory in %.0f ms", self._what, (self._clock() - started) * 1000
        )
        return snap

    def __call__(self) -> T | None:
        with self._lock:
            snap, age = self._snapshot, self._clock() - self._taken
        if self._on_main() and (snap is None or age > MAX_AGE_S):
            return self.refresh() or snap
        return snap


def attach_collection(scratch_dir: Path) -> NativeSnapshot[Snapshot] | None:
    """The in-memory collection source, or None outside a patched Traktor."""
    dump = _process_dump("tk_collection_dump")
    if dump is None:
        return None
    scratch_dir.mkdir(parents=True, exist_ok=True)
    return NativeSnapshot(dump, scratch_dir / "collection-dump.tsv", parse_native_dump)


def attach_playlists(scratch_dir: Path) -> NativeSnapshot[list[Playlist]] | None:
    """The in-memory playlist tree source, or None outside a patched Traktor."""
    dump = _process_dump("tk_playlists_dump")
    if dump is None:
        return None
    scratch_dir.mkdir(parents=True, exist_ok=True)
    return NativeSnapshot(
        dump, scratch_dir / "playlists-dump.tsv", parse_native_playlists, what="playlists"
    )


def attach_cues(scratch_dir: Path) -> NativeSnapshot[list[TrackCues]] | None:
    """Every track's cue points from memory, or None outside a patched Traktor."""
    dump = _process_dump("tk_cues_dump")
    if dump is None:
        return None
    scratch_dir.mkdir(parents=True, exist_ok=True)
    return NativeSnapshot(dump, scratch_dir / "cues-dump.tsv", parse_native_cues, what="cue points")
