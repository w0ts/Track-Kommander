"""Fixing a wrong beat grid from what the phase meter says.

A track whose grid is off sounds wrong when the decks' grids agree. The DJ
nudges the jog until it sounds right, which leaves the two grids standing
apart — and Traktor reports that gap, per deck, as
``app.traktor.decks.N.tempo.phase``: a fraction of a beat in −0.5 … +0.5.

With the tempos locked (sync on, or matched by ear) that number holds still;
with the tempos apart it runs and wraps several times a minute, and means
nothing. So only a settled reading is worth capturing — see
:class:`PhaseTracker`.

The captured offset is queued against the track's file path and written to
its ``AutoGrid`` marker in ``collection.nml`` later, because Traktor
overwrites that file from memory when it quits.
"""

from __future__ import annotations

import json
import logging
import os
import time
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from pathlib import Path

from core.collection_write import write_collection

log = logging.getLogger(__name__)

#: How long a phase reading must hold, how many samples that needs, and how
#: far it may wander meanwhile (in beats). The bridge ticks about once a
#: second, so this is a three-second hold.
SETTLE_SECONDS = 3.0
SETTLE_SAMPLES = 3
SETTLE_TOLERANCE = 0.01

#: A reading older than this says nothing about now — the bridge went quiet.
STALE_SECONDS = 5.0

#: Traktor's grid marker: a CUE_V2 of this type, with its START in ms.
GRID_CUE_TYPE = "4"


# There is deliberately no cap on the size of a reading. A phase near ±0.5
# looks ambiguous — half a beat early, or half a beat late? — but the two
# readings differ by exactly one beat, and a grid shifted by a whole beat is
# the same grid: START + 0.47b + n·b and START − 0.53b + n·b are the same set
# of beats. ±0.5 is simply the common "the grid landed on the offbeat" error.


def grid_queue_path() -> Path:
    """Where captured fixes live between sessions."""
    from core.runtime import user_data_dir

    return user_data_dir() / "grid-fixes.json"


def phase_to_ms(phase: float, bpm: float) -> float | None:
    """A phase offset in beats, as milliseconds at *bpm*. None if bpm ≤ 0."""
    if not bpm or bpm <= 0:
        return None
    return phase * 60_000.0 / bpm


@dataclass(frozen=True)
class GridFix:
    """One captured grid error, waiting to be written to the collection."""

    path: str
    deck: str
    phase: float
    bpm: float
    title: str = ""
    artist: str = ""
    captured_at: str = ""

    @property
    def offset_ms(self) -> float:
        return phase_to_ms(self.phase, self.bpm) or 0.0


class PhaseTracker:
    """The recent phase readings per deck, and whether one has settled."""

    def __init__(self) -> None:
        self._by_deck: dict[str, list[tuple[float, float]]] = {}

    def add(self, deck: str, phase: float, *, at: float | None = None) -> None:
        now = time.monotonic() if at is None else at
        samples = self._by_deck.setdefault(deck, [])
        samples.append((now, phase))
        cutoff = now - STALE_SECONDS * 2
        self._by_deck[deck] = [s for s in samples if s[0] >= cutoff]

    def latest(self, deck: str) -> float | None:
        samples = self._by_deck.get(deck)
        return samples[-1][1] if samples else None

    def settled(self, deck: str, *, now: float | None = None) -> bool:
        """True when this deck's phase has held still long enough to mean
        something: the tempos are locked and the offset is the grid error.

        A drifting phase — the tempos are apart — is not settled, and neither
        is a wrap, which drift produces at the ends of the range.
        """
        samples = self._by_deck.get(deck) or []
        now = samples[-1][0] if now is None and samples else (now or 0.0)
        if len(samples) < SETTLE_SAMPLES:
            return False
        if now - samples[-1][0] > STALE_SECONDS:
            return False
        window = [(t, p) for t, p in samples if now - t <= SETTLE_SECONDS]
        if len(window) < SETTLE_SAMPLES:
            return False
        if window[-1][0] - window[0][0] < SETTLE_SECONDS - 1.5:
            return False
        values = [p for _t, p in window]
        return max(values) - min(values) <= SETTLE_TOLERANCE


class GridQueue:
    """Captured fixes, kept as JSON so they survive a restart.

    One entry per track: capturing the same track again is a correction, not
    a second entry.
    """

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)

    def entries(self) -> list[GridFix]:
        try:
            raw = json.loads(self._path.read_text())
        except (OSError, ValueError):
            return []
        if not isinstance(raw, list):
            return []
        out = []
        for item in raw:
            try:
                out.append(GridFix(**item))
            except TypeError:
                log.debug("grid queue: dropping unreadable entry %r", item)
        return out

    def add(self, fix: GridFix) -> None:
        kept = [f for f in self.entries() if f.path != fix.path]
        self._write([*kept, fix])

    def remove(self, path: str) -> None:
        self._write([f for f in self.entries() if f.path != path])

    def clear(self) -> None:
        self._write([])

    def _write(self, fixes: list[GridFix]) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps([asdict(f) for f in fixes], indent=1))
        except OSError:
            log.warning("could not write %s", self._path, exc_info=True)


def _spelled_one_way(path: str) -> str:
    """*path* in the spelling both sides of the lookup are compared in.

    The collection's paths are spelled the platform's way; a fix carries
    the path as Traktor reported it to the bridge. On Windows those can
    differ in the separator and in the case of a letter and still be the
    same file. Elsewhere this changes nothing about a path already normal.
    """
    return os.path.normcase(os.path.normpath(path))


def apply_grid_fixes(nml_path: Path, fixes: list[GridFix]) -> tuple[int, list[str]]:
    """Shift each track's grid marker by its captured offset.

    Returns ``(written, skipped)`` where *skipped* explains each fix that
    could not be applied. Makes a ``.bak`` backup before writing, and writes
    nothing at all when there is nothing to do.
    """
    from core.traktor import _nml_build_path

    if not fixes:
        return 0, []
    try:
        tree = ET.parse(str(nml_path))
    except ET.ParseError as exc:
        raise RuntimeError(f"Cannot parse NML: {exc}") from exc

    by_path: dict[str, ET.Element] = {}
    for entry in tree.getroot().findall(".//ENTRY"):
        loc = entry.find("LOCATION")
        if loc is None:
            continue
        full = _nml_build_path(loc)
        if full:
            by_path[_spelled_one_way(full)] = entry

    written, skipped = 0, []
    for fix in fixes:
        entry = by_path.get(_spelled_one_way(fix.path))
        if entry is None:
            skipped.append(f"{Path(fix.path).name}: not in the collection")
            continue
        grid = next((c for c in entry.findall("CUE_V2") if c.get("TYPE") == GRID_CUE_TYPE), None)
        if grid is None:
            skipped.append(f"{Path(fix.path).name}: no beat grid — analyse it first")
            continue
        offset = phase_to_ms(fix.phase, fix.bpm)
        if offset is None:
            skipped.append(f"{Path(fix.path).name}: no BPM")
            continue
        try:
            start = float(grid.get("START", "0"))
        except ValueError:
            skipped.append(f"{Path(fix.path).name}: unreadable grid marker")
            continue
        moved = start + offset
        # The marker is the first beat; before the start of the file it is not
        # one. A whole beat later is the same grid.
        beat = 60_000.0 / fix.bpm
        while moved < 0:
            moved += beat
        grid.set("START", f"{moved:.6f}")
        log.info(
            "grid fix: %s %+.1f ms (%.4f beat at %.2f BPM) — %0.3f → %0.3f",
            Path(fix.path).name,
            offset,
            fix.phase,
            fix.bpm,
            start,
            moved,
        )
        written += 1

    if written == 0:
        return 0, skipped

    write_collection(tree, nml_path)
    return written, skipped
