"""What a track's bassline does.

Techno lives in the low end, and a lot of what a DJ hears as character is
there: one note hammered for eight minutes is a cold stab track; a line that
leaps an octave is a big bass track; a rolling sixteenth pattern is a driving
one. None of that shows up in loudness or in a harmonic/percussive ratio.

Notes are read one per sixteenth off the beat grid rather than per frame:
a CQT's loudest bin wobbles inside a single held note, and counting that
wobble gives thirty "notes" to the bar where there are two.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

#: A pitch class must carry this share of the low end to count as played.
_PRESENT = 0.05

#: The third must be this much of the root's weight to call major or minor;
#: below it the bassline simply does not say.
_THIRD_MIN = 0.15

#: A sixteenth quieter than this fraction of the track's median is a rest.
_REST_BELOW = 0.45


@dataclass(frozen=True)
class BassLine:
    """The shape of a bassline. Semitones throughout."""

    notes_per_bar: float
    """How often the note changes — rolling vs sustained."""
    distinct: int
    """How many pitch classes carry real weight."""
    semitone_range: float
    """How far the line travels, 5th to 95th percentile."""
    mean_jump: float
    """The average interval when it does move — steps or leaps."""
    rest_fraction: float
    """Share of sixteenths with no bass at all."""
    is_major: bool | None
    """Whether the third above the root is major. None when there is none."""


def notes_from_cqt(
    cqt: np.ndarray, *, frames_per_slot: float, rest_below: float = _REST_BELOW
) -> list[int | None]:
    """One note per sixteenth: the loudest bin of each slot, or None.

    *cqt* is magnitude, bins × frames; *frames_per_slot* is how many frames
    a sixteenth spans at this track's tempo.
    """
    if cqt.size == 0 or frames_per_slot <= 0:
        return []
    energy = cqt.sum(axis=0)
    floor = float(np.median(energy)) * rest_below
    n_slots = max(1, int(cqt.shape[1] / frames_per_slot))
    out: list[int | None] = []
    for i in range(n_slots):
        a = int(i * frames_per_slot)
        b = max(a + 1, int((i + 1) * frames_per_slot))
        col = cqt[:, a:b].mean(axis=1)
        out.append(int(col.argmax()) if col.sum() > floor else None)
    return out


def analyse_bassline(notes: list[int | None], *, pitch_weight: np.ndarray) -> BassLine:
    """Measure a sixteenth-quantised bassline.

    *pitch_weight* is the total low-end energy per pitch class (12 values),
    used only to decide major or minor.
    """
    played = [n for n in notes if n is not None]
    rest = 1.0 - (len(played) / len(notes)) if notes else 1.0
    if len(played) < 4:
        return BassLine(0.0, 0, 0.0, 0.0, rest, None)

    changes = sum(1 for a, b in zip(notes, notes[1:], strict=False) if a != b and b is not None)
    bars = max(len(notes) / 16.0, 1e-9)
    arr = np.asarray(played, dtype=float)
    semis = arr % 12 + (arr // 12) * 12  # already semitone bins
    jumps = np.abs(np.diff(arr))
    jumps = jumps[jumps > 0]

    counts = np.bincount(np.asarray(played))
    share = counts / counts.sum()
    distinct = int((share > _PRESENT).sum())

    return BassLine(
        notes_per_bar=changes / bars,
        distinct=distinct,
        semitone_range=float(np.percentile(semis, 95) - np.percentile(semis, 5)),
        mean_jump=float(jumps.mean()) if len(jumps) else 0.0,
        rest_fraction=rest,
        is_major=_third(pitch_weight),
    )


def _third(pitch_weight: np.ndarray) -> bool | None:
    """Major, minor, or no third worth the name."""
    w = np.asarray(pitch_weight, dtype=float)
    if w.size != 12 or w.sum() <= 0:
        return None
    root = int(w.argmax())
    minor, major = w[(root + 3) % 12], w[(root + 4) % 12]
    if max(minor, major) < w[root] * _THIRD_MIN:
        return None
    return bool(major > minor)
