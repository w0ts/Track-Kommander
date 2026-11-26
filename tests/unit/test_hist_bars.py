"""A bin that holds tracks must be visible.

The CLAP mood features are pinned: 829 of 872 tracks land in one bin. The
other 43 were drawn a third of a pixel tall, so the card read as empty when
it was actually saying something useful — "this feature is a constant".
"""

from __future__ import annotations

import pytest

from ui.stats import bar_height

PLOT = 76.0


def test_an_empty_bin_draws_nothing() -> None:
    assert bar_height(0, 829, PLOT) == 0.0


def test_the_tallest_bin_fills_the_plot() -> None:
    assert bar_height(829, 829, PLOT) == pytest.approx(PLOT)


def test_a_bin_with_one_track_is_still_visible() -> None:
    h = bar_height(1, 829, PLOT)
    assert h >= 2.0, "a bin holding tracks must not vanish"


def test_proportions_are_kept_where_they_are_visible() -> None:
    assert bar_height(400, 800, PLOT) == pytest.approx(PLOT / 2)


def test_a_bigger_bin_is_never_shorter_than_a_smaller_one() -> None:
    heights = [bar_height(c, 829, PLOT) for c in (0, 1, 2, 5, 40, 829)]
    assert heights == sorted(heights)


def test_a_tiny_plot_area_is_not_an_error() -> None:
    assert 0.0 <= bar_height(1, 100, 1.0) <= 1.0
