"""Which detected hotcues go where on one track — the rules of the collection.nml writer.

:func:`core.traktor.write_hotcues_to_nml` applies these rules to ``CUE_V2`` elements. Inside
Traktor the cues are written live instead (libtk, :mod:`workers.native_cues`), so the same
decision is made here on :class:`core.cue_points.Cue` values:

* a track with a hotcue of the user's own (slot 1-7, name not starting with ``tk: ``) is left
  alone unless forced;
* TK's earlier cues (``tk: …`` in slots 1-7) are replaced;
* new cues go into the free slots 1-7, in order;
* a new cue within one beat of a cue that stays (grid markers aside) is dropped.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from core.cue_points import GRID_MARKER, Cue

#: The name every hotcue TK detects starts with (core.hotcues), as the NML writer checks it.
TK_PREFIX = "tk: "
#: Hotcue slots TK may use: slot 0 is Traktor's autocue.
SLOTS = range(1, 8)


@dataclass(frozen=True)
class HotcuePlan:
    remove_ours: bool  # TK's earlier cues on the track go first
    add: tuple[tuple[int, float, str, int], ...]  # (slot, start ms, name, cue type)


def plan_hotcues(
    existing: Sequence[Cue], detected: Sequence[tuple[float, str, int]], bpm: float, force: bool
) -> HotcuePlan | None:
    """The changes for one track, or None when it is the user's and not forced.

    *detected* is ``[(start_ms, name, cue_type), …]`` as the detection returns it.
    """
    if not force and any(c.hotcue >= 1 and not c.name.startswith(TK_PREFIX) for c in existing):
        return None
    kept = [c for c in existing if not (c.hotcue >= 1 and c.name.startswith(TK_PREFIX))]
    occupied = {c.hotcue for c in kept}
    free = [slot for slot in SLOTS if slot not in occupied]
    beat_ms = 60_000.0 / bpm if bpm > 0 else 0.0
    starts = [c.start for c in kept if c.type != GRID_MARKER]
    fits = [
        (start, name, cue_type)
        for start, name, cue_type in detected
        if beat_ms <= 0 or all(abs(start - s) > beat_ms for s in starts)
    ]
    # As many as there are free slots: the shorter of the two decides (the NML writer's slice).
    add = tuple((slot, start, name, t) for slot, (start, name, t) in zip(free, fits, strict=False))
    return HotcuePlan(remove_ours=len(kept) != len(existing), add=add)
