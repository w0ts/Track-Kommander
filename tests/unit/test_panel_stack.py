"""Settings, Library and Stats as pages inside the main window.

Separate dialogs kept ending up underneath the docked, always-on-top
window; as pages there is one window and nothing to order.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6.QtWidgets")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel, QPushButton

from ui.panel_stack import PanelStack


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def stack(qapp):
    home = QLabel("tracks")
    s = PanelStack(home)
    heard: list[str] = []
    s.panel_changed.connect(heard.append)
    return s, home, heard


def test_it_starts_on_the_tracks(stack) -> None:
    s, home, _heard = stack
    assert s.currentWidget() is home and s.current_panel == ""


def test_a_panel_replaces_the_tracks(stack) -> None:
    s, home, heard = stack
    settings = QLabel("settings")
    s.show_panel("settings", "Settings", settings)
    assert s.current_panel == "settings"
    assert s.currentWidget() is not home
    assert settings.isVisibleTo(s.currentWidget()) or settings.parent() is s.currentWidget()
    assert heard == ["settings"]


def test_the_page_is_built_once(stack) -> None:
    s, _home, _heard = stack
    widget = QLabel("library")
    s.show_panel("library", "Library", widget)
    first = s.currentWidget()
    s.close_panel()
    s.show_panel("library", "Library", widget)
    assert s.currentWidget() is first and s.count() == 2


def test_opening_the_open_panel_again_goes_home(stack) -> None:
    s, home, heard = stack
    w = QLabel("stats")
    s.toggle_panel("stats", "Stats", w)
    s.toggle_panel("stats", "Stats", w)
    assert s.currentWidget() is home and heard == ["stats", ""]


def test_another_panel_switches_directly(stack) -> None:
    s, _home, _heard = stack
    s.toggle_panel("settings", "Settings", QLabel("a"))
    s.toggle_panel("library", "Library", QLabel("b"))
    assert s.current_panel == "library"


def test_the_back_button_goes_home(stack) -> None:
    s, home, _heard = stack
    s.show_panel("settings", "Settings", QLabel("a"))
    back = next(b for b in s.currentWidget().findChildren(QPushButton) if b.text().endswith("Back"))
    back.click()
    assert s.currentWidget() is home


# ── Maximize while a panel is open; Back restores the size ────────────────


def _button(s, suffix: str) -> QPushButton:
    return next(b for b in s.currentWidget().findChildren(QPushButton) if b.text().endswith(suffix))


@pytest.fixture
def windowed(stack):
    s, home, heard = stack
    s.setGeometry(100, 120, 500, 400)
    s.show()
    yield s, home, heard
    s.hide()


def _geom(w) -> tuple[int, int, int, int]:
    g = w.geometry()
    return g.x(), g.y(), g.width(), g.height()


def test_maximize_fills_the_screen(windowed) -> None:
    s, _home, _heard = windowed
    s.show_panel("stats", "Stats", QLabel("a"))
    _button(s, "Maximize").click()
    assert s.geometry() == s.screen().availableGeometry()


def test_back_restores_the_size_it_had(windowed) -> None:
    s, home, _heard = windowed
    before = _geom(s)
    s.show_panel("stats", "Stats", QLabel("a"))
    _button(s, "Maximize").click()
    _button(s, "Back").click()
    assert s.currentWidget() is home
    assert _geom(s) == before


def test_maximized_there_is_no_restore_button(windowed) -> None:
    """Back is the way to the original size; a second button was clutter."""
    s, _home, _heard = windowed
    s.show_panel("stats", "Stats", QLabel("a"))
    _button(s, "Maximize").click()
    assert not _button(s, "Maximize").isVisible()
    assert not any("Restore" in b.text() for b in s.currentWidget().findChildren(QPushButton))


def test_switching_panels_keeps_it_maximized(windowed) -> None:
    s, _home, _heard = windowed
    before = _geom(s)
    s.show_panel("stats", "Stats", QLabel("a"))
    _button(s, "Maximize").click()
    s.show_panel("library", "Library", QLabel("b"))
    assert s.geometry() == s.screen().availableGeometry()
    assert not _button(s, "Maximize").isVisible()  # on the new page too
    _button(s, "Back").click()
    assert _geom(s) == before
    s.show_panel("stats", "Stats", QLabel("a"))
    assert _button(s, "Maximize").isVisible()


def test_back_without_maximize_leaves_the_size_alone(windowed) -> None:
    s, _home, _heard = windowed
    s.show_panel("stats", "Stats", QLabel("a"))
    s.setGeometry(150, 150, 600, 450)  # the user resized it themselves
    _button(s, "Back").click()
    assert _geom(s) == (150, 150, 600, 450)


def test_a_panel_header_has_the_close_cross_and_moves_the_window(qapp):
    from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QMainWindow

    from ui.panel_stack import _Header

    win = QMainWindow()
    stack = PanelStack(QLabel("home"))
    win.setCentralWidget(stack)
    win.move(100, 100)
    win.show()
    stack.show_panel("settings", "Settings", QLabel("body"))
    header = stack.currentWidget().findChild(_Header)
    assert any(b.toolTip() == "Close" for b in header.findChildren(QPushButton))
    before = win.pos()

    def event(kind, at):
        left = Qt.MouseButton.LeftButton
        return QMouseEvent(
            kind, QPointF(5, 5), QPointF(at), left, left, Qt.KeyboardModifier.NoModifier
        )

    header.mousePressEvent(event(QEvent.Type.MouseButtonPress, QPoint(200, 120)))
    header.mouseMoveEvent(event(QEvent.Type.MouseMove, QPoint(260, 170)))
    assert win.pos() - before == QPoint(60, 50)
    win.close()
