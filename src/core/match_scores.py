"""What a match is worth: the scoring rules, one candidate at a time.

Pure functions of two tracks' values — tempo, key, a feature against a
target, a rating. No database and no arrays: each says what a score *means*
for one candidate, in a form that can be read and tested on its own.

:mod:`core.ranking` applies the same rules to a whole library at once. Where
a rule has few possible inputs (a key is one of two dozen codes) it calls
these directly, once per distinct input; where the inputs are continuous it
repeats the arithmetic on arrays, and ``tests/unit/test_matcher_parity.py``
holds the two to the same answers.
"""

from __future__ import annotations

import functools
import math
import sqlite3

from core.constants import BPM_PLATEAU, BPM_ZERO
from core.embeddings import cosine
from core.features import BY_KEY, ActiveAxis, FeatureSpec

# Axis-scoring tunables.
VIBE_SIGMA = 0.40  # gaussian width for scalar value-vs-target closeness
RATING_BONUS = 0.05  # gentle star-rating influence blended into the composite

# Back-compat alias: tests and callers reference matcher._cosine.
_cosine = cosine


def _score_bpm(a: float, b: float) -> float:
    """Flat 100 within ±BPM_PLATEAU %, then linear decay to 0 at BPM_ZERO %."""
    if not a or not b:
        return 0.0
    pct = abs(b - a) / a * 100.0
    if pct <= BPM_PLATEAU:
        return 100.0
    return max(0.0, (1.0 - (pct - BPM_PLATEAU) / (BPM_ZERO - BPM_PLATEAU)) * 100.0)


def _best_bpm_match(src_bpm: float, match_bpm: float) -> tuple[float, float, int]:
    """Return (best_score, effective_bpm, factor).

    factor:  1 = normal,  2 = match played at double speed (×2),
            -2 = match played at half speed (/2).
    Picks whichever factor gives the closest BPM to the source.
    """
    if not src_bpm or not match_bpm:
        return 0.0, match_bpm, 1
    options: list[tuple[float, float, int]] = [
        (_score_bpm(src_bpm, match_bpm), match_bpm, 1),
        (_score_bpm(src_bpm, match_bpm * 2), match_bpm * 2, 2),
        (_score_bpm(src_bpm, match_bpm / 2), match_bpm / 2, -2),
    ]
    return max(options, key=lambda x: x[0])


# Camelot ranking curve, indexed by distance around the wheel (0–6).
#
# The first two entries of each row are the classic harmonic-mixing scores
# (same key 100, +-1 same mode 90, relative major/minor 75, +-1 with a mode
# change 50) and are deliberately unchanged. Distances of 2+ used to collapse
# to a flat 0.0, which was fine while Key doubled as a hard filter — everything
# scoring 0 was dropped. Now that the filter is opt-in (``harmonic_only``), a
# flat 0 would leave 18 of the 24 slots tied and make the axis unable to rank
# most of the library, so those distances decay instead. The two rows stay
# interleaved (same-mode d always beats cross-mode d, which beats same-mode
# d+1) so the merged ordering is strictly monotone in harmonic distance.
_KEY_SAME = (100.0, 90.0, 40.0, 32.0, 24.0, 16.0, 8.0)
_KEY_CROSS = (75.0, 50.0, 34.0, 27.0, 20.0, 13.0, 6.0)

#: Every distinct score the two tables can produce, best first. Exported so
#: the Key column can give each one its own colour without restating the
#: numbers — a colour table copied by hand goes stale the first time a
#: distance is added.
KEY_SCORE_LEVELS: tuple[float, ...] = tuple(sorted(set(_KEY_SAME) | set(_KEY_CROSS), reverse=True))


def _parse_camelot(v: str | None) -> tuple[int, str] | None:
    """``"8A"`` → ``(8, "A")``; None for anything unparseable.

    Guards the int() cast: the column is free-form text and a malformed value
    must degrade to "unknown key", never raise mid-rank.
    """
    if not v or len(v) < 2:
        return None
    try:
        return int(v[:-1]), v[-1]
    except ValueError:
        return None


#: Upper bound for the key caches below. A clean library has 24 keys, so
#: 24 x 24 pairs; the slack absorbs the odd malformed value in the free-form
#: column without letting a pathological library grow the cache unbounded.
_KEY_CACHE_SIZE = 4096


@functools.lru_cache(maxsize=_KEY_CACHE_SIZE)
def _key_distance(a: str | None, b: str | None) -> tuple[int, bool] | None:
    """``(wheel distance 0–6, same mode)`` for two Camelot codes, or None.

    Memoised because every re-rank asks it once per candidate (and the
    transpose search seven times), always about the same few hundred pairs.
    """
    pa, pb = _parse_camelot(a), _parse_camelot(b)
    if pa is None or pb is None:
        return None
    an, al = pa
    bn, bl = pb
    return min(abs(bn - an), 12 - abs(bn - an)), al == bl


def key_gap(a: str | None, b: str | None) -> tuple[int, bool] | None:
    """``(steps round the wheel, same mode)`` — the public form of the gap.

    Both halves matter to a reader. 8A to 8B is zero steps but a mode change:
    the relative major/minor, which shares a key signature with the source
    and nothing else. Reporting only the steps would call that the same key.
    """
    return _key_distance(a, b)


def key_distance(a: str | None, b: str | None) -> int | None:
    """Steps round the Camelot wheel between two keys, the short way (0-6).

    Wheel position only. Use :func:`key_gap` where the mode matters.
    """
    d = _key_distance(a, b)
    return None if d is None else d[0]


def _score_key(a: str | None, b: str | None) -> float:
    """0–100 harmonic closeness. 0.0 means *unknown*, not *incompatible* —
    use :func:`_harmonic_compatible` for the mixing-rule predicate."""
    d = _key_distance(a, b)
    if d is None:
        return 0.0
    diff, same = d
    return (_KEY_SAME if same else _KEY_CROSS)[diff]


#: A semitone is seven places round the Camelot wheel, because the wheel is
#: the circle of fifths: 8A + 1 semitone = 3A, + 2 = 10A.
_SEMITONE_STEPS = 7

#: What a semitone of transposing costs, in score. Set so an untouched
#: neighbour on the wheel (90) still beats a perfect match that needs a
#: semitone (100 - 12): reach for the Key knob only when it buys something.
TRANSPOSE_COST = 12.0

#: How far the transpose axis will look. Beyond three semitones a track
#: audibly changes character, whatever the harmony says.
MAX_TRANSPOSE = 3


def transpose_camelot(camelot: str | None, semitones: int) -> str | None:
    """*camelot* moved by *semitones*, or None if it cannot be read.

    The mode never changes — transposing a minor key gives a minor key —
    so only the wheel number moves, seven places per semitone.
    """
    parsed = _parse_camelot(camelot)
    if parsed is None:
        return None
    number, letter = parsed
    moved = (number - 1 + semitones * _SEMITONE_STEPS) % 12 + 1
    return f"{moved}{letter}"


def best_transpose(
    source: str | None, candidate: str | None, *, max_semitones: int = MAX_TRANSPOSE
) -> tuple[int, float]:
    """``(semitones, score)`` — the best this candidate can do if you move
    Traktor's Key knob, and by how much.

    Ties go to the smaller shift, and to no shift at all.
    """
    return _best_transpose_cached(source, candidate, max_semitones)


@functools.lru_cache(maxsize=_KEY_CACHE_SIZE)
def _best_transpose_cached(
    source: str | None, candidate: str | None, max_semitones: int
) -> tuple[int, float]:
    """The search behind :func:`best_transpose`, memoised.

    It runs once per candidate on every re-rank and tries up to seven
    shifts, each re-parsing and re-formatting a Camelot code; that was a
    third of a re-rank. The answer depends only on the two codes and the
    shift limit, a space of a few thousand, so it is computed once.
    """
    if _parse_camelot(source) is None or _parse_camelot(candidate) is None:
        return 0, 0.0
    best_shift, best_score = 0, -1.0
    for shift in sorted(range(-max_semitones, max_semitones + 1), key=abs):
        moved = transpose_camelot(candidate, shift)
        score = _score_key(source, moved) - abs(shift) * TRANSPOSE_COST
        if score > best_score:
            best_shift, best_score = shift, score
    return best_shift, max(0.0, best_score)


def _harmonic_compatible(a: str | None, b: str | None) -> bool:
    """True for the classic Camelot mixing set: same key, +-1 on the wheel,
    the relative major/minor, and +-1 with a mode change.

    Split out from :func:`_score_key` so the hard filter keeps its exact
    historical meaning even though the score no longer bottoms out at 0.
    """
    d = _key_distance(a, b)
    return d is not None and d[0] <= 1


def _score_rating(rating: int | None) -> float:
    """Unrated (0) → neutral 50; 1–5 stars → 20–100."""
    if not rating:
        return 50.0
    return rating / 5 * 100.0


def _row_text(row: sqlite3.Row, col: str) -> str:
    """Safe string read of a (possibly absent / null) text column."""
    try:
        v = row[col]
    except Exception:
        return ""
    return v if isinstance(v, str) else ""


def _norm_value(spec: FeatureSpec, row: sqlite3.Row) -> float | None:
    """Read a scalar feature from a row and map it into [0, 1] via its normalizer."""
    if spec.column is None or spec.normalize is None:
        return None
    try:
        if spec.derived:
            from core.library_snapshot import DERIVED

            raw = DERIVED[spec.column](row)
        else:
            raw = row[spec.column]
    except Exception:
        return None
    if raw is None:
        return None
    try:
        return spec.normalize(float(raw))
    except Exception:
        return None


def _score_scalar(spec: FeatureSpec, target: float, row: sqlite3.Row) -> float | None:
    """Closeness of a candidate's normalised feature to the target. 0–100, or None.

    A true Gaussian (not a clamped parabola): it decreases *monotonically* with
    distance and never floors to 0 within [0, 1]. So a candidate closer to the
    target always outscores a farther one — even when the target sits at an
    extreme where every candidate is far. The old parabola clamped everything
    past ~0.31 to 0, so far-from-target candidates all tied and the slider
    appeared to do nothing at the low/high ends.
    """
    cand = _norm_value(spec, row)
    if cand is None:
        return None
    diff = cand - target
    sigma = spec.sigma or VIBE_SIGMA
    return math.exp(-(diff * diff) / (2.0 * sigma * sigma)) * 100.0


def absolute_axes(axes: list[ActiveAxis]) -> list[ActiveAxis]:
    """The active axes that carry a target of their own.

    An axis can only rank without a current track if it holds an absolute
    target: a scalar dialled in by the user. Relational axes (bpm/key/
    similarity/genre/style) compare *against* the current track, and a scalar
    axis with its sync lock on is only mirroring that track's value — both are
    meaningless with nothing loaded, so both are dropped here.
    """
    return [a for a in axes if a.strength > 0 and not a.sync and not BY_KEY[a.key].relational]
