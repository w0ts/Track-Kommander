"""The Key column cell: which key, what to do to the knob, how far away.

Three facts in one narrow column. The key you will hear, the shift that gets
you there, and the distance round the wheel from what is playing — the last
all of it painted in the match colour, so "how well does this fit" is
answered before the text is even read.
"""

from __future__ import annotations

from core.matcher import key_distance
from ui.now_playing import key_cell_text
from ui.style import key_score_color

# ── Distance round the wheel ───────────────────────────────────────────────


def test_the_same_key_is_no_distance() -> None:
    assert key_distance("8A", "8A") == 0


def test_a_neighbour_is_one_step() -> None:
    assert key_distance("8A", "9A") == 1
    assert key_distance("8A", "7A") == 1


def test_the_wheel_wraps_the_short_way() -> None:
    """12A and 1A are neighbours, not eleven steps apart."""
    assert key_distance("12A", "1A") == 1


def test_it_never_exceeds_half_the_wheel() -> None:
    assert key_distance("1A", "7A") == 6


def test_the_wheel_step_ignores_the_mode() -> None:
    """The raw number is wheel position only — 8A and 8B sit on the same
    spoke. What the *column* does with that is a separate question, below."""
    assert key_distance("8A", "8B") == 0


def test_the_gap_carries_the_mode_too() -> None:
    from core.matcher import key_gap

    assert key_gap("8A", "8B") == (0, False)
    assert key_gap("8A", "8A") == (0, True)
    assert key_gap("8A", "9B") == (1, False)
    assert key_gap("8A", None) is None


def test_an_unreadable_key_has_no_distance() -> None:
    assert key_distance("8A", None) is None
    assert key_distance("banana", "8A") is None


# ── The text ───────────────────────────────────────────────────────────────


def test_an_untransposed_track_is_key_then_distance() -> None:
    assert key_cell_text("9A", 0, "8A") == "2m 1"


def test_a_transposed_track_carries_the_shift_against_the_key() -> None:
    """ "12m+1 3" — the key you will hear, the knob move, the distance."""
    assert key_cell_text("12A", 1, "10A") == "12m+1 3"


def test_the_key_shown_is_the_one_you_will_hear() -> None:
    """Not the one written on the track. 8A transposed up two is 10A, and
    10A is what comes out of the speakers."""
    assert key_cell_text("8A", 2, "8A").startswith("3m+2")


def test_a_downward_shift_keeps_its_sign() -> None:
    assert key_cell_text("8A", -1, "8A") == "6m-1 5"


def test_the_distance_is_measured_from_the_key_you_will_hear() -> None:
    """A transpose that lands on the source key is zero away, however far
    the written key was."""
    assert key_cell_text("9A", 5, "8A") == "1m+5 0"


def test_no_key_is_a_dash() -> None:
    assert key_cell_text(None, 0, "8A") == "—"
    assert key_cell_text("", 0, "8A") == "—"


def test_a_relative_major_minor_is_not_zero_away() -> None:
    """12m to 12d is the relative major/minor: the same key signature, but a
    different tonic and a different mode. A bare "0" reads as "same key",
    which is the one thing it is not — the tilde says "same spoke, other
    mode"."""
    assert key_cell_text("7B", 0, "7A") == "12d ~0"


def test_the_diagonal_is_marked_too() -> None:
    """+-1 across modes: one step *and* a mode change."""
    assert key_cell_text("8B", 0, "7A") == "1d ~1"


def test_a_same_mode_move_has_no_mark() -> None:
    """The mark has to mean something, so it only appears when it applies."""
    assert "~" not in key_cell_text("8A", 0, "7A")
    assert "~" not in key_cell_text("7A", 0, "7A")


def test_no_source_key_leaves_the_distance_out() -> None:
    """Browsing with an empty deck: there is nothing to be far from."""
    assert key_cell_text("8A", 0, None) == "1m"


# ── The colour ─────────────────────────────────────────────────────────────


def test_the_whole_cell_takes_the_match_colour() -> None:
    """Key, knob move and distance all in one colour: the column is read at
    a glance, and one colour per cell is what a glance can take in."""
    from core.matcher import _score_key

    exact = key_score_color(_score_key("8A", "8A")).name()
    near = key_score_color(_score_key("8A", "9A")).name()
    assert exact != near, "a perfect match and a neighbour must differ"
