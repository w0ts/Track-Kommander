"""Sidebar glyphs come from different fonts — a colour emoji, a dingbat, a
plain ×  — so no font-size makes them the same size. Rendering each into a
fixed box does."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QIcon, QImage
from PySide6.QtWidgets import QApplication

from ui.icons import glyph_icon, ink_box


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


def _ink(icon: QIcon, box: int, mode=QIcon.Mode.Normal) -> tuple[int, int]:
    """(width, height) of the drawn pixels, in device pixels / dpr."""
    pm = icon.pixmap(box, box, mode)
    img = pm.toImage().convertToFormat(QImage.Format.Format_ARGB32)
    left, top, right, bottom = ink_box(img)
    dpr = pm.devicePixelRatio() or 1
    return round((right - left + 1) / dpr), round((bottom - top + 1) / dpr)


@pytest.mark.parametrize("glyph", ["×", "⚙", "♫", "🎧", "▦", "⊘", "▶"])
def test_every_glyph_fills_the_box(qapp, glyph: str) -> None:
    """Whatever the font drew, the icon's ink spans the box in at least one
    direction — that is what makes them all the same size."""
    box = 22
    w, h = _ink(glyph_icon(glyph, "#888888", "#ffffff", box=box), box)
    assert max(w, h) >= box - 2, (glyph, w, h)
    assert max(w, h) <= box


def test_the_hover_colour_is_a_second_pixmap(qapp) -> None:
    icon = glyph_icon("×", "#888888", "#ffffff", box=22)
    normal = icon.pixmap(22, 22, QIcon.Mode.Normal).toImage()
    active = icon.pixmap(22, 22, QIcon.Mode.Active).toImage()
    assert normal != active


def test_icons_are_cached(qapp) -> None:
    a = glyph_icon("♫", "#888888", "#ffffff", box=22)
    b = glyph_icon("♫", "#888888", "#ffffff", box=22)
    assert a.cacheKey() == b.cacheKey()
