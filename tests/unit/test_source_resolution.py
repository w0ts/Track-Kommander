"""Regression tests for source-track resolution in the NOW PLAYING panel.

The D2 bridge reports channel-fader / on-air changes continuously, and every
one of those re-runs ``_auto_select_active_deck`` → ``_select_deck``. When the
deck's track is *not* in the library, ``_select_deck`` used to fall through to
``set_track`` → ``_show_library_browse`` on every single ping, rebuilding the
whole results table and destroying the user's selection several times a second.

These tests pin the guards that stop that:

* an unchanged (deck, file_path) short-circuits before any DB lookup;
* a repeated miss for the same query does not rebuild the browse listing;
* a genuine change (new track, different deck) still resolves;
* clicking a deck button always re-resolves, so a track analysed since the
  last ping is picked up.

``NowPlayingTab`` builds its entire UI in ``__init__`` (and hits the library DB),
so the tests construct a bare instance and attach only the collaborators the
methods under test touch.
"""

from __future__ import annotations

import types

import pytest

pytest.importorskip("PySide6.QtWidgets")

import ui.now_playing as np_mod


class _Calls:
    """Counters for the collaborators we assert against."""

    def __init__(self) -> None:
        self.find_by_path = 0
        self.browse = 0
        self.source_rows: list[str] = []


def _make_panel(monkeypatch, library: dict[str, object]) -> tuple[object, _Calls]:
    """A NowPlayingTab with just enough state for _select_deck / set_track.

    ``library`` maps file path → fake DB row (anything truthy); a path absent
    from it behaves like a track that has not been analysed.
    """
    calls = _Calls()
    panel = np_mod.NowPlayingTab.__new__(np_mod.NowPlayingTab)

    panel._source = None
    panel._source_key = None
    panel.show_cover_art = True
    panel._browse_query = None
    panel._deck_info = {}
    panel._deck_btns = {}
    panel._drag_handle = types.SimpleNamespace(set_art=lambda _art: None)
    panel.info_lbl = types.SimpleNamespace(setText=lambda _t: None)
    panel._refresh_deck_buttons = lambda: None
    panel._refresh_info_line = lambda: None

    def _browse() -> None:
        calls.browse += 1

    def _set_source_row(row) -> None:
        panel._source = row
        panel._browse_query = None
        calls.source_rows.append(row["path"])

    panel._show_library_browse = _browse
    panel._set_source_row = _set_source_row

    def _find_by_path(path: str):
        calls.find_by_path += 1
        return library.get(path)

    monkeypatch.setattr(np_mod, "find_track_by_path", _find_by_path)
    monkeypatch.setattr(np_mod, "find_track", lambda _q: None)
    monkeypatch.setattr(np_mod, "_load_album_art", lambda _p: None)
    return panel, calls


def _deck(path: str, artist: str = "Nina", title: str = "Untitled") -> dict:
    return {"file_path": path, "artist": artist, "title": title}


# ── Unknown track: repeated pings must not rebuild the table ────────────────


def test_repeated_pings_for_unknown_track_browse_once(monkeypatch) -> None:
    panel, calls = _make_panel(monkeypatch, library={})
    panel._deck_info["A"] = _deck("/music/not-scanned.mp3")

    for _ in range(20):
        panel._select_deck("A")

    assert calls.browse == 1, "library browse rebuilt more than once"
    assert calls.find_by_path == 1, "DB re-queried on an unchanged deck"


def test_unknown_track_then_new_track_resolves_again(monkeypatch) -> None:
    panel, calls = _make_panel(monkeypatch, library={})
    panel._deck_info["A"] = _deck("/music/first.mp3", title="First")
    panel._select_deck("A")
    panel._select_deck("A")

    panel._deck_info["A"] = _deck("/music/second.mp3", title="Second")
    panel._select_deck("A")

    assert calls.find_by_path == 2
    assert calls.browse == 2, "a different unknown track must refresh the listing"


# ── Known track ─────────────────────────────────────────────────────────────


def test_known_track_resolved_once_across_pings(monkeypatch) -> None:
    row = {"path": "/music/known.mp3"}
    panel, calls = _make_panel(monkeypatch, library={"/music/known.mp3": row})
    panel._deck_info["B"] = _deck("/music/known.mp3")

    for _ in range(10):
        panel._select_deck("B")

    assert calls.source_rows == ["/music/known.mp3"]
    assert calls.browse == 0


def test_switching_deck_resolves_the_other_track(monkeypatch) -> None:
    rows = {"/music/a.mp3": {"path": "/music/a.mp3"}, "/music/b.mp3": {"path": "/music/b.mp3"}}
    panel, calls = _make_panel(monkeypatch, library=rows)
    panel._deck_info["A"] = _deck("/music/a.mp3")
    panel._deck_info["B"] = _deck("/music/b.mp3")

    panel._select_deck("A")
    panel._select_deck("A")
    panel._select_deck("B")

    assert calls.source_rows == ["/music/a.mp3", "/music/b.mp3"]


# ── Empty deck ──────────────────────────────────────────────────────────────


def test_empty_deck_does_not_requery(monkeypatch) -> None:
    """An empty deck becomes the source once — no track, so the library is
    browsed on the absolute axes — and the pings that follow do nothing."""
    panel, calls = _make_panel(monkeypatch, library={})

    for _ in range(5):
        panel._select_deck("C")  # no _deck_info entry at all

    assert calls.find_by_path == 0
    assert calls.browse == 1
    assert panel._source is None


def test_the_info_line_clears_on_the_first_click_of_an_empty_deck(monkeypatch) -> None:
    """BPM and key used to linger until the second click: the line was
    repainted before the source was dropped."""
    panel, _calls = _make_panel(monkeypatch, library={})
    painted: list[object] = []
    panel._refresh_info_line = lambda: painted.append(panel._source)
    panel._source = {"path": "/old.mp3", "bpm": 144.0, "camelot": "3A"}

    panel._select_deck("C")

    assert painted and painted[-1] is None


# ── Manual deck click always re-resolves ───────────────────────────────────


def test_deck_click_reresolves_after_analysis(monkeypatch) -> None:
    """A track analysed between pings is picked up on the next deck click."""
    library: dict[str, object] = {}
    panel, calls = _make_panel(monkeypatch, library=library)
    panel._deck_info["A"] = _deck("/music/late.mp3")

    panel._select_deck("A")
    assert calls.browse == 1

    library["/music/late.mp3"] = {"path": "/music/late.mp3"}
    panel._on_deck_clicked("A")

    assert calls.source_rows == ["/music/late.mp3"]


# ── set_track's own miss guard ──────────────────────────────────────────────


def test_set_track_miss_browses_once_per_query(monkeypatch) -> None:
    panel, calls = _make_panel(monkeypatch, library={})

    panel.set_track("Nina - Untitled")
    panel.set_track("Nina - Untitled")
    assert calls.browse == 1

    panel.set_track("Someone - Else")
    assert calls.browse == 2


# ── Clicking the selected deck again deselects it ───────────────────────────


class _FakeDeckButton:
    def __init__(self) -> None:
        self._checked = False

    def isChecked(self) -> bool:
        return self._checked

    def setChecked(self, checked: bool) -> None:
        self._checked = checked


def test_clicking_the_selected_deck_selects_none(monkeypatch) -> None:
    """A checkable button unchecks itself on a second click; that must leave
    no deck selected and no source track, not re-select the deck."""
    row = {"path": "/music/a.mp3"}
    panel, calls = _make_panel(monkeypatch, library={"/music/a.mp3": row})
    panel._deck_btns = {d: _FakeDeckButton() for d in "ABCD"}
    panel._deck_info["A"] = _deck("/music/a.mp3")

    panel._deck_btns["A"].setChecked(True)  # Qt toggles before clicked
    panel._on_deck_clicked("A", True)
    assert panel._source is row

    panel._deck_btns["A"].setChecked(False)
    panel._on_deck_clicked("A", False)

    assert not any(b.isChecked() for b in panel._deck_btns.values())
    assert panel._source is None
    assert panel._source_key is None
    assert calls.browse == 1

    panel._deck_btns["A"].setChecked(True)  # and a third click re-selects
    panel._on_deck_clicked("A", True)
    assert calls.source_rows == ["/music/a.mp3", "/music/a.mp3"]


# ── Inside Traktor: no cover is read (its first read imports PyAV, ~0.7 s) ──


def test_inside_traktor_the_cover_is_not_read(monkeypatch) -> None:
    panel, calls = _make_panel(monkeypatch, library={"/music/a.mp3": {"path": "/music/a.mp3"}})
    reads: list[str] = []
    monkeypatch.setattr(np_mod, "_load_album_art", lambda p: reads.append(p))
    panel.show_cover_art = False
    panel._deck_info["A"] = _deck("/music/a.mp3")
    panel._select_deck("A")
    assert reads == [] and calls.source_rows == ["/music/a.mp3"]  # still resolved, no cover

    panel.show_cover_art = True
    panel._deck_info["A"] = _deck("/music/b.mp3")
    panel._select_deck("A")
    assert reads == ["/music/b.mp3"]  # standalone TK still shows it
