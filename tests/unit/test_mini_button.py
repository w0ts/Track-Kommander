"""Minimised to one small floating button that uncovers Traktor's library."""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6.QtWidgets")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QPointF, QSettings, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

import core.drag
import core.traktor
from ui.main_window import MainWindow
from ui.mini_button import SIZE, MiniButton


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def button(qapp, tmp_path):
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path))
    QCoreApplication.setApplicationName("tk-mini-button-test")
    b = MiniButton()
    heard: list[bool] = []
    b.restore_requested.connect(lambda: heard.append(True))
    yield b, heard
    b.deleteLater()


def _mouse(kind, at: QPoint) -> QMouseEvent:
    p = QPointF(at)
    return QMouseEvent(
        kind,
        QPointF(5, 5),
        p,
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )


def test_it_is_small_and_stays_visible_when_traktor_is_active(button) -> None:
    b, _heard = button
    assert b.width() == b.height() == SIZE
    assert b.testAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow)
    assert b.windowFlags() & Qt.WindowType.WindowStaysOnTopHint


def test_a_click_restores_the_window(button) -> None:
    b, heard = button
    b.mousePressEvent(_mouse(QEvent.Type.MouseButtonPress, QPoint(100, 100)))
    b.mouseReleaseEvent(_mouse(QEvent.Type.MouseButtonRelease, QPoint(101, 100)))
    assert heard == [True]


def test_a_drag_moves_it_and_is_remembered(button) -> None:
    b, heard = button
    b.show_at(QPoint(50, 50))
    b.mousePressEvent(_mouse(QEvent.Type.MouseButtonPress, QPoint(100, 100)))
    b.mouseMoveEvent(_mouse(QEvent.Type.MouseMove, QPoint(160, 130)))
    b.mouseReleaseEvent(_mouse(QEvent.Type.MouseButtonRelease, QPoint(160, 130)))
    assert heard == [], "a drag is not a click"
    assert b.pos() == QPoint(110, 80)
    assert QSettings().value("mini_button_pos") == QPoint(110, 80)


def test_it_reappears_where_it_was_left(button) -> None:
    b, _heard = button
    QSettings().setValue("mini_button_pos", QPoint(300, 200))
    b.show_at(QPoint(10, 10))
    assert b.pos() == QPoint(300, 200)


def test_it_shows_the_tk_logo_without_being_handed_an_icon(button) -> None:
    """The compiled app leaves the window icon empty — a black square."""
    b, _heard = button
    assert not b._icon.isNull()


def test_it_is_big_enough_to_hit() -> None:
    assert SIZE >= 64


# ── Only while Traktor can be seen ──────────────────────────────────────────


class _Button:
    def __init__(self, visible: bool) -> None:
        self.visible = visible

    def isVisible(self) -> bool:
        return self.visible

    def show(self) -> None:
        self.visible = True

    def hide(self) -> None:
        self.visible = False

    def raise_(self) -> None:
        pass


@pytest.mark.parametrize(
    "on_screen, front, shown",
    [
        (True, True, True),  # Traktor up, it or we in front
        (True, False, False),  # another app in front (an editor over Traktor)
        (False, True, False),  # Traktor hidden, minimised or quit
    ],
)
def test_the_button_follows_traktor(qapp, monkeypatch, on_screen, front, shown) -> None:
    monkeypatch.setattr(core.drag, "is_traktor_visible", lambda: on_screen)
    monkeypatch.setattr(core.drag, "front_app_is_traktor_or_us", lambda: front)
    for start in (True, False):
        win = SimpleNamespace(_mini_button=_Button(start), _mini_follows_traktor=True)
        MainWindow._update_mini_visibility(win)
        assert win._mini_button.visible is shown


@pytest.mark.parametrize(("on_screen", "front"), [(False, False), (True, False), (False, True)])
def test_minimised_with_traktor_closed_the_button_always_shows(
    qapp, monkeypatch, on_screen, front
) -> None:
    """It is the only way back to the window (nothing was left, 2026-10-05)."""
    monkeypatch.setattr(core.drag, "is_traktor_visible", lambda: on_screen)
    monkeypatch.setattr(core.drag, "front_app_is_traktor_or_us", lambda: front)
    win = SimpleNamespace(_mini_button=_Button(False), _mini_follows_traktor=False)
    MainWindow._update_mini_visibility(win)
    assert win._mini_button.visible


# ── Restoring brings Traktor back too ───────────────────────────────────────


def _restore(monkeypatch, *, traktor_running: bool):
    calls: list[str] = []
    monkeypatch.setattr(
        core.traktor, "bring_traktor_forward", lambda: calls.append("traktor") or traktor_running
    )
    monkeypatch.setattr(
        "PySide6.QtCore.QTimer.singleShot", lambda ms, fn: calls.append(f"later:{fn.__name__}")
    )
    win = SimpleNamespace(
        _mini_button=None,
        _collapsed_docked=False,
        _collapsed_geometry=None,
        setGeometry=lambda g: None,
        show=lambda: calls.append("show"),
        _force_raise=lambda: calls.append("raise"),
    )
    win._force_raise.__name__ = "_force_raise"
    MainWindow._expand_from_button(win)
    return calls


def test_restoring_brings_traktor_up_behind_us(qapp, monkeypatch) -> None:
    calls = _restore(monkeypatch, traktor_running=True)
    assert calls.index("traktor") < calls.index("later:_force_raise")
    assert "raise" not in calls, "raised after Traktor has settled, not before"


def test_without_traktor_we_just_come_forward(qapp, monkeypatch) -> None:
    assert _restore(monkeypatch, traktor_running=False)[-1] == "raise"
