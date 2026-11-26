"""The deck grid shows C│D only when those decks are in use.

Traktor does not expose its 2/4-deck layout, and reading it from a
screenshot flickered (bright UI in a 2-deck layout passed as C and D). The
rule is simpler: nothing loaded or playing on C and D means a 2-deck set.
"""

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
    QCoreApplication.setApplicationName("tk-deck-grid-test")
    QCoreApplication.setOrganizationDomain("deckgrid.invalid")
    import core.db as db

    monkeypatch.setattr(db, "_db_path", lambda: tmp_path / "t.db")
    db.init_db()
    import ui.now_playing as np_mod

    w = np_mod.NowPlayingTab()
    w.show()
    yield w
    w.deleteLater()


def _row_visible(tab) -> bool:
    return not tab._deck_btns["C"].isHidden()


def test_two_decks_when_c_and_d_are_empty(tab) -> None:
    assert not _row_visible(tab)
    assert tab._deck_grid.height() == tab._deck_btns["A"].height()


def test_a_track_loaded_on_c_shows_the_second_row(tab) -> None:
    tab.update_deck("C", {"file_path": "/c.mp3", "title": "C", "artist": "x"})
    assert _row_visible(tab)
    assert tab._deck_grid.height() > tab._deck_btns["A"].height() * 1.5


def test_a_deck_playing_on_d_shows_the_second_row(tab) -> None:
    tab.set_deck_playing("D", True)
    assert _row_visible(tab)


def test_emptying_c_and_d_goes_back_to_two_decks(tab) -> None:
    tab.update_deck("C", {"file_path": "/c.mp3", "title": "C", "artist": "x"})
    tab.update_deck("C", {"file_path": "", "title": "", "artist": ""})
    assert not _row_visible(tab)


def test_loads_on_a_and_b_keep_two_decks(tab) -> None:
    tab.update_deck("A", {"file_path": "/a.mp3", "title": "A", "artist": "x"})
    tab.set_deck_playing("B", True)
    assert not _row_visible(tab)
