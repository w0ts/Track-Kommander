"""Docked mode: the window sits where the library snap put it.

Docked, the window stays above other windows and the cover art gets a 2 px
frame. Moving or resizing it off the snapped spot undocks it.
"""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6.QtWidgets")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QRect, Qt, QTimer
from PySide6.QtGui import QColor, QCursor
from PySide6.QtWidgets import QApplication

import core.drag
import core.traktor
import ui.window_level
from ui.main_window import MainWindow
from ui.now_playing import _DragHandle

FRAME = QColor(_DragHandle.DOCKED_FRAME_COLOR).name()


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


# ── The frame ───────────────────────────────────────────────────────────────


def _corner(handle) -> QColor:
    return handle.grab().toImage().pixelColor(0, 0)


def test_the_cover_is_framed_only_while_docked(qapp) -> None:
    handle = _DragHandle()
    handle.set_art(None)
    assert _corner(handle).name() != FRAME

    handle.set_framed(True)
    img = handle.grab().toImage()
    edge = handle.DOCKED_FRAME_PX
    for x, y in [(0, 0), (edge - 1, 60), (60, img.height() - 1), (img.width() - 1, 60)]:
        assert img.pixelColor(x, y).name() == FRAME, (x, y)

    handle.set_framed(False)
    assert _corner(handle).name() != FRAME


def test_marking_a_snap_is_announced(qapp) -> None:
    handle = _DragHandle()
    heard = []
    handle.snap_changed.connect(lambda: heard.append(handle._snapped_geom))
    handle.mark_snapped()
    assert heard and heard[0] is not None


# ── The window ──────────────────────────────────────────────────────────────


def _window(
    monkeypatch,
    snapped: list[bool],
    visible: list[bool] | None = None,
    front: list[bool] | None = None,
):
    """A window stand-in; returns it, the on-top calls and the frame calls.

    *visible*: Traktor has a window on screen. *front*: the active app is
    Traktor or this one.
    """
    visible = visible if visible is not None else [True]
    front = front if front is not None else [True]
    levels, frames = [], []
    monkeypatch.setattr(
        ui.window_level, "set_always_on_top", lambda w, on: levels.append(on) or True
    )
    monkeypatch.setattr(core.drag, "is_traktor_visible", lambda: visible[0])
    # The on-top tick reads Traktor's bounds once and takes "on screen" from
    # them (the same window lookup), so the bounds follow *visible* too.
    monkeypatch.setattr(
        core.drag,
        "_traktor_logical_bounds",
        lambda: (0.0, 30.0, 1600.0, 1000.0) if visible[0] else None,
    )
    monkeypatch.setattr(core.drag, "front_app", lambda: "traktor" if front[0] else "other")
    now_tab = SimpleNamespace(is_snapped=lambda: snapped[0], set_docked_frame=frames.append)
    win = SimpleNamespace(now_tab=now_tab, _docked=False, _on_top=False, _last_front="traktor")
    win._update_on_top = lambda: MainWindow._update_on_top(win)
    win._keep_on_top = lambda: None
    return win, levels, frames


def test_docked_with_traktor_visible_is_on_top_and_framed(qapp, monkeypatch) -> None:
    win, levels, frames = _window(monkeypatch, [True])
    MainWindow._update_docked(win)
    assert win._docked and win._on_top
    assert levels == [True] and frames == [True]


def test_docked_with_traktor_hidden_is_framed_but_not_on_top(qapp, monkeypatch) -> None:
    win, levels, frames = _window(monkeypatch, [True], visible=[False])
    MainWindow._update_docked(win)
    assert win._docked and not win._on_top
    assert levels == [] and frames == [True]


def test_traktor_hiding_drops_it_and_coming_back_restores_it(qapp, monkeypatch) -> None:
    visible = [True]
    win, levels, _frames = _window(monkeypatch, [True], visible)
    MainWindow._update_docked(win)
    visible[0] = False  # Cmd+H, minimised or quit
    MainWindow._update_on_top(win)
    visible[0] = True
    MainWindow._update_on_top(win)
    assert levels == [True, False, True]


def test_another_app_in_front_is_not_covered(qapp, monkeypatch) -> None:
    """An editor over Traktor: Traktor is on screen but cannot be seen, and
    floating over the editor would just be in the way."""
    front = [True]
    win, levels, _frames = _window(monkeypatch, [True], front=front)
    MainWindow._update_docked(win)
    front[0] = False  # the user clicks into the editor
    MainWindow._update_on_top(win)
    assert not win._on_top
    front[0] = True  # back to Traktor (or to us)
    MainWindow._update_on_top(win)
    assert levels == [True, False, True]


def test_moving_off_the_snap_undocks(qapp, monkeypatch) -> None:
    snapped = [True]
    win, levels, frames = _window(monkeypatch, snapped)
    MainWindow._update_docked(win)
    snapped[0] = False
    MainWindow._update_docked(win)
    assert not win._docked and not win._on_top
    assert levels == [True, False] and frames == [True, False]


def test_undocked_is_never_on_top(qapp, monkeypatch) -> None:
    win, levels, _frames = _window(monkeypatch, [False])
    MainWindow._update_docked(win)
    MainWindow._update_on_top(win)
    assert levels == []


def test_repeated_checks_do_nothing(qapp, monkeypatch) -> None:
    """Every move event and every poll lands here; the native call only on a change."""
    win, levels, _frames = _window(monkeypatch, [True])
    for _ in range(5):
        MainWindow._update_docked(win)
        MainWindow._update_on_top(win)
    assert levels == [True]


def test_a_move_before_the_tabs_exist_is_ignored(qapp) -> None:
    MainWindow._update_docked(SimpleNamespace())


# ── Focus follows the pointer (macOS) ───────────────────────────────────────


def _tick(monkeypatch, *, pointer, front, buttons=False, traktor=(0, 0, 1000, 800)):
    """Run one hover tick; returns what was activated."""
    activated: list[str] = []
    monkeypatch.setattr(QCursor, "pos", staticmethod(lambda: QPoint(*pointer)))
    monkeypatch.setattr(core.drag, "front_app", lambda: front)
    monkeypatch.setattr(core.drag, "mouse_buttons_down", lambda: buttons)
    monkeypatch.setattr(
        core.traktor, "bring_traktor_forward", lambda: activated.append("traktor") or True
    )
    win = SimpleNamespace(
        _traktor_rect=traktor,
        frameGeometry=lambda: QRect(0, 500, 1000, 300),  # docked over the library
        _force_raise=lambda: activated.append("us"),
        isVisible=lambda: True,
    )
    MainWindow._hover_focus_tick(win)
    return activated


def test_hover_onto_traktor_decks_activates_traktor(qapp, monkeypatch) -> None:
    assert _tick(monkeypatch, pointer=(400, 200), front="us") == ["traktor"]


def test_hover_back_onto_us_activates_us(qapp, monkeypatch) -> None:
    assert _tick(monkeypatch, pointer=(400, 650), front="traktor") == ["us"]


def test_hover_with_a_button_held_switches_nothing(qapp, monkeypatch) -> None:
    assert _tick(monkeypatch, pointer=(400, 200), front="us", buttons=True) == []


# ── Back from another app ───────────────────────────────────────────────────


def _activate(monkeypatch, *, docked: bool, last_front: str):
    scheduled: list = []
    monkeypatch.setattr(QTimer, "singleShot", lambda ms, fn: scheduled.append(fn))
    win = SimpleNamespace(_docked=docked, _last_front=last_front, _restore_traktor_and_raise="pair")
    MainWindow._on_app_state(win, Qt.ApplicationState.ApplicationActive)
    return win, scheduled


def test_back_from_an_editor_brings_traktor_up_behind(qapp, monkeypatch) -> None:
    win, scheduled = _activate(monkeypatch, docked=True, last_front="other")
    assert scheduled == ["pair"]
    assert win._last_front == "traktor", "the activation pairing causes must not pair again"


def test_back_from_traktor_needs_nothing(qapp, monkeypatch) -> None:
    """Traktor is right behind already; pairing would pull it forward every
    time the pointer crosses back onto this window."""
    _win, scheduled = _activate(monkeypatch, docked=True, last_front="traktor")
    assert scheduled == []


def test_undocked_is_left_alone(qapp, monkeypatch) -> None:
    _win, scheduled = _activate(monkeypatch, docked=False, last_front="other")
    assert scheduled == []


def test_the_on_top_check_remembers_the_last_other_app(qapp, monkeypatch) -> None:
    front = [False]
    win, _levels, _frames = _window(monkeypatch, [True], front=front)
    MainWindow._update_on_top(win)
    assert win._last_front == "other"
    monkeypatch.setattr(core.drag, "front_app", lambda: "us")
    MainWindow._update_on_top(win)
    assert win._last_front == "other", "our own activation is not recorded"


# ── The real level, not just the flag ───────────────────────────────────────


def test_a_lost_level_is_reapplied(qapp, monkeypatch) -> None:
    """Restoring from the minimised button left the window at layer 0 while
    the flag said on top; it went under Traktor."""
    applied: list[bool] = []
    monkeypatch.setattr(ui.window_level, "is_always_on_top", lambda w: False)
    monkeypatch.setattr(
        ui.window_level, "set_always_on_top", lambda w, on: applied.append(on) or True
    )
    win = SimpleNamespace(isVisible=lambda: True)
    MainWindow._keep_on_top(win)
    assert applied == [True]


def test_a_kept_level_is_left_alone(qapp, monkeypatch) -> None:
    applied: list[bool] = []
    monkeypatch.setattr(ui.window_level, "is_always_on_top", lambda w: True)
    monkeypatch.setattr(
        ui.window_level, "set_always_on_top", lambda w, on: applied.append(on) or True
    )
    MainWindow._keep_on_top(SimpleNamespace(isVisible=lambda: True))
    assert applied == []


def test_the_periodic_check_repairs_the_level(qapp, monkeypatch) -> None:
    win, _levels, _frames = _window(monkeypatch, [True])
    MainWindow._update_docked(win)
    kept: list[bool] = []
    win._keep_on_top = lambda: kept.append(True)
    MainWindow._update_on_top(win)
    assert kept == [True]


# ── Starting with Traktor already running ───────────────────────────────────


@pytest.mark.parametrize("running, expected", [(True, ["pair"]), (False, [])])
def test_startup_brings_a_running_traktor_up_behind(qapp, monkeypatch, running, expected) -> None:
    scheduled: list = []
    monkeypatch.setattr(core.traktor, "is_traktor_running", lambda: running)
    monkeypatch.setattr(QTimer, "singleShot", lambda ms, fn: scheduled.append(fn))
    win = SimpleNamespace(_restore_traktor_and_raise="pair")
    MainWindow._bring_up_running_traktor(win)
    assert scheduled == expected
