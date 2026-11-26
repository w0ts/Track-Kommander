"""The cached key scorers must give exactly what the plain algorithm gave.

``best_transpose`` runs once per candidate on every re-rank, and used to
re-parse the Camelot strings for each of its seven trial shifts. It is now
memoised. The input space is tiny (24 keys by 24 keys by a handful of
shift limits), so a cache is safe, but only if it never changes a score.
These tests pin it against a copy of the original loop.
"""

from __future__ import annotations

import pytest

import core.matcher as matcher
from core.matcher import _KEY_CROSS, _KEY_SAME, TRANSPOSE_COST, best_transpose

_ALL_KEYS = [f"{n}{mode}" for n in range(1, 13) for mode in ("A", "B")]
# Values the free-form column really holds besides clean codes.
_ODD_KEYS = [None, "", "A", "8", "xA", "nonsense"]


def _ref_parse(v):
    if not v or len(v) < 2:
        return None
    try:
        return int(v[:-1]), v[-1]
    except ValueError:
        return None


def _ref_score_key(a, b) -> float:
    pa, pb = _ref_parse(a), _ref_parse(b)
    if pa is None or pb is None:
        return 0.0
    diff = min(abs(pb[0] - pa[0]), 12 - abs(pb[0] - pa[0]))
    return (_KEY_SAME if pa[1] == pb[1] else _KEY_CROSS)[diff]


def _ref_transpose(camelot, semitones):
    parsed = _ref_parse(camelot)
    if parsed is None:
        return None
    number, letter = parsed
    return f"{(number - 1 + semitones * 7) % 12 + 1}{letter}"


def _ref_best_transpose(source, candidate, max_semitones):
    """The algorithm exactly as it stood before the cache went in."""
    if _ref_parse(source) is None or _ref_parse(candidate) is None:
        return 0, 0.0
    best_shift, best_score = 0, -1.0
    for shift in sorted(range(-max_semitones, max_semitones + 1), key=abs):
        moved = _ref_transpose(candidate, shift)
        score = _ref_score_key(source, moved) - abs(shift) * TRANSPOSE_COST
        if score > best_score:
            best_shift, best_score = shift, score
    return best_shift, max(0.0, best_score)


@pytest.mark.parametrize("max_semitones", range(7))
def test_best_transpose_matches_the_reference_for_every_key_pair(max_semitones: int) -> None:
    for src in _ALL_KEYS + _ODD_KEYS:
        for cand in _ALL_KEYS + _ODD_KEYS:
            got = best_transpose(src, cand, max_semitones=max_semitones)
            want = _ref_best_transpose(src, cand, max_semitones)
            # Exact equality on purpose: the ranking compares these floats.
            assert got == want, (src, cand, max_semitones)
            assert type(got[1]) is float


def test_score_key_matches_the_reference_for_every_key_pair() -> None:
    for src in _ALL_KEYS + _ODD_KEYS:
        for cand in _ALL_KEYS + _ODD_KEYS:
            assert matcher._score_key(src, cand) == _ref_score_key(src, cand), (src, cand)


def test_best_transpose_is_memoised() -> None:
    """The point of the change: a repeated pair is answered from the cache."""
    matcher._best_transpose_cached.cache_clear()
    best_transpose("8A", "10A", max_semitones=3)
    best_transpose("8A", "10A", max_semitones=3)
    info = matcher._best_transpose_cached.cache_info()
    assert info.hits == 1 and info.misses == 1
