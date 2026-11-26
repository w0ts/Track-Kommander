"""The Key-T column: the key you would play, and the shift to get there.

A transpose axis is no use unless the number is on screen — the whole
point is knowing how far to move Traktor's Key knob. Shown in Open Key
notation, like the Key column beside it.
"""

from __future__ import annotations

from ui.now_playing import transposed_key_text


def test_no_shift_reads_as_the_plain_key() -> None:
    """A track that already fits must still show, and show cleanly."""
    assert transposed_key_text("8A", 0) == "1m"


def test_an_upward_shift_shows_the_new_key_and_the_move() -> None:
    assert transposed_key_text("8A", 2) == "3m  +2"


def test_a_downward_shift_keeps_its_sign() -> None:
    assert transposed_key_text("10A", -2) == "1m  -2"


def test_a_missing_key_is_a_dash() -> None:
    assert transposed_key_text(None, 1) == "—"
    assert transposed_key_text("", 0) == "—"


def test_no_axis_means_no_shift_to_show() -> None:
    """The matcher reports None when the transpose axis is off."""
    assert transposed_key_text("8A", None) == "1m"


def test_the_wheel_wraps() -> None:
    assert transposed_key_text("12A", 1) == "12m  +1"
