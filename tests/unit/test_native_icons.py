"""TK's band icons for Traktor: glyph silhouettes in one colour, Traktor's pixel layout."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from PySide6.QtWidgets import QApplication

from ui.native_icons import (
    AUTO_GLYPHS,
    BUTTON_GLYPHS,
    ICON_H,
    ICON_W,
    LIT_ARGB,
    OFF_ARGB,
    push_band_icons,
    silhouette,
)


@pytest.fixture(scope="module")
def app() -> Iterator[QApplication]:
    yield QApplication.instance() or QApplication([])


@pytest.mark.parametrize("glyph", [*BUTTON_GLYPHS.values(), *AUTO_GLYPHS.values()])
def test_every_glyph_draws_something_in_exactly_one_colour(app, glyph) -> None:
    for argb in (OFF_ARGB, LIT_ARGB):
        px = silhouette(glyph, argb)
        assert px.shape == (ICON_W * ICON_H,)
        alpha = px >> 24
        assert (alpha > 0).sum() > 10, glyph  # the glyph is there
        assert set((px & 0x00FFFFFF).tolist()) == {argb & 0x00FFFFFF}  # one colour, emoji too


def test_outside_traktor_nothing_is_sent(app) -> None:
    assert push_band_icons() == 0


@pytest.mark.parametrize("glyph", [*BUTTON_GLYPHS.values(), *AUTO_GLYPHS.values()])
def test_every_glyph_fills_the_icon_like_the_others(app, glyph) -> None:
    """Fonts draw symbols at their own sizes (⌨ ⚖ ⟳ came out a third of the rest)."""
    alpha = (silhouette(glyph, OFF_ARGB) >> 24).reshape(ICON_H, ICON_W)
    ys, xs = alpha.nonzero()
    width, height = xs.max() - xs.min() + 1, ys.max() - ys.min() + 1
    assert width >= ICON_W - 4 or height >= ICON_H - 4, glyph
