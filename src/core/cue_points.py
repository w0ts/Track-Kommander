"""Traktor's cue points per track, wherever they were read from, and what they decide.

A track's cues come from ``collection.nml`` (``CUE_V2`` elements) or, inside
Traktor, from its memory (libtk's ``tk_cues_dump``; see
:mod:`core.collection_source`). Both become :class:`TrackCues`, so the rules
below — which tracks auto hotcue detection may work on — read the same either
way.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass

#: Traktor's cue types (CUE_V2 TYPE): the beat-grid marker is 4.
GRID_MARKER = 4


@dataclass(frozen=True)
class Cue:
    type: int
    hotcue: int  # slot 0-7, -1 for a plain memory cue
    start: float  # ms
    name: str = ""  # TK's own detected cues start with "tk: " (core.hotcue_plan)
    length: float = 0.0  # ms: a loop's length (the NML's LEN); 0 for a plain cue


@dataclass(frozen=True)
class TrackCues:
    path: str
    bpm: float  # 0 when Traktor has no BPM it trusts (no TEMPO, or BPM_QUALITY 0)
    cues: tuple[Cue, ...]


def eligibility(
    tracks: list[TrackCues], include_existing: bool = False
) -> tuple[list[tuple[str, float, float]], dict[str, int]]:
    """``(candidates, skipped)`` for auto hotcue detection — see
    :func:`core.traktor.hotcue_eligibility` for the rules and the shapes."""
    result: list[tuple[str, float, float]] = []
    skipped: dict[str, int] = {}

    def _skip(reason: str) -> None:
        skipped[reason] = skipped.get(reason, 0) + 1

    for track in tracks:
        if track.bpm <= 0:
            _skip("no BPM")
            continue
        hotcue_slots = {c.hotcue for c in track.cues}
        # A beat grid must exist. Analysis writes the TYPE=4 grid marker; the
        # slot-0 autocue only appears once the track is loaded in a deck, so
        # requiring it skipped whole batch-analysed libraries.
        has_grid = any(c.type == GRID_MARKER for c in track.cues)
        if not has_grid and 0 not in hotcue_slots:
            _skip("no beat grid")
            continue
        # When not forcing, skip any track that already has hotcues in slots
        # 1–7 — including ones we generated. Treating our own cues as
        # replaceable meant every run re-analysed almost the whole library
        # (measured: 890 of 891 "candidates" already had our cues), which is
        # slow, audio-decoding work for no new result. Re-running them is what
        # ``include_existing`` is for.
        if not include_existing and any(slot >= 1 for slot in hotcue_slots):
            _skip("already has hotcues")
            continue
        # Beat phase: START of the TYPE=4 grid marker, else 0
        beat_phase_ms = next((c.start for c in track.cues if c.type == GRID_MARKER), 0.0)
        result.append((track.path, track.bpm, beat_phase_ms))
    return result, skipped


def _num(text: str, default: float) -> float:
    try:
        value = float(text)
    except ValueError:
        return default
    return value if value == value else default  # NaN


def parse_native_cues(text: str) -> list[TrackCues]:
    """libtk's cue dump → :class:`TrackCues`, paths NFC as the NML spells them.

    Lines: ``T <path> <bpm> <bpm quality>`` starts a track, then one
    ``C <type> <hotcue> <start> <length> <repeats> <name>`` per cue.
    """
    out: list[TrackCues] = []
    path, bpm, cues = "", 0.0, []

    def flush() -> None:
        if path:
            out.append(TrackCues(path, bpm, tuple(cues)))

    for line in text.splitlines():
        f = line.split("\t")
        if f[0] == "T" and len(f) == 4:
            flush()
            path = unicodedata.normalize("NFC", f[1])
            value, quality = _num(f[2], 0.0), _num(f[3], 0.0)
            bpm = value if value > 0 and quality > 0 else 0.0
            cues = []
        elif f[0] == "C" and len(f) >= 4 and path:
            name = f[6] if len(f) >= 7 else ""
            length = _num(f[4], 0.0) if len(f) >= 5 else 0.0
            cues.append(
                Cue(int(_num(f[1], -1)), int(_num(f[2], -1)), _num(f[3], 0.0), name, length)
            )
    flush()
    return out
