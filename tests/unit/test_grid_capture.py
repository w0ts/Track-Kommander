"""Pressing the capture button: what gets queued, and what gets refused."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QSettings
from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def tab(qapp, tmp_path, monkeypatch):
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path))
    QCoreApplication.setApplicationName("tk-grid-capture-test")
    QCoreApplication.setOrganizationDomain("gridcap.invalid")
    import core.db as db

    monkeypatch.setattr(db, "_db_path", lambda: tmp_path / "t.db")
    db.init_db()
    import core.grid_fix as gf
    import ui.now_playing as np_mod

    monkeypatch.setattr(np_mod, "grid_queue_path", lambda: tmp_path / "fixes.json")
    w = np_mod.NowPlayingTab()
    w._deck_info = {"A": {"file_path": "/m/a.mp3", "title": "T", "artist": "Ar", "bpm": 130.0}}
    w._playing_decks = {"A"}
    for i in range(6):
        w._phases.add("A", 0.27, at=100.0 + i)
    w._grid_queue = gf.GridQueue(tmp_path / "fixes.json")
    yield w
    w.deleteLater()


def _notices(tab, monkeypatch) -> list[tuple[str, str]]:
    seen: list[tuple[str, str]] = []
    tab.notice_requested.connect(lambda text, sev: seen.append((text, sev)))
    return seen


def test_a_settled_phase_is_queued(tab, monkeypatch) -> None:
    seen = _notices(tab, monkeypatch)
    assert tab.capture_grid_offset("A") is True
    q = tab._grid_queue.entries()
    assert [(f.path, f.phase, f.bpm) for f in q] == [("/m/a.mp3", 0.27, 130.0)]
    assert seen and "125" in seen[0][0]  # ms, in the notice


def test_a_drifting_phase_is_refused(tab) -> None:
    seen = _notices(tab, None)
    for i, ph in enumerate((0.1, 0.2, 0.3, 0.4, 0.5, -0.4)):
        tab._phases.add("A", ph, at=200.0 + i)
    assert tab.capture_grid_offset("A") is False
    assert tab._grid_queue.entries() == []
    assert seen and "tempo" in seen[0][0].lower()


def test_an_empty_deck_is_refused(tab) -> None:
    seen = _notices(tab, None)
    assert tab.capture_grid_offset("C") is False
    assert tab._grid_queue.entries() == []
    assert seen and "no track" in seen[0][0].lower()


def test_a_track_with_no_bpm_is_refused(tab) -> None:
    tab._deck_info["A"]["bpm"] = 0
    seen = _notices(tab, None)
    assert tab.capture_grid_offset("A") is False
    assert seen and "bpm" in seen[0][0].lower()


def test_a_half_beat_offset_is_a_normal_capture(tab) -> None:
    """A grid on the offbeat reads ±0.5, and that is exactly the error to
    fix. Half a beat early and half a beat late differ by one beat, which is
    the same grid — so there is nothing ambiguous to refuse."""
    for i in range(6):
        tab._phases.add("A", -0.50, at=300.0 + i)
    assert tab.capture_grid_offset("A") is True
    assert [f.phase for f in tab._grid_queue.entries()] == [-0.50]


def test_the_queue_survives_the_panel(tab, tmp_path) -> None:
    tab.capture_grid_offset("A")
    import core.grid_fix as gf

    assert len(gf.GridQueue(tmp_path / "fixes.json").entries()) == 1


# ── Right-click the deck ────────────────────────────────────────────────────


def _deck_menu(tab, monkeypatch, deck: str) -> list[tuple[str, bool]]:
    """The entries a right-click on a deck button would show."""
    items: list[tuple[str, bool]] = []

    class _Menu:
        def __init__(self, parent=None):
            pass

        def addSeparator(self):
            pass

        def addAction(self, label):
            from PySide6.QtGui import QAction

            act = QAction(label)
            items.append((label, True))
            _Menu.last = act
            return act

        def exec(self, pos):
            if getattr(_Menu, "last", None) is not None and _Menu.last.isEnabled():
                _Menu.last.trigger()

    monkeypatch.setattr("ui.np_deck_api.QMenu", _Menu)
    from PySide6.QtCore import QPoint

    tab._show_deck_menu(deck, QPoint(1, 1))
    return [(lb, en) for lb, en in items]


def test_the_deck_menu_offers_the_fix_with_the_offset(tab, monkeypatch) -> None:
    """Milliseconds to judge the size, beats to recognise the error —
    a quarter-beat and a half-beat grid are the ones you see by eye."""
    items = _deck_menu(tab, monkeypatch, "A")
    label = next(lb for lb, _e in items if "Fix phase" in lb)
    assert "+125 ms" in label
    assert "+0.270 beat" in label
    assert tab._grid_queue.entries()  # triggering it captured


def test_a_half_beat_is_named_as_such_in_the_menu(tab, monkeypatch) -> None:
    for i in range(6):
        tab._phases.add("A", -0.500, at=400.0 + i)
    items = _deck_menu(tab, monkeypatch, "A")
    label = next(lb for lb, _e in items if "Fix phase" in lb)
    assert "-0.500 beat" in label and "half a beat" in label


def test_the_deck_menu_explains_a_drifting_phase(tab, monkeypatch) -> None:
    for i, ph in enumerate((0.1, 0.2, 0.3, 0.4, 0.5, -0.4)):
        tab._phases.add("A", ph, at=200.0 + i)
    items = _deck_menu(tab, monkeypatch, "A")
    assert any("tempo" in lb.lower() for lb, _e in items), items
    assert tab._grid_queue.entries() == []


def test_the_deck_menu_on_an_empty_deck_says_so(tab, monkeypatch) -> None:
    items = _deck_menu(tab, monkeypatch, "C")
    assert any("no track" in lb.lower() for lb, _e in items), items
