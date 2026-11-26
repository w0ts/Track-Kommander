"""Where Traktor's browser starts, from a capture of its window.

Every layout draws the same thing there: a pure-black separator line across
the window, a dark row, then the browser's column-header band at gray 33.
That is what the library overlay should cover from — not a fixed gap that
slices the C/D decks in a 4-deck layout.
"""

from __future__ import annotations

import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QImage

from core.drag import detect_browser_top


def _image(
    h: int = 1000,
    w: int = 1600,
    *,
    line_at: int | None = 700,
    header: bool = True,
    extra_line_at: int | None = 60,
    background: int = 17,
    band: int = 33,
    thick: int = 1,
) -> QImage:
    a = np.full((h, w, 3), background, dtype=np.uint8)
    rng = np.random.default_rng(1)
    a[100 : line_at or 650] = rng.integers(0, 120, size=(((line_at or 650) - 100), w, 3))
    if extra_line_at is not None:
        # A deck/toolbar separator: black, then the background just goes on.
        a[extra_line_at : extra_line_at + thick] = 0
        a[extra_line_at + thick : extra_line_at + 40] = background
    if line_at is not None:
        a[line_at : line_at + thick] = 0
        a[line_at + thick : line_at + thick + 2] = background
        if header:
            a[line_at + thick + 2 : line_at + thick + 22] = band
            a[line_at + thick + 7 : line_at + thick + 13, 300:900] = 110  # header text
        a[line_at + thick + 24 :] = background
    img = QImage(a.tobytes(), w, h, w * 3, QImage.Format.Format_RGB888)
    return img.copy()


def test_the_live_fullscreen_palette_two_pixel_lines() -> None:
    """What the app's own capture looked like: background 24, band 38,
    separators two rows thick — and the deck-row separator 330 px higher."""
    img = _image(h=1410, w=2560, line_at=713, extra_line_at=387, background=24, band=38, thick=2)
    assert detect_browser_top(img) == pytest.approx(714 / 1410, abs=0.002)


def test_finds_the_separator_above_the_header_band() -> None:
    assert detect_browser_top(_image(line_at=700)) == pytest.approx(0.700, abs=0.002)


def test_a_two_deck_layout_puts_it_higher(qapp=None) -> None:
    assert detect_browser_top(_image(line_at=480)) == pytest.approx(0.480, abs=0.002)


def test_a_black_line_without_the_header_band_is_not_it() -> None:
    assert detect_browser_top(_image(line_at=700, header=False)) is None


def test_nothing_found_is_none() -> None:
    assert detect_browser_top(_image(line_at=None)) is None


def test_the_users_screenshots(tmp_path) -> None:
    """The two real captures this was derived from, when they are around."""
    from pathlib import Path

    shots = {
        "four.png": 721 / 1094,
        "two.png": 484 / 1116,
    }
    base = Path(
        "/private/tmp/claude-501/-Users-mots-GitHub-TraktorKontrol/"
        "e93bc344-bbfc-4d25-9194-feee17e240e3/scratchpad"
    )
    found = False
    for name, expected in shots.items():
        p = base / name
        if not p.is_file():
            continue
        found = True
        assert detect_browser_top(QImage(str(p))) == pytest.approx(expected, abs=0.003)
    if not found:
        pytest.skip("reference screenshots not on this machine")
