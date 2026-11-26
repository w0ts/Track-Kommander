"""The matching popup borrows the tab's axis panel and always gives it back."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

from ui.matching_popup import MatchingPopup


@pytest.fixture(scope="module")
def app() -> Iterator[QApplication]:
    yield QApplication.instance() or QApplication([])


class _Tab(QWidget):
    """Just what the popup uses of NowPlayingTab: the panel and its home."""

    def __init__(self) -> None:
        super().__init__()
        self._axis_panel_home = QVBoxLayout(self)
        self._axis_panel_home.addWidget(QLabel("decks"))
        self._axis_panel = QWidget()
        self._axis_panel_home.addWidget(self._axis_panel, stretch=1)


def test_opening_moves_the_panel_in_and_closing_puts_it_back(app) -> None:
    tab = _Tab()
    popup = MatchingPopup(tab)
    popup.open_at(QPoint(100, 100))
    assert popup.isVisible()
    assert tab._axis_panel.parent() is popup
    assert tab._axis_panel_home.indexOf(tab._axis_panel) == -1
    popup.close()
    assert tab._axis_panel.parent() is tab
    assert tab._axis_panel_home.indexOf(tab._axis_panel) == 1  # same place as before


def test_toggle_opens_then_closes(app) -> None:
    tab = _Tab()
    popup = MatchingPopup(tab)
    popup.toggle(QPoint(50, 50))
    assert popup.isVisible()
    popup.toggle(QPoint(50, 50))
    assert not popup.isVisible()
    assert tab._axis_panel.parent() is tab


def test_closing_a_popup_never_opened_leaves_the_panel_alone(app) -> None:
    tab = _Tab()
    MatchingPopup(tab).close()
    assert tab._axis_panel_home.indexOf(tab._axis_panel) == 1
