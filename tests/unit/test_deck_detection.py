"""Regression tests for ``detect_deck_letters``.

The deck-letter glyph is a bright, roughly SQUARE, HOLLOW-outline character at
the top-right of each deck panel (a real "A"/"B" fills only ~0.33–0.50 of its
box). The deck panels also contain solid square UI tiles (e.g. the blue ♪
"load" box) and off-letter junk (BPM-clock digits, knob LEDs); an earlier
version rejected the letters by area and let that junk hijack the regions.
These tests build synthetic captures and assert the letters win.
"""

from __future__ import annotations

import pytest

# scipy/numpy/PySide6 are runtime deps; skip cleanly if a bare env lacks them.
np = pytest.importorskip("numpy")
pytest.importorskip("scipy")
QtGui = pytest.importorskip("PySide6.QtGui")

import core.drag as drag  # noqa: E402

QImage = QtGui.QImage
QPainter = QtGui.QPainter
QColor = QtGui.QColor

W, H = 2000, 1104
GREY = QColor(180, 180, 180)
BG = QColor(10, 10, 12)


def _blank() -> QImage:
    img = QImage(W, H, QImage.Format.Format_RGBA8888)
    img.fill(BG)
    return img


def _fill(painter: QPainter, cx: int, cy: int, w: int, h: int, color: QColor = GREY) -> None:
    """Draw a filled (solid) rect centred on (cx, cy)."""
    painter.fillRect(cx - w // 2, cy - h // 2, w, h, color)


def _glyph(painter: QPainter, cx: int, cy: int, size: int = 22, color: QColor = GREY) -> None:
    """Draw a HOLLOW square outline centred on (cx, cy) — a letter-like glyph.

    Fill ≈ 0.47 of its bounding box, matching a real deck letter (solid tiles
    would be ~1.0 and are deliberately rejected by the detector's max-fill).
    """
    half = size // 2
    border = max(2, size // 6)
    painter.fillRect(cx - half, cy - half, size, size, color)
    painter.fillRect(
        cx - half + border, cy - half + border, size - 2 * border, size - 2 * border, BG
    )


def _letter_anchor(deck: str) -> tuple[int, int]:
    """Top-right-ish point of a deck's search region — where the glyph sits."""
    x0, y0, x1, y1 = drag._deck_search_regions(W, H)[deck]
    return (x1 - 30, y0 + 30)


def _draw_decks(painter: QPainter, decks: list[str]) -> dict[str, tuple[int, int]]:
    """Draw a hollow letter glyph for each deck; return their anchor points."""
    anchors = {}
    for d in decks:
        cx, cy = _letter_anchor(d)
        _glyph(painter, cx, cy)
        anchors[d] = (cx, cy)
    return anchors


def _detect(img: QImage) -> dict[str, tuple[int, int]]:
    return {k: (round(v[0]), round(v[1])) for k, v in drag.detect_deck_letters(img).items()}


def test_two_deck_finds_only_ab() -> None:
    img = _blank()
    p = QPainter(img)
    anchors = _draw_decks(p, ["A", "B"])
    p.end()
    found = _detect(img)
    assert set(found) == {"A", "B"}
    for d, (ax, ay) in anchors.items():
        assert abs(found[d][0] - ax) <= 2 and abs(found[d][1] - ay) <= 2


def test_four_deck_finds_all() -> None:
    img = _blank()
    p = QPainter(img)
    anchors = _draw_decks(p, ["A", "B", "C", "D"])
    p.end()
    found = _detect(img)
    assert set(found) == {"A", "B", "C", "D"}
    for d, (ax, ay) in anchors.items():
        assert abs(found[d][0] - ax) <= 2 and abs(found[d][1] - ay) <= 2


def test_letter_beats_solid_tile() -> None:
    """A SOLID square tile (e.g. the blue ♪ load box) must not outvote the
    hollow letter glyph — the detector rejects high-fill blobs."""
    img = _blank()
    p = QPainter(img)
    anchors = _draw_decks(p, ["A", "B", "C", "D"])
    # Solid square tiles in each lower-deck region: square (passes aspect) and
    # larger than the letter, but ~1.0 filled — must be rejected by max-fill.
    for d in ("C", "D"):
        x0, y0, x1, y1 = drag._deck_search_regions(W, H)[d]
        _fill(p, (x0 + x1) // 2, (y0 + y1) // 2, 26, 26)
    p.end()
    found = _detect(img)
    assert set(found) == {"A", "B", "C", "D"}
    for d in ("C", "D"):
        ax, ay = anchors[d]
        assert abs(found[d][0] - ax) <= 2 and abs(found[d][1] - ay) <= 2


def test_lone_bottom_deck_is_rejected() -> None:
    """A 2-deck layout with a spurious bright glyph in the D region must NOT
    report a deck D — decks only ever appear as A+B or A+B+C+D, never a lone
    bottom deck. (Regression: false "D" mis-loaded the wrong deck on drag.)"""
    img = _blank()
    p = QPainter(img)
    _draw_decks(p, ["A", "B"])
    # A letter-like glyph in the D region but with no C partner.
    x0, y0, x1, y1 = drag._deck_search_regions(W, H)["D"]
    _glyph(p, x1 - 30, y0 + 30)
    p.end()
    assert set(_detect(img)) == {"A", "B"}


def test_misaligned_bottom_pair_is_rejected() -> None:
    """C/D present but NOT column-aligned below A/B (e.g. junk) are dropped."""
    img = _blank()
    p = QPainter(img)
    _draw_decks(p, ["A", "B"])
    # Both C and D regions have a glyph, but shoved to the wrong side of their
    # column (not under A/B), so they fail the alignment check.
    for deck in ("C", "D"):
        x0, y0, x1, y1 = drag._deck_search_regions(W, H)[deck]
        _glyph(p, x0 + 30, y0 + 30)  # left edge of region, not under A/B anchor
    p.end()
    assert set(_detect(img)) == {"A", "B"}


def test_colored_letter_is_detected() -> None:
    """The active deck's letter can be COLOURED (saturated), not grey — the
    hue-agnostic mask must still find it."""
    img = _blank()
    p = QPainter(img)
    cx, cy = _letter_anchor("A")
    _glyph(p, cx, cy, color=QColor(67, 139, 247))  # saturated Traktor blue
    bx, by = _letter_anchor("B")
    _glyph(p, bx, by, color=QColor(248, 113, 66))  # saturated orange
    p.end()
    found = _detect(img)
    assert set(found) == {"A", "B"}
    assert abs(found["A"][0] - cx) <= 2 and abs(found["A"][1] - cy) <= 2


def test_huge_waveform_blob_is_ignored() -> None:
    """A wide bright waveform band (over max_area) must be rejected outright."""
    img = _blank()
    p = QPainter(img)
    anchors = _draw_decks(p, ["A", "B", "C", "D"])
    # A fat horizontal bar across the C/D band — one big component, > max_area.
    p.fillRect(200, 410, 1600, 24, GREY)
    p.end()
    found = _detect(img)
    assert set(found) == {"A", "B", "C", "D"}
    for d, (ax, ay) in anchors.items():
        assert abs(found[d][0] - ax) <= 2 and abs(found[d][1] - ay) <= 2
