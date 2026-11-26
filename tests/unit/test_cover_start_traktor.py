"""Double-clicking the cover art starts Traktor when it is not running.

The double-click snaps the window over Traktor's library. With Traktor
closed there is nothing to snap to, so it starts Traktor instead and snaps
once Traktor's window appears.
"""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6.QtWidgets")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPointF, Qt, QTimer
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

import core.drag
import core.traktor
from ui.main_window import MainWindow
from ui.now_playing import _DragHandle


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


def _double_click() -> QMouseEvent:
    return QMouseEvent(
        QMouseEvent.Type.MouseButtonDblClick,
        QPointF(5, 5),
        QPointF(5, 5),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )


def _handle(monkeypatch, *, running: bool):
    snaps = []
    monkeypatch.setattr(core.traktor, "is_traktor_running", lambda: running)
    monkeypatch.setattr(
        core.drag, "snap_over_traktor_library", lambda win: snaps.append(win) or False
    )
    handle = _DragHandle()
    asked = []
    handle.start_traktor_requested.connect(lambda: asked.append(True))
    return handle, asked, snaps


# ── The cover ───────────────────────────────────────────────────────────────


def test_with_traktor_closed_it_asks_to_start_it(qapp, monkeypatch) -> None:
    handle, asked, snaps = _handle(monkeypatch, running=False)
    handle.mouseDoubleClickEvent(_double_click())
    assert asked == [True]
    assert snaps == [], "nothing to snap to yet"


def test_with_traktor_running_it_snaps_as_before(qapp, monkeypatch) -> None:
    handle, asked, snaps = _handle(monkeypatch, running=True)
    handle.mouseDoubleClickEvent(_double_click())
    assert asked == []
    assert len(snaps) == 1


# ── The main window's side ──────────────────────────────────────────────────


def _window(monkeypatch, *, launched: bool):
    notices, timers = [], []
    monkeypatch.setattr(core.traktor, "launch_traktor", lambda: launched)
    monkeypatch.setattr(QTimer, "singleShot", lambda ms, fn: timers.append((ms, fn)))
    win = SimpleNamespace(
        _autostarted_traktor=False,
        _initial_snap_budget=10,
        _initial_snap_done=True,
        _initial_snap_attempts=7,
        _start_autostart_raise_watch=lambda: None,
        _try_initial_snap=lambda: None,
        notice_banner=SimpleNamespace(show_transient=lambda text, sev: notices.append(text)),
    )
    win._launch_traktor = lambda: MainWindow._launch_traktor(win)
    return MainWindow, win, notices, timers


def test_a_launch_rearms_the_snap(qapp, monkeypatch) -> None:
    cls, win, notices, timers = _window(monkeypatch, launched=True)
    cls._start_traktor_and_snap(win)
    assert win._autostarted_traktor
    assert win._initial_snap_budget == 40, "cold start gets the long budget"
    assert not win._initial_snap_done and win._initial_snap_attempts == 0
    assert [fn for _ms, fn in timers] == [win._try_initial_snap]
    assert notices == []


def test_no_traktor_to_launch_says_so(qapp, monkeypatch) -> None:
    cls, win, notices, timers = _window(monkeypatch, launched=False)
    cls._start_traktor_and_snap(win)
    assert timers == []
    assert notices and "Traktor" in notices[0]
