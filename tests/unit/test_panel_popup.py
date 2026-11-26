"""A page opened from Traktor's band borrows its widget and gives it back."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QTabWidget, QVBoxLayout, QWidget

from ui.panel_popup import PanelPopup


def _no_traktor(_window: QWidget) -> bool:
    return False  # as if Traktor's window was not found: stay near the mouse


@pytest.fixture(scope="module")
def app() -> Iterator[QApplication]:
    yield QApplication.instance() or QApplication([])


def test_a_page_in_a_nested_layout_goes_back_to_its_place(app) -> None:
    host = QWidget()
    outer = QVBoxLayout(host)
    outer.addWidget(QLabel("header"))
    inner = QHBoxLayout()
    outer.addLayout(inner)
    inner.addWidget(QLabel("left"))
    page = QWidget()
    inner.addWidget(page, 3)
    popup = PanelPopup("Settings", page, place=_no_traktor)
    popup.open_at(QPoint(200, 200))
    assert page.parentWidget() is popup
    assert inner.indexOf(page) == -1
    popup.close()
    assert inner.indexOf(page) == 1
    assert inner.stretch(1) == 3


def test_a_page_never_shown_in_tk_is_left_free(app) -> None:
    page = QWidget()
    popup = PanelPopup("Library", page, place=_no_traktor)
    popup.toggle(QPoint(10, 10))
    assert popup.isVisible()
    popup.toggle(QPoint(10, 10))
    assert not popup.isVisible()
    assert page.parentWidget() is None


class _Tabbed(QWidget):
    """A page like SettingsTab: a tab widget of scroll_page()-wrapped tabs."""

    def __init__(self) -> None:
        from ui.style import scroll_page

        super().__init__()
        lay = QVBoxLayout(self)
        self._tab_widget = QTabWidget()
        self.pages = [QLabel("general"), QLabel("controls"), QLabel("about")]
        for name, page in zip(("General", "Controls", "About"), self.pages, strict=True):
            self._tab_widget.addTab(scroll_page(page), name)
        self._tab_widget.setCurrentIndex(1)
        lay.addWidget(self._tab_widget)


def test_tabs_are_shown_one_under_another_and_put_back(app) -> None:
    page = _Tabbed()
    popup = PanelPopup("Settings", page, place=_no_traktor)
    popup.open_at(QPoint(100, 100))
    tw = page._tab_widget
    assert tw.count() == 0 and tw.isHidden()
    assert all(p.isVisibleTo(popup) for p in page.pages)
    popup.close()
    assert [tw.tabText(i) for i in range(tw.count())] == ["General", "Controls", "About"]
    assert tw.currentIndex() == 1
    assert tw.widget(0).widget() is page.pages[0]  # back inside its scroll wrapper
