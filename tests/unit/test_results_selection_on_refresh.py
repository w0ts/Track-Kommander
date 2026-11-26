"""A new track's matches start at the top, with the best match selected.

Re-ranks for the same track (a slider nudge, a tempo change) keep the row the
user was on; a new source track is a fresh list, so it must not inherit the
old scroll position or selection.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QSettings
from PySide6.QtWidgets import QApplication

_N = 300


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def tab(qapp, tmp_path, monkeypatch):
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path))
    QCoreApplication.setApplicationName("tk-refresh-sel-test")
    QCoreApplication.setOrganizationDomain("refreshsel.invalid")
    import core.db as db

    monkeypatch.setattr(db, "_db_path", lambda: tmp_path / "t.db")
    db.init_db()
    from ui.now_playing import NowPlayingTab

    w = NowPlayingTab()
    w.resize(900, 400)
    w.show()

    def fake_load_more() -> None:
        batch = w._filtered_results[w._loaded :]
        start = w.table.rowCount()
        w.table.setRowCount(start + len(batch))
        for i, r in enumerate(batch):
            w.table._row_paths[start + i] = r["row"]["path"]
        w._loaded += len(batch)

    monkeypatch.setattr(w, "_load_more", fake_load_more)
    monkeypatch.setattr(w, "_maybe_rebuild_columns", lambda: None)
    monkeypatch.setattr(w, "_row_search_text", lambda row: row["path"])
    # A source track's matches (with none, a random sample is listed: core.browse_sample).
    w._source = {"path": "/m/source.mp3", "bpm": 120.0, "camelot": "8A"}
    w._all_results = [{"row": {"path": f"/m/{i}.mp3"}} for i in range(_N)]
    w._apply_filter()
    yield w
    w.deleteLater()


def _selected(w) -> int:
    rows = w.table.selectionModel().selectedRows()
    return rows[0].row() if rows else -1


def _browse_to_bottom(w) -> None:
    w.navigate_results(_N)
    assert _selected(w) == _N - 1
    assert w.table.verticalScrollBar().value() > 0


def test_new_track_selects_the_first_match_and_scrolls_to_top(tab) -> None:
    _browse_to_bottom(tab)
    tab._apply_filter(to_top=True)
    assert _selected(tab) == 0
    assert tab.table.verticalScrollBar().value() == 0


def test_rerank_for_the_same_track_keeps_the_selected_row(tab) -> None:
    _browse_to_bottom(tab)
    tab._apply_filter()
    assert _selected(tab) == _N - 1


def test_rerank_that_drops_the_selected_track_falls_back_to_the_first(tab) -> None:
    _browse_to_bottom(tab)
    tab._all_results = tab._all_results[:-1]
    tab._apply_filter()
    assert _selected(tab) == 0


def test_set_source_row_with_a_new_track_resets_to_the_top(tab, monkeypatch) -> None:
    calls: list[bool] = []
    monkeypatch.setattr(tab, "_refresh", lambda *, to_top=False: calls.append(to_top))
    monkeypatch.setattr(tab, "_refresh_info_line", lambda: None)
    tab._set_source_row({"path": "/a.mp3"})
    tab._set_source_row({"path": "/a.mp3"})  # keepalive: same track, no rebuild
    tab._set_source_row({"path": "/b.mp3"})
    assert calls == [True, True]
