"""The cards strip pins at the top of Traktor's list. The geometry is a pure
function so its placement and clamping are checked without a live Traktor.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6.QtWidgets")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from ui.embedded_overlay import cards_strip_rect


def test_strip_sits_at_the_list_top_full_width() -> None:
    # Traktor at (100, 50), 1200x800; list starts 40% down.
    x, y, w, h = cards_strip_rect((100.0, 50.0, 1200.0, 800.0), 0.40, 96.0)
    assert (x, w) == (100.0, 1200.0)  # left-aligned, full width
    assert y == 50.0 + 0.40 * 800.0  # top of the list
    assert h == 96.0


def test_height_is_clamped_to_keep_the_list_visible() -> None:
    # List top near the bottom: only a little room, so the strip shrinks to
    # leave min_list_visible px below.
    _, _, _, h = cards_strip_rect((0.0, 0.0, 1000.0, 400.0), 0.90, 200.0, min_list_visible=80.0)
    # list_top = 360, list_bottom = 400, avail = 400-360-80 = -40 -> clamped to 0
    assert h == 0.0


def test_a_reasonable_strip_fits() -> None:
    _, _, _, h = cards_strip_rect((0.0, 0.0, 1000.0, 1000.0), 0.30, 120.0, min_list_visible=80.0)
    # list_top=300, bottom=1000, avail=620 -> 120 fits
    assert h == 120.0


@pytest.mark.parametrize("frac", [-0.5, 1.5])
def test_out_of_range_fraction_is_clamped(frac: float) -> None:
    x, y, w, h = cards_strip_rect((0.0, 0.0, 800.0, 600.0), frac, 50.0)
    assert 0.0 <= y <= 600.0  # never off the window
