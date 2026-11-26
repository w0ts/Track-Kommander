"""Page Up / Page Down move the selection by a screenful of results."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QSettings
from PySide6.QtWidgets import QApplication

from core import actions


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def store(qapp, tmp_path):
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path))
    QCoreApplication.setApplicationName("tk-paging-test")
    QCoreApplication.setOrganizationDomain("paging.invalid")
    actions.reset_all()
    yield
    actions.reset_all()


@pytest.fixture
def tab(store, tmp_path, monkeypatch):
    import core.db as db

    monkeypatch.setattr(db, "_db_path", lambda: tmp_path / "t.db")
    db.init_db()
    from ui.now_playing import NowPlayingTab

    w = NowPlayingTab()
    w.table.setRowCount(200)
    for r in range(200):
        w.table._row_paths[r] = f"/m/{r}.mp3"
    yield w
    w.deleteLater()


def test_the_actions_exist_and_are_bound_to_the_page_keys(store) -> None:
    for action_id, key in (("results.page_up", 0x01000016), ("results.page_down", 0x01000017)):
        binding = actions.binding_for(action_id)
        assert binding is not None, f"{action_id} is unbound"
        assert binding.code == key


def test_a_page_moves_by_a_visible_screenful(tab) -> None:
    tab.table.selectRow(0)
    page = tab._visible_rows()  # measured before: scrolling can add a
    tab.navigate_results_page(1)  # scrollbar and change the viewport
    moved = tab.table.selectionModel().selectedRows()[0].row()
    assert moved > 1, "a page has to be more than a row"
    assert moved == page


def test_paging_stops_at_the_ends(tab) -> None:
    tab.table.selectRow(0)
    tab.navigate_results_page(-1)
    assert tab.table.selectionModel().selectedRows()[0].row() == 0
    tab.table.selectRow(tab.table.rowCount() - 1)
    tab.navigate_results_page(1)
    assert tab.table.selectionModel().selectedRows()[0].row() == tab.table.rowCount() - 1


def test_paging_down_then_up_lands_near_the_start(tab) -> None:
    """Not exactly the same row: a scrollbar appearing mid-move changes how
    many rows are on screen. Within a row or two is what matters."""
    tab.table.selectRow(40)
    tab.navigate_results_page(1)
    tab.navigate_results_page(-1)
    assert abs(tab.table.selectionModel().selectedRows()[0].row() - 40) <= 2


def test_an_empty_table_is_not_an_error(tab) -> None:
    tab.table.setRowCount(0)
    tab.navigate_results_page(1)


def test_a_page_is_never_a_single_row(tab) -> None:
    """A window so short that no row is fully visible still pages."""
    tab.table.setFixedHeight(4)
    assert tab._visible_rows() >= 2
