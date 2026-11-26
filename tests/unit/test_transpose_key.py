"""Finding tracks that fit once you move the Key knob.

Traktor transposes in semitones; the Camelot wheel is the circle of
fifths, so one semitone up is seven positions round it and the letter
never changes. 8A (A minor) + 1 = 3A (B♭ minor), + 2 = 10A (B minor).

A transposed match is a real option but a bigger intervention than an
untouched one, so each semitone costs a little.
"""

from __future__ import annotations

import pytest

from core.matcher import best_transpose, transpose_camelot

# ── Moving round the wheel ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("start", "semitones", "expected"),
    [
        ("8A", 0, "8A"),
        ("8A", 1, "3A"),  # A minor → B flat minor
        ("8A", 2, "10A"),
        ("8A", 3, "5A"),
        ("8A", -1, "1A"),  # A minor → A flat minor
        ("8B", 1, "3B"),  # the mode never changes
        ("12A", 1, "7A"),  # wraps
        ("1A", -1, "6A"),
    ],
)
def test_a_semitone_is_seven_places_round_the_wheel(
    start: str, semitones: int, expected: str
) -> None:
    assert transpose_camelot(start, semitones) == expected


def test_twelve_semitones_is_the_same_key() -> None:
    assert transpose_camelot("8A", 12) == "8A"


def test_an_unparseable_key_transposes_to_nothing() -> None:
    assert transpose_camelot("", 1) is None
    assert transpose_camelot("nonsense", 1) is None


# ── Picking the best shift ──────────────────────────────────────────────────


def test_an_exact_match_needs_no_shift() -> None:
    shift, score = best_transpose("8A", "8A", max_semitones=3)
    assert shift == 0
    assert score == pytest.approx(100.0)


def test_a_distant_key_is_found_by_transposing() -> None:
    """10A is four places from 8A — unmixable — but a whole tone down lands
    exactly on it."""
    from core.matcher import _score_key

    shift, score = best_transpose("8A", "10A", max_semitones=3)
    assert shift == -2
    assert score > _score_key("8A", "10A"), (
        "transposing has to beat leaving it alone, or there is no point"
    )


def test_a_shift_costs_something() -> None:
    """An untouched neighbour beats a transposed perfect match, or the axis
    would send you to the Key knob for no reason."""
    _s, untouched = best_transpose("8A", "9A", max_semitones=3)
    _s, shifted = best_transpose("8A", "10A", max_semitones=3)
    assert untouched > shifted


def test_a_smaller_shift_wins_when_both_land_well() -> None:
    shift, _score = best_transpose("8A", "3A", max_semitones=3)
    assert shift == -1, "one semitone down, not eleven up"


def test_nothing_reachable_scores_low_not_zero() -> None:
    _shift, score = best_transpose("8A", "2B", max_semitones=1)
    assert 0 < score < 60


def test_the_limit_is_respected() -> None:
    near, _ = best_transpose("8A", "10A", max_semitones=1)
    assert abs(near) <= 1


def test_no_transposing_allowed_is_the_plain_key_score() -> None:
    from core.matcher import _score_key

    shift, score = best_transpose("8A", "9A", max_semitones=0)
    assert shift == 0
    assert score == pytest.approx(_score_key("8A", "9A"))


def test_an_unknown_key_is_unknown_not_incompatible() -> None:
    assert best_transpose(None, "8A", max_semitones=3) == (0, 0.0)
    assert best_transpose("8A", None, max_semitones=3) == (0, 0.0)
