"""The embedded window turns libtk's native-track POSTs into a per-deck
readout. The numbers and strings cross a loopback socket from C, so a bad
value must not blank the readout — mirroring the webhook-payload tests.

The window is built without calling ``start()``, so no loopback port is
bound and no thread is spawned; the bridge signals are driven directly.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6.QtWidgets")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from ui.embedded_window import EmbeddedWindow


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def win(qapp):
    w = EmbeddedWindow()
    yield w
    w.deleteLater()


def test_a_native_track_shows_on_its_deck(win) -> None:
    win._on_native_track(
        {"title": "Circuit", "artist": "Forest Drive West", "bpm": 100.0, "deck": "a"}
    )
    text = win._decks_lbl.text()
    assert "<b>A</b>" in text
    assert "Forest Drive West" in text and "Circuit" in text
    assert "100.0" in text
    assert win._heading.text() == "Track Kommander — embedded"


def test_the_deck_letter_is_upper_cased_and_kept_distinct(win) -> None:
    win._on_native_track({"title": "One", "deck": "a"})
    win._on_native_track({"title": "Two", "deck": "b"})
    text = win._decks_lbl.text()
    assert "<b>A</b>" in text and "One" in text
    assert "<b>B</b>" in text and "Two" in text


def test_a_later_track_replaces_the_deck(win) -> None:
    win._on_native_track({"title": "First", "deck": "c"})
    win._on_native_track({"title": "Second", "deck": "c"})
    text = win._decks_lbl.text()
    assert "Second" in text
    assert "First" not in text


def test_a_track_with_no_deck_is_ignored(win) -> None:
    win._on_native_track({"title": "Nowhere"})
    assert win._decks_lbl.text() == ""
    assert win._heading.text() == "Waiting for Traktor…"


def test_a_bad_bpm_still_shows_the_track(win) -> None:
    win._on_native_track({"title": "T", "artist": "A", "bpm": "fast", "deck": "d"})
    text = win._decks_lbl.text()
    assert "<b>D</b>" in text and "A" in text and "T" in text
    assert "0.0" in text  # bad bpm → 0.0, not a crash


def test_missing_title_and_artist_do_not_blank_the_row(win) -> None:
    win._on_native_track({"deck": "a"})
    text = win._decks_lbl.text()
    assert "<b>A</b>" in text
    assert "—" in text  # placeholder title


def test_deck_loaded_bridge_path_also_feeds_the_readout(win) -> None:
    # If the QML D2 bridge is live too, its deckLoaded feeds the same view.
    win._on_deck_loaded("B", {"title": "Bridged", "artist": "X", "bpm": 128.0})
    assert "Bridged" in win._decks_lbl.text()


def test_start_is_needed_before_a_watcher_exists(win) -> None:
    # __init__ must not bind the port / spawn the thread.
    assert win._watcher is None
