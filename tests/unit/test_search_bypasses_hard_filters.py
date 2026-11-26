"""A search is an explicit ask for a track. The hard filters — BPM restrict,
Harmonic only — trim browsing noise; they must not hide what was asked for.

"FKA" found only "Puffka": the FKA twigs track at 166 BPM was cut by a ±12%
BPM restrict in SQL before the search box ever saw it.
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
    QCoreApplication.setApplicationName("tk-search-test")
    QCoreApplication.setOrganizationDomain("search.invalid")
    import core.db as db

    monkeypatch.setattr(db, "_db_path", lambda: tmp_path / "t.db")
    db.init_db()
    import ui.now_playing as np_mod

    calls: list[dict] = []

    def _rank(source, axes, max_bpm_pct=0, src_bpm_override=None, *, harmonic_only=False):
        calls.append({"bpm": max_bpm_pct, "harmonic": harmonic_only})
        return []

    monkeypatch.setattr("ui.np_results.rank_matches_axes", _rank)
    w = np_mod.NowPlayingTab()
    w._settings.setValue("bpm_restrict", True)
    w._settings.setValue("bpm_restrict_pct", 12)
    w._settings.setValue("harmonic_only", True)
    w._source = {"path": "/src.mp3", "bpm": 120.0, "camelot": "2A"}
    w.calls = calls
    yield w
    w.deleteLater()


def _type(tab, text: str) -> None:
    """Type *text* into the filter box and let the typing pause run out."""
    tab.search_edit.setText(text)
    tab._filter_debounce.stop()
    tab._apply_search_text()


def test_the_filters_apply_while_browsing(tab) -> None:
    tab._refresh()
    assert tab.calls[-1] == {"bpm": 12, "harmonic": True}


def test_a_search_lifts_the_filters(tab) -> None:
    tab._refresh()
    _type(tab, "fka")
    assert tab.calls[-1] == {"bpm": 0, "harmonic": False}


def test_typing_more_does_not_rerank_again(tab) -> None:
    tab._refresh()
    _type(tab, "f")
    n = len(tab.calls)
    _type(tab, "fk")
    _type(tab, "fka")
    assert len(tab.calls) == n, "only the empty/non-empty transition re-ranks"


def test_clearing_the_search_puts_the_filters_back(tab) -> None:
    tab._refresh()
    _type(tab, "fka")
    _type(tab, "")
    assert tab.calls[-1] == {"bpm": 12, "harmonic": True}


def test_an_empty_deck_means_no_source_track(tab, monkeypatch) -> None:
    """With no track to take BPM, key or synced values from, nothing relational
    can filter or rank. The absolute axes still can, and the search stays."""
    absolute: list[int] = []
    monkeypatch.setattr(
        "ui.np_results.rank_matches_absolute", lambda axes: absolute.append(1) or []
    )
    monkeypatch.setattr("ui.np_results.list_all_tracks_for_browsing", lambda: [])
    tab._refresh()
    _type(tab, "fka")
    n_relational = len(tab.calls)

    tab._deck_info = {}  # every deck empty
    tab._on_deck_clicked("C")

    assert tab._source is None
    assert absolute, "ranks on the absolute axes, not against the old track"
    assert len(tab.calls) == n_relational, "no relational ranking without a source"
    assert tab.search_edit.text() == "fka"
