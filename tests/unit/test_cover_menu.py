"""Right-click on the cover art opens the app's menu, on every platform.

On Windows the menu bar is hidden, so this menu is the only place a user
finds Share Log when asked for one.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6.QtWidgets")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QApplication, QMenu, QWidget

import ui.log_actions
from ui.now_playing import NowPlayingTab


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


class _Tab(QWidget):
    """Just what the menu uses of NowPlayingTab."""

    open_settings_requested = Signal()
    open_library_requested = Signal()
    open_stats_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._drag_handle = QWidget(self)


@pytest.fixture
def shown(qapp):
    tab = _Tab()
    return tab, NowPlayingTab._build_cover_menu(tab)


def _action(menu: QMenu, text: str):
    return next(a for a in menu.actions() if a.text() == text)


def test_the_menu_offers_the_log(shown) -> None:
    _tab, menu = shown
    texts = [a.text() for a in menu.actions() if a.text()]
    assert texts == ["Settings…", "Library…", "Library Stats…", "Show Log Folder", "Share Log…"]


def test_share_log_runs_the_shared_action(shown, monkeypatch) -> None:
    _tab, menu = shown
    called = []
    monkeypatch.setattr(ui.log_actions, "share_log", called.append)
    _action(menu, "Share Log…").trigger()
    assert len(called) == 1


def test_settings_asks_the_window_to_open_it(shown) -> None:
    tab, menu = shown
    heard = []
    tab.open_settings_requested.connect(lambda: heard.append(True))
    _action(menu, "Settings…").trigger()
    assert heard == [True]
