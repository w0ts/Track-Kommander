"""Where Auto DJ starts a transition in the playing track.

At the track's last hotcue (User, 2026-11-24: "transition at the last hot cue"):
DJs put one where the outro begins, and TK's hotcue tool does too. Without one,
as before: early enough that the transition ends with the track.
"""

from __future__ import annotations

from collections.abc import Iterable

# Traktor's cue types that are not mix points: a load marker and the beat grid's anchor.
_NOT_MIX_POINTS = frozenset({3, 4})
#: A last hotcue before this share of the track is no outro (an intro cue, a drop): ignored.
MIN_OUTRO_SHARE = 0.5


def last_hotcue_s(slots: Iterable[tuple[bool, int, float]]) -> float | None:
    """The latest hotcue, in seconds, from (exists, type, start s) per slot; None if none.

    Slots are not in time order, and an empty slot keeps a stale position."""
    starts = [
        start
        for exists, kind, start in slots
        if exists and kind not in _NOT_MIX_POINTS and start > 0.0
    ]
    return max(starts) if starts else None


def mix_start_s(track_length_s: float, last_cue_s: float | None, transition_s: float) -> float:
    """Position (s) at which the transition into the next track should start."""
    at_end = max(0.0, track_length_s - transition_s)
    if last_cue_s is None or not (MIN_OUTRO_SHARE * track_length_s <= last_cue_s < track_length_s):
        return at_end
    return min(last_cue_s, at_end)
