"""Unit tests for the library-overlay top-gap geometry.

Covers ``_deck_row_top_fraction``: given detected deck-letter positions, decide
where the library overlay should start so Traktor's lower (C/D) decks stay
visible. The function is pure (no capture / Qt), so it's tested directly.
"""

from __future__ import annotations

import pytest

import core.drag as drag

# Representative capture height; the function works in fractions, so the exact
# value only matters for the pixel→fraction conversion.
IMG_H = 1000


def test_two_deck_layout_returns_none() -> None:
    """Only A/B present → no C/D to protect → caller keeps its configured gap."""
    found = {"A": (300.0, 130.0), "B": (800.0, 130.0)}
    assert drag._deck_row_top_fraction(found, IMG_H) is None


def test_empty_detection_returns_none() -> None:
    assert drag._deck_row_top_fraction({}, IMG_H) is None


def test_four_deck_pushes_below_cd_row() -> None:
    """A/B glyph at 0.13, C/D glyph at 0.46 → bottom = 2·0.46 − 0.13 − offset."""
    found = {
        "A": (300.0, 130.0),
        "B": (800.0, 130.0),
        "C": (300.0, 460.0),
        "D": (800.0, 460.0),
    }
    top = drag._deck_row_top_fraction(found, IMG_H)
    assert top is not None
    expected = 0.46 + (0.46 - 0.13) - drag._FOUR_DECK_HEADER_OFFSET
    assert abs(top - expected) < 1e-9


def test_top_fraction_is_clamped() -> None:
    """A very low C/D row must not push the overlay past the max fraction."""
    found = {
        "A": (300.0, 100.0),
        "C": (300.0, 850.0),  # extreme: would compute > max without the clamp
    }
    top = drag._deck_row_top_fraction(found, IMG_H)
    assert top == drag._FOUR_DECK_MAX_TOP_FRAC


def test_missing_ab_uses_header_fallback() -> None:
    """C/D detected without an A/B reference falls back to the header band."""
    found = {"C": (300.0, 460.0), "D": (800.0, 460.0)}
    top = drag._deck_row_top_fraction(found, IMG_H)
    assert top is not None
    expected = 0.46 + (0.46 - drag._AB_HEADER_FALLBACK_FRAC) - drag._FOUR_DECK_HEADER_OFFSET
    assert abs(top - expected) < 1e-9


def test_uses_lowest_cd_header() -> None:
    """When C and D headers differ, the lower (max y) one anchors the row."""
    found = {
        "A": (300.0, 130.0),
        "C": (300.0, 340.0),
        "D": (800.0, 360.0),  # lower than C — should drive the estimate
    }
    top = drag._deck_row_top_fraction(found, IMG_H)
    expected = 0.36 + (0.36 - 0.13) - drag._FOUR_DECK_HEADER_OFFSET
    assert abs(top - expected) < 1e-9


def test_zero_height_is_safe() -> None:
    """Guards the div-by-zero path; should not raise."""
    found = {"A": (10.0, 10.0), "C": (10.0, 20.0)}
    top = drag._deck_row_top_fraction(found, 0)
    assert top == drag._FOUR_DECK_MAX_TOP_FRAC


# ── _layout_is_four_deck ────────────────────────────────────────────────────

# ── _overlay_top_fraction ───────────────────────────────────────────────────


def test_overlay_top_fraction_two_deck_returns_none() -> None:
    """An authoritative 2-deck layout keeps the configured gap (no push)."""
    assert drag._overlay_top_fraction(False) is None


def test_overlay_top_fraction_unknown_returns_none(monkeypatch) -> None:
    """Unknown layout must NOT fall back to screenshot detection (which
    false-positives C/D on 2-deck) — it keeps the configured gap."""
    called = False

    def _boom(*_a, **_k):
        nonlocal called
        called = True
        return 0.9  # would push the overlay down if (wrongly) consulted

    monkeypatch.setattr(drag, "_library_top_fraction_4deck", _boom)
    assert drag._overlay_top_fraction(None) is None
    assert called is False, "detection must not be consulted for unknown layout"


def test_overlay_top_fraction_four_deck_uses_detection(monkeypatch) -> None:
    """A confirmed 4-deck layout pushes down using the detected fraction."""
    monkeypatch.setattr(drag, "_library_top_fraction_4deck", lambda default: 0.6)
    assert drag._overlay_top_fraction(True) == 0.6


@pytest.mark.parametrize(
    ("layout", "expected"),
    [
        ("fourDecks", True),
        ("4 light", True),
        ("4 internal", True),
        ("FourDecks", True),
        ("twoDecks", False),
        ("2 decks", False),
        ("singleDeck", False),
        ("1 deck", False),
        (None, None),
        ("", None),
        ("null", None),
        ("undefined", None),
        ("mixerOnly", None),
    ],
)
def test_layout_classifier(layout, expected) -> None:
    assert drag._layout_is_four_deck(layout) is expected


# ── The overlay starts where the browser starts ─────────────────────────────


class _Img:
    def __init__(self, h: int) -> None:
        self._h = h

    def height(self) -> int:
        return self._h


class _Win:
    def __init__(self) -> None:
        self.geom = None

    def setGeometry(self, g):
        self.geom = (g.x(), g.y(), g.width(), g.height())

    def show(self):
        pass

    def raise_(self):
        pass

    def activateWindow(self):
        pass


@pytest.fixture
def traktor(monkeypatch):
    monkeypatch.setattr(drag, "_traktor_logical_bounds", lambda: (100.0, 50.0, 1600.0, 1000.0))
    monkeypatch.setattr(drag, "capture_traktor_window", lambda: (_Img(1000), (100, 50, 1600, 1000)))


def test_snap_covers_from_the_detected_browser_line(traktor, monkeypatch) -> None:
    monkeypatch.setattr(drag, "detect_browser_top", lambda img: 0.72)
    win = _Win()
    assert drag.snap_over_traktor_library(win) is True
    assert win.geom == (100, 50 + 720, 1600, 1000 - 720)


def test_snap_falls_back_to_the_configured_gap_without_a_detection(traktor, monkeypatch) -> None:
    monkeypatch.setattr(drag, "detect_browser_top", lambda img: None)
    win = _Win()
    assert drag.snap_over_traktor_library(win) is True
    assert win.geom[1] == 50 + drag._LIB_OVERLAY_TOP_GAP_PX


def test_snap_survives_a_failed_capture(traktor, monkeypatch) -> None:
    monkeypatch.setattr(drag, "capture_traktor_window", lambda: None)
    win = _Win()
    assert drag.snap_over_traktor_library(win) is True
    assert win.geom[1] == 50 + drag._LIB_OVERLAY_TOP_GAP_PX


def test_browser_top_fraction_reads_one_capture(monkeypatch) -> None:
    monkeypatch.setattr(drag, "capture_traktor_window", lambda: (_Img(1000), (0, 0, 1, 1000)))
    monkeypatch.setattr(drag, "detect_browser_top", lambda img: 0.5)
    assert drag.browser_top_fraction() == 0.5
    monkeypatch.setattr(drag, "capture_traktor_window", lambda: None)
    assert drag.browser_top_fraction() is None


def test_the_title_bar_the_capture_leaves_out_is_put_back(monkeypatch) -> None:
    """The capture ignores framing; the bounds include the 28 px title bar.
    A line 700 px into a 1000 px content area is 728 px into the window."""
    monkeypatch.setattr(drag, "capture_traktor_window", lambda: (_Img(1000), (0, 0, 1, 1028)))
    monkeypatch.setattr(drag, "detect_browser_top", lambda img: 0.7)
    assert drag.browser_top_fraction() == pytest.approx(728 / 1028)


# ── A failed detection keeps one capture, not one every tick ────────────────


def test_a_missed_browser_line_saves_one_capture_per_episode(monkeypatch) -> None:
    """LayoutWatch probes every 3 s; while the browser is hidden each probe
    used to PNG-encode the whole capture to the log folder."""
    saved: list[str] = []
    monkeypatch.setattr(drag, "_browser_probe_missing", False)
    monkeypatch.setattr(drag, "_dump_capture", lambda img, name: saved.append(name))
    monkeypatch.setattr(drag, "capture_traktor_window", lambda: (_Img(1000), (0, 0, 1, 1000)))
    monkeypatch.setattr(drag, "detect_browser_top", lambda img: None)
    for _ in range(5):
        assert drag.browser_top_fraction() is None
    assert saved == ["snap-capture.png"]
    # Found again, then lost again: a new episode, a new capture.
    monkeypatch.setattr(drag, "detect_browser_top", lambda img: 0.5)
    assert drag.browser_top_fraction() == 0.5
    monkeypatch.setattr(drag, "detect_browser_top", lambda img: None)
    drag.browser_top_fraction()
    drag.browser_top_fraction()
    assert len(saved) == 2


def test_an_uncapturable_traktor_is_logged_once_per_episode(monkeypatch, caplog) -> None:
    import logging

    monkeypatch.setattr(drag, "_browser_probe_missing", False)
    monkeypatch.setattr(drag, "capture_traktor_window", lambda: None)
    with caplog.at_level(logging.INFO, logger=drag._log.name):
        for _ in range(4):
            drag.browser_top_fraction()
    assert sum("could not be captured" in r.getMessage() for r in caplog.records) == 1
