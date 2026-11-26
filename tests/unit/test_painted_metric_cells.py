"""Result metric cells are painted items, not per-cell widgets.

The UI watchdog showed every results refresh stalling 250–500 ms in
``_make_bar_cell``: one QWidget + layout + QFrame per bar cell, ~500 per page.
They are now plain items painted by ``MetricCellDelegate``, and a row's rich
tooltip (cover art included) is built on hover instead of for every row.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, QSettings
from PySide6.QtGui import QColor, QHelpEvent, QImage, QPainter
from PySide6.QtWidgets import (
    QApplication,
    QStyleOptionViewItem,
    QTableWidgetItem,
)

from ui import widgets


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


def _paint_cell(qapp, item: QTableWidgetItem, w: int = 100, h: int = 24) -> QImage:
    table = widgets.ResultsTable(1, 1)
    table.setItem(0, 0, item)
    delegate = widgets.MetricCellDelegate(table)
    img = QImage(w, h, QImage.Format.Format_ARGB32)
    img.fill(QColor(0, 0, 0))
    opt = QStyleOptionViewItem()
    opt.rect = img.rect()
    opt.font = table.font()
    p = QPainter(img)
    delegate.paint(p, opt, table.model().index(0, 0))
    p.end()
    table.deleteLater()
    return img


def test_a_bar_fills_its_fraction_of_the_cell(qapp) -> None:
    it = QTableWidgetItem()
    it.setData(widgets.BAR_ROLE, (0.5, QColor(255, 0, 0)))
    img = _paint_cell(qapp, it)
    mid_y = img.height() // 2
    assert QColor(img.pixel(20, mid_y)).red() > 200  # inside the bar
    assert QColor(img.pixel(80, mid_y)).red() < 50  # past the value


def test_an_empty_bar_paints_nothing(qapp) -> None:
    it = QTableWidgetItem()
    it.setData(widgets.BAR_ROLE, (0.0, QColor(255, 0, 0)))
    img = _paint_cell(qapp, it)
    assert all(QColor(img.pixel(x, img.height() // 2)).red() < 50 for x in range(img.width()))


def test_genre_parts_are_painted(qapp) -> None:
    it = QTableWidgetItem()
    it.setData(widgets.PARTS_ROLE, [("Techno", QColor(0, 255, 0))])
    img = _paint_cell(qapp, it, w=160)
    greens = sum(
        QColor(img.pixel(x, y)).green() > 100
        for x in range(img.width())
        for y in range(img.height())
    )
    assert greens > 0


def test_the_tooltip_is_built_on_hover(qapp, monkeypatch) -> None:
    table = widgets.ResultsTable(3, 1)
    for r in range(3):
        table.setItem(r, 0, QTableWidgetItem("x"))
    asked: list[int] = []
    table.row_tooltip_provider = lambda row: asked.append(row) or f"tip {row}"
    shown: list[str] = []
    monkeypatch.setattr(table, "_show_tip", lambda _src, _pos, text, _rect=None: shown.append(text))
    pos = table.visualRect(table.model().index(1, 0)).center()
    table.viewportEvent(QHelpEvent(QEvent.Type.ToolTip, pos, table.viewport().mapToGlobal(pos)))
    assert asked == [], "nothing is built while the show delay runs"
    table._pending_timer.stop()
    table._show_pending_tip()
    assert asked == [1]
    assert shown == ["tip 1"]
    table.deleteLater()


def _paint_score_cell(table, delegate, column: int) -> QImage:
    rect = table.visualRect(table.model().index(0, column))
    img = QImage(rect.width(), rect.height(), QImage.Format.Format_ARGB32)
    img.fill(QColor(0, 0, 0))
    opt = QStyleOptionViewItem()
    opt.rect = img.rect()
    opt.font = table.font()
    p = QPainter(img)
    delegate.paint(p, opt, table.model().index(0, column))
    p.end()
    return img


def test_the_score_bar_follows_a_column_resize(qapp) -> None:
    """The bar's pixels come from the column widths at paint time, so widening
    the first lead column pushes the fill out of the second one."""
    table = widgets.ResultsTable(1, 3)
    delegate = widgets.ScoreBarDelegate(table, (0, 1, 2))
    for c in range(3):
        table.setColumnWidth(c, 100)
        it = QTableWidgetItem("")
        it.setData(widgets.FILL_COLOR_ROLE, QColor(255, 0, 0))
        it.setData(widgets.FILL_FRAC_ROLE, 0.5)  # fill ends at 150 of 300 px
        table.setItem(0, c, it)

    img = _paint_score_cell(table, delegate, 1)
    mid_y = img.height() // 2
    assert QColor(img.pixel(40, mid_y)).red() > 60, "filled up to 150 px"
    assert QColor(img.pixel(70, mid_y)).red() < 40, "empty past it"

    table.setColumnWidth(0, 200)  # fill now ends at 200 px
    img = _paint_score_cell(table, delegate, 1)
    assert all(QColor(img.pixel(x, mid_y)).red() < 40 for x in range(img.width()))
    table.deleteLater()


def test_span_segment() -> None:
    assert widgets.span_segment([100, 100, 100], 1, 0.5) == (100.0, 150.0)
    assert widgets.span_segment([100, 100], 0, 2.0) == (0.0, 200.0)


# ── The real results table ──────────────────────────────────────────────────


@pytest.fixture
def tab(qapp, tmp_path, monkeypatch):
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path))
    QCoreApplication.setApplicationName("tk-painted-cells-test")
    QCoreApplication.setOrganizationDomain("painted.invalid")
    import core.db as db

    monkeypatch.setattr(db, "_db_path", lambda: tmp_path / "t.db")
    db.init_db()
    from ui.now_playing import NowPlayingTab

    w = NowPlayingTab()
    yield w
    w.deleteLater()


def _result(path: str) -> dict:
    row = {
        "path": path,
        "artist": "Nina",
        "title": "Untitled",
        "album": "",
        "bpm": 124.0,
        "camelot": "8A",
        "rating": 3,
        "genres": None,
        "genre": "Techno",
    }
    return {"row": row, "composite": 70.0, "axis_scores": {}}


def test_a_results_page_creates_no_cell_widgets_or_tooltips(tab, monkeypatch) -> None:
    built: list[str] = []
    monkeypatch.setattr(
        "ui.np_columns._row_tooltip", lambda r, _src: built.append(r["row"]["path"]) or "tip"
    )
    tab._source = None
    tab._filtered_results = [_result(f"/m/{i}.mp3") for i in range(5)]
    tab._loaded = 0
    tab.table.setRowCount(0)
    tab._load_more()

    assert tab.table.rowCount() == 5
    assert all(
        tab.table.cellWidget(r, c) is None for r in range(5) for c in range(tab.table.columnCount())
    )
    assert built == [], "tooltips must not be built while populating"
    assert tab._row_tooltip_at(3) == "tip"
    assert built == ["/m/3.mp3"]
    assert tab._row_tooltip_at(99) == ""
