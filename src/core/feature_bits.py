"""Which features a track has been analysed for, as one integer per track.

This replaces a single global ``analysis_version``. That number could only
say "something changed", so changing one feature's definition re-ran every
feature on every track — minutes of CLAP and HPSS to repair one column.

A bit per feature says exactly what a track is missing. The bit is set when
the feature is computed *at its current version*, so bumping that version is
one statement — clear the bit everywhere — and exactly those tracks go stale,
for exactly that feature.

Asking the database for *stale* tracks rather than current ones matters more
than it sounds: normally nothing is stale, and returning nothing is far
cheaper than returning every path in the library (11 ms against 48 at 200k
tracks).
"""

from __future__ import annotations

#: Bit index per feature key, assigned once and never reordered — these are
#: written into every user's database, so remapping them would silently mark
#: tracks as analysed for the wrong things. Append only; a retired feature
#: keeps its bit.
FEATURE_BITS: dict[str, int] = {
    # signal
    "brightness": 0,
    "hfc": 1,
    "low_energy": 2,
    "mid_energy": 3,
    "bassiness": 4,
    "low_dynamics": 5,
    "mid_dynamics": 6,
    "high_dynamics": 7,
    "danceability": 8,
    "dynamics": 9,
    "loudness": 10,
    "fingerprint": 11,
    # melodic
    "melodic": 12,
    # ml (CLAP)
    "party": 13,
    "valence": 14,
    "style": 15,
    "genre": 16,
    "happy": 17,
    "relaxed": 18,
    "aggressive": 19,
    "arousal": 20,
    "vocal": 21,
    "electronic": 22,
    "similarity": 23,
    # core
    "bpm": 24,
    "key": 25,
    "rating": 26,
    # embeddings
    "mert": 27,
    "discogs": 28,
    "openl3": 29,
    "panns": 30,
    # shape — structure and bassline
    "loop": 31,
    "sameness": 32,
    "evolution": 33,
    "sections": 34,
    "bass_density": 35,
    "bass_range": 36,
    "bounce": 37,
    "onset_density": 38,
    # 39 was "key_shift", folded back into "key" as a mode. Left free rather
    # than reused: these bits live in every user's database.
}

#: SQLite stores integers as 64-bit signed, so bit 63 is the sign bit and a
#: mask must stay below it. Past that, a second column.
MAX_BIT = 62


def bit_of(key: str) -> int | None:
    """The bit a feature owns, or None if it has none."""
    return FEATURE_BITS.get(key)


def mask_for(keys: list[str] | tuple[str, ...]) -> int:
    """A mask with one bit set per named feature. Unknown names are ignored:
    a database written by a newer build may name a feature this one has
    dropped, and refusing to scan over that would be worse."""
    mask = 0
    for key in keys:
        bit = FEATURE_BITS.get(key)
        if bit is not None:
            mask |= 1 << bit
    return mask


def clear_bit(mask: int | None, key: str) -> int:
    """*mask* with one feature's bit cleared — what a version bump does."""
    bit = FEATURE_BITS.get(key)
    if bit is None:
        return mask or 0
    return (mask or 0) & ~(1 << bit)


def missing_features(have: int | None, want: int) -> list[str]:
    """Which of the wanted features this track has not been analysed for.

    Features it has beyond *want* are not missing — a track analysed for
    something since disabled is still up to date.
    """
    have = have or 0
    gap = want & ~have
    if not gap:
        return []
    return [key for key, bit in FEATURE_BITS.items() if gap & (1 << bit)]


def stale_clause(want: int) -> tuple[str, list[int]]:
    """SQL and parameters selecting the tracks that still need work."""
    return "computed & ? != ?", [want, want]


def enabled_mask(keys: list[str] | tuple[str, ...]) -> int:
    """The mask of features the user has switched on for computing.

    Inside Traktor there is no Compute group (User, 2026-11-24): every feature is computed,
    the default, whatever was switched off in the standalone app."""
    from PySide6.QtCore import QSettings

    from core.features import axis_compute_key
    from core.runtime import in_traktor

    if in_traktor():
        return mask_for(list(keys))
    settings = QSettings()
    return mask_for([key for key in keys if settings.value(axis_compute_key(key), True, type=bool)])
