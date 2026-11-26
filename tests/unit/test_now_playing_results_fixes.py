"""Regression tests for the NOW PLAYING results table and its controls.

Each test pins one bug that was found by reading the code:

* a table rebuild moved the selection model's current row, which fired the
  Traktor pre-search and stopped the deck loaded with Enter;
* removing a track left every row below it pointing at the wrong track;
* dimming a played track touched hard-coded column numbers;
* a second re-analyse click dropped the only reference to a running thread;
* a cancelled Auto DJ transition's timer unlocked the next one early;
* identical deck pings restyled all four deck buttons every time;
* every keystroke in the filter re-filtered the whole list synchronously;
* axis cards were restyled on every re-rank and slider tick;
* a first page that fits the viewport left the rest unreachable by mouse.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6.QtWidgets")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QObject, QSettings, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication

import core.db as db
import ui.dialogs as dialogs
import ui.now_playing as np_mod
from ui.now_playing import NowPlayingTab


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def tab(qapp, tmp_path, monkeypatch):
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path))
    QCoreApplication.setApplicationName("tk-results-fixes-test")
    QCoreApplication.setOrganizationDomain("results-fixes.invalid")
    monkeypatch.setattr(db, "_db_path", lambda: tmp_path / "t.db")
    db.init_db()
    w = NowPlayingTab()
    w.resize(900, 500)
    w.show()
    yield w
    w.hide()
    w.deleteLater()


def _result(i: int, *, composite: float | None = 70.0) -> dict:
    row = {
        "path": f"/m/{i}.mp3",
        "artist": f"Artist {i}",
        "title": f"Title {i}",
        "album": f"Album {i}",
        "bpm": 124.0,
        "camelot": "8A",
        "rating": 3,
        "genres": None,
        "genre": "Techno",
    }
    return {"row": row, "composite": composite, "axis_scores": {}}


def _fill(tab, n: int) -> None:
    tab._all_results = [_result(i) for i in range(n)]
    tab._apply_filter()


def _selected_path(tab) -> str | None:
    rows = tab.table.selectionModel().selectedRows()
    return tab.table._row_paths.get(rows[0].row()) if rows else None


# ── 1. A rebuild is not a user selection ────────────────────────────────────


def test_a_rebuild_does_not_fire_the_traktor_pre_search(tab) -> None:
    _fill(tab, 30)
    tab.table.selectRow(5)  # the user picks a row …
    tab._search_debounce.stop()  # … and its pre-search has already run
    tab._apply_filter()
    assert not tab._search_debounce.isActive(), (
        "rebuilding the table must not look like a user selection"
    )
    assert _selected_path(tab) == "/m/5.mp3"


def test_a_rebuild_from_no_selection_does_not_fire_it_either(tab) -> None:
    _fill(tab, 30)
    tab._search_debounce.stop()
    tab._apply_filter(to_top=True)
    assert not tab._search_debounce.isActive()


def test_restore_selection_keeps_an_outer_callers_guard(tab) -> None:
    _fill(tab, 5)
    tab._restoring_selection = True
    tab._restore_selection("/m/2.mp3")
    assert tab._restoring_selection is True
    tab._restoring_selection = False


# ── 2. Removing a track keeps rows and tracks in step ───────────────────────


def test_removing_a_track_keeps_every_row_on_its_own_track(tab, monkeypatch) -> None:
    removed: list[str] = []
    monkeypatch.setattr(dialogs, "confirm", lambda *_a, **_k: True)
    monkeypatch.setattr("ui.np_actions.remove_track", removed.append)
    _fill(tab, 30)
    tab.table.selectRow(3)
    tab._search_debounce.stop()

    tab._remove_from_db("/m/3.mp3", 3)

    assert removed == ["/m/3.mp3"]
    assert all(r["row"]["path"] != "/m/3.mp3" for r in tab._all_results)
    for ri in range(tab.table.rowCount()):
        path = tab.table._row_paths[ri]
        assert tab._filtered_results[ri]["row"]["path"] == path
        artist = tab.table.item(ri, _col(tab, "artist")).text()
        assert artist == f"Artist {path.split('/')[-1].split('.')[0]}"
    assert _selected_path(tab) == "/m/4.mp3", "the user stays where they were"
    assert not tab._search_debounce.isActive()


def test_removing_the_last_row_selects_the_one_above(tab, monkeypatch) -> None:
    monkeypatch.setattr(dialogs, "confirm", lambda *_a, **_k: True)
    monkeypatch.setattr("ui.np_actions.remove_track", lambda _p: None)
    _fill(tab, 4)
    tab.table.selectRow(3)
    tab._remove_from_db("/m/3.mp3", 3)
    assert tab.table.rowCount() == 3
    assert _selected_path(tab) == "/m/2.mp3"


# ── 3. Played rows dim the right columns ────────────────────────────────────


def _col(tab, cid: str) -> int:
    return next(i for i, c in enumerate(tab._col_model) if c["id"] == cid)


def test_dimming_a_played_track_hits_its_text_columns(tab) -> None:
    _fill(tab, 3)
    tab._dim_played_rows({"/m/1.mp3"})
    played = QColor("#666688")
    for cid in ("artist", "album", "title"):
        if not any(c["id"] == cid for c in tab._col_model):
            continue
        assert tab.table.item(1, _col(tab, cid)).foreground().color() == played, cid
        assert tab.table.item(0, _col(tab, cid)).foreground().color() != played, cid


def test_a_played_row_looks_the_same_after_a_rebuild(tab) -> None:
    _fill(tab, 3)
    tab._dim_played_rows({"/m/1.mp3"})
    before = [
        tab.table.item(1, c).foreground().color().name()
        for c in range(tab.table.columnCount())
        if tab.table.item(1, c)
    ]
    tab._session_played.add("/m/1.mp3")
    tab._apply_filter()
    after = [
        tab.table.item(1, c).foreground().color().name()
        for c in range(tab.table.columnCount())
        if tab.table.item(1, c)
    ]
    assert before == after


# ── 4. One re-analyse at a time ─────────────────────────────────────────────


class _FakeWorker:
    instances: list = []

    def __init__(self, paths, force=False, parent=None):

        class _Sig(QObject):
            finished = Signal(int, int, dict)
            error = Signal(str)

        self._sig = _Sig()
        self.finished = self._sig.finished
        self.error = self._sig.error
        self.paths = paths
        self.running = False
        _FakeWorker.instances.append(self)

    def start(self) -> None:
        self.running = True

    def isRunning(self) -> bool:
        return self.running


def test_a_second_reanalyse_while_one_runs_is_refused(tab, monkeypatch) -> None:
    _FakeWorker.instances = []
    monkeypatch.setattr("ui.np_actions.AnalysisWorker", _FakeWorker)
    notices: list[str] = []
    tab.notice_requested.connect(lambda text, _sev: notices.append(text))
    tab._reanalyse("/m/1.mp3")
    tab._reanalyse("/m/2.mp3")
    assert len(_FakeWorker.instances) == 1
    assert tab._reanalyse_worker is _FakeWorker.instances[0]
    assert notices, "the refused click is explained"
    assert not tab.table.isEnabled()

    first = _FakeWorker.instances[0]
    first.running = False
    first.finished.emit(1, 0, {})
    assert tab.table.isEnabled()
    tab._reanalyse("/m/2.mp3")
    assert len(_FakeWorker.instances) == 2


# ── 5. A cancelled transition's deadline is cancelled too ───────────────────


def test_a_stale_transition_deadline_cannot_unlock_a_newer_one(tab) -> None:
    tab._auto_loading = True
    tab._auto_unlock_timer.start(30_000)
    tab._cancel_auto_transition()
    assert not tab._auto_unlock_timer.isActive()

    tab._auto_loading = True  # a new transition begins
    tab._auto_unlock_timer.start(30_000)
    assert tab._auto_loading
    tab._apply_auto_mode()
    assert not tab._auto_unlock_timer.isActive()
    assert not tab._auto_loading


def test_the_unlock_timer_releases_the_lock(tab) -> None:
    tab._auto_loading = True
    tab._auto_unlock_timer.timeout.emit()
    assert not tab._auto_loading


# ── 6. Deck pings don't restyle unchanged buttons ───────────────────────────


def test_identical_deck_pings_do_not_restyle_the_buttons(tab, monkeypatch) -> None:
    tab._deck_info["A"] = {"file_path": "/m/0.mp3", "artist": "A", "title": "T"}
    tab.set_deck_playing("A", True)
    tab.update_deck_on_air("A", 1.0)

    calls: list[str] = []
    for letter, btn in tab._deck_btns.items():
        orig = btn.setStyleSheet
        monkeypatch.setattr(
            btn, "setStyleSheet", lambda qss, _o=orig, _l=letter: (calls.append(_l), _o(qss))
        )
    for _ in range(5):
        for d in "ABCD":
            tab.set_deck_playing(d, d == "A")
            tab.update_deck_on_air(d, 1.0 if d == "A" else 0.0)
    assert calls == []


def test_reselecting_the_same_deck_skips_the_info_line(tab, monkeypatch) -> None:
    tab._deck_info["A"] = {"file_path": "/m/0.mp3"}
    tab._select_deck("A")
    refreshed: list[int] = []
    monkeypatch.setattr(tab, "_refresh_info_line", lambda: refreshed.append(1))
    tab._select_deck("A")
    assert refreshed == []


def test_play_state_of_the_selected_deck_still_updates_the_info_line(tab, monkeypatch) -> None:
    tab._deck_info["A"] = {"file_path": "/m/0.mp3"}
    tab._select_deck("A")
    refreshed: list[int] = []
    monkeypatch.setattr(tab, "_refresh_info_line", lambda: refreshed.append(1))
    tab.set_deck_playing("A", True)
    assert refreshed, "the live tempo depends on whether the deck plays"


def test_a_failed_load_flash_is_restyled_back(tab) -> None:
    tab._refresh_deck_buttons()
    normal = tab._deck_btns["B"].styleSheet()
    tab._flash_deck_load_failed("B")
    assert tab._deck_btns["B"].styleSheet() != normal
    tab._refresh_deck_buttons()
    assert tab._deck_btns["B"].styleSheet() == normal


# ── 7. Filter typing is debounced and cached ────────────────────────────────


def test_typing_filters_after_a_pause(tab) -> None:
    _fill(tab, 30)
    tab.search_edit.setText("title 1")
    assert tab.table.rowCount() == 30, "no work per keystroke"
    assert tab._filter_debounce.isActive()
    tab._filter_debounce.stop()
    tab._apply_search_text()
    # Terms AND together: "title" matches every row, "1" rows 1, 10–19, 21.
    assert tab.table.rowCount() == 12


def test_search_text_is_built_once_per_track(tab, monkeypatch) -> None:
    _fill(tab, 10)
    built: list[str] = []
    orig = type(tab)._row_search_text
    monkeypatch.setattr(tab, "_row_search_text", lambda row: built.append(row["path"]) or orig(row))
    for text in ("t", "ti", "tit"):
        tab.search_edit.setText(text)
        tab._filter_debounce.stop()
        tab._apply_search_text()
    assert len(built) == 10
    tab._all_results = [_result(i) for i in range(10)]  # a re-rank
    tab.search_edit.setText("titl")
    tab._filter_debounce.stop()
    tab._apply_search_text()
    assert len(built) == 20, "a new result list is searched afresh"


# ── 9. Axis cards keep their stylesheet when nothing changed ────────────────


def test_card_background_is_not_reapplied_when_unchanged(tab, monkeypatch) -> None:
    key = next(iter(tab._axis_widgets))
    card = tab._axis_widgets[key]["card"]
    tab._update_card_background(key)
    calls: list[int] = []
    orig = card.setStyleSheet
    monkeypatch.setattr(card, "setStyleSheet", lambda qss: (calls.append(1), orig(qss)))
    tab._update_card_background(key)
    tab._update_card_background(key)
    assert calls == []


def test_a_strength_tick_restyles_only_what_changed(tab, monkeypatch) -> None:
    key = (
        next(k for k, r in tab._axis_widgets.items() if "val" in r)
        if any("val" in r for r in tab._axis_widgets.values())
        else next(iter(tab._axis_widgets))
    )
    refs = tab._axis_widgets[key]
    tab._on_axis_strength_changed(key, 50)
    calls: list[str] = []
    for name in ("label", "val", "sync_btn", "card"):
        if name in refs:
            orig = refs[name].setStyleSheet
            monkeypatch.setattr(
                refs[name],
                "setStyleSheet",
                lambda qss, _o=orig, _n=name: (calls.append(_n), _o(qss)),
            )
    tab._on_axis_strength_changed(key, 60)
    assert calls == []


# ── 10. A short first page still leads to the rest ──────────────────────────


def test_rows_keep_loading_until_the_viewport_is_filled(tab) -> None:
    row_h = tab.table.verticalHeader().defaultSectionSize()
    tab.table.setMinimumHeight(row_h * (np_mod._PAGE + 20))
    tab.resize(900, row_h * (np_mod._PAGE + 20) + 400)
    QApplication.processEvents()
    _fill(tab, 300)
    assert tab._loaded > np_mod._PAGE
    assert tab._loaded < 300, "only what the viewport (plus a margin) needs"
