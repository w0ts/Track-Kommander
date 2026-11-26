"""Hotcues written into Traktor's collection live (integrated mode): libtk's tk_cue_* calls.

Inside Traktor, collection.nml cannot be written (Traktor overwrites it when it quits), so the
detected hotcues go in the way Traktor's own "store hotcue" puts them in (libtk tk_cuewrite.mm),
on Traktor's main thread — which is Qt's there, so these calls are made from the UI thread. The
rules of what goes where are the NML writer's (:mod:`core.hotcue_plan`).
"""

from __future__ import annotations

import ctypes
import logging
import unicodedata
from collections.abc import Iterable
from pathlib import Path

from core.cue_points import TrackCues
from core.hotcue_plan import TK_PREFIX, plan_hotcues
from workers.native_lib import process_lib

log = logging.getLogger(__name__)


class NativeCues:
    """libtk's cue writer: by file path, as TK knows its tracks."""

    def __init__(self, lib) -> None:
        self._write = lib.tk_cue_write_path
        self._write.argtypes = [
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_double,
            ctypes.c_int,
            ctypes.c_char_p,
        ]
        self._write.restype = ctypes.c_int
        self._remove = lib.tk_cue_remove_path
        self._remove.argtypes = [ctypes.c_char_p, ctypes.c_char_p]
        self._remove.restype = ctypes.c_int

    @classmethod
    def attach(cls) -> NativeCues | None:
        """The writer inside Traktor; None anywhere else (no libtk in the process)."""
        lib = process_lib()
        return cls(lib) if lib is not None and hasattr(lib, "tk_cue_write_path") else None

    @staticmethod
    def _spellings(path: str) -> list[bytes]:
        """The path as TK spells it (NFC), then decomposed: Traktor may store either form."""
        forms = [path, unicodedata.normalize("NFD", path)]
        return [f.encode() for i, f in enumerate(forms) if f not in forms[:i]]

    def write(self, path: str, slot: int, start_ms: float, name: str, cue_type: int) -> bool:
        for spelled in self._spellings(path):
            if self._write(spelled, slot, start_ms, cue_type, name.encode()) >= 0:
                return True
        log.warning("hotcue %s not written into Traktor for %s", name, path)
        return False

    def remove(self, path: str, prefix: str = TK_PREFIX) -> int:
        """The cues of *path* named *prefix*…: how many went (0 when the track is not found)."""
        for spelled in self._spellings(path):
            removed = self._remove(spelled, prefix.encode())
            if removed >= 0:
                return removed
        return 0


def apply_detected(
    cues: NativeCues,
    detected: dict[str, list[tuple[float, str, int]]],
    tracks: Iterable[TrackCues],
    force: bool,
) -> list[tuple[str, list[tuple[int, float, str]]]]:
    """Write each track's detected hotcues by the NML writer's rules.

    Returns the tracks changed, each with the cues that went in as ``(slot, start_ms, name)``
    — what the page lists so the user can check them (User, 2026-11-24)."""
    by_path = {t.path: t for t in tracks}
    changed: list[tuple[str, list[tuple[int, float, str]]]] = []
    for path, new in detected.items():
        track = by_path.get(unicodedata.normalize("NFC", path))
        if track is None:
            log.warning("hotcues: %s is not in Traktor's collection", path)
            continue
        plan = plan_hotcues(track.cues, new, track.bpm, force)
        if plan is None:
            continue  # the user's own hotcues, not forced
        if plan.remove_ours:
            cues.remove(path)
        added = [
            (slot, start, name)
            for slot, start, name, t in plan.add
            if cues.write(path, slot, start, name, t)
        ]
        if added or plan.remove_ours:
            changed.append((path, added))
    return changed


def describe(changed: list[tuple[str, list[tuple[int, float, str]]]]) -> str:
    """The changed tracks for the user: one per line, its cues as "slot m:ss Name"."""

    def cue(slot: int, start: float, name: str) -> str:
        seconds = int(start // 1000)
        return f"{slot + 1} {seconds // 60}:{seconds % 60:02d} {name.removeprefix(TK_PREFIX)}"

    lines = []
    for path, added in changed:
        cues_text = ", ".join(cue(*c) for c in added) or "TK's earlier cues removed"
        lines.append(f"{Path(path).stem}\n    {cues_text}")
    return "\n".join(lines)


def remove_tk_cues(cues: NativeCues, tracks: Iterable[TrackCues]) -> tuple[int, int]:
    """Every hotcue TK detected, removed: (tracks changed, cues removed)."""
    changed = removed = 0
    for track in tracks:
        if not any(c.hotcue >= 1 and c.name.startswith(TK_PREFIX) for c in track.cues):
            continue
        n = cues.remove(track.path)
        if n:
            changed += 1
            removed += n
    return changed, removed
