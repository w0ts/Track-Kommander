"""Deck positions are re-detected when Traktor changes, not every 15 s.

Traktor exposes no layout event, so drop positions were refreshed by a
screenshot every 15 s — up to 15 s stale after Traktor's window moved. Now a
window move (seen by the docked on-top check) or a browser shift (seen by the
layout watch) triggers one detection once things settle; a 60 s timer is the
fallback.
"""

from __future__ import annotations

import os
import threading
import time
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6.QtWidgets")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

import core.drag
import ui.main_window as mw
import ui.mw_calibration as cal
from ui.main_window import MainWindow
from ui.mw_docking import _moved


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


def _docked_window(monkeypatch, rects: list):
    """A docked window stand-in whose Traktor bounds come from *rects*."""
    scheduled: list[str] = []
    monkeypatch.setattr(core.drag, "_traktor_logical_bounds", lambda: rects.pop(0))
    monkeypatch.setattr(core.drag, "is_traktor_visible", lambda: True)
    monkeypatch.setattr(core.drag, "front_app", lambda: "traktor")
    win = SimpleNamespace(
        _docked=True,
        _on_top=True,
        _last_front="traktor",
        _traktor_rect=None,
        _hover_timer=None,
        _keep_on_top=lambda: None,
        _schedule_recalibrate=scheduled.append,
    )
    return win, scheduled


def test_a_moved_traktor_window_schedules_a_recalibration(qapp, monkeypatch) -> None:
    win, scheduled = _docked_window(monkeypatch, [(0, 33, 1728, 1084), (200, 33, 1728, 1084)])
    MainWindow._update_on_top(win)  # first sighting: nothing to compare
    MainWindow._update_on_top(win)  # moved 200 px
    assert scheduled == ["Traktor window moved"]


def test_a_still_window_schedules_nothing(qapp, monkeypatch) -> None:
    win, scheduled = _docked_window(monkeypatch, [(0, 33, 1728, 1084)] * 3)
    for _ in range(3):
        MainWindow._update_on_top(win)
    assert scheduled == []


def test_a_jitter_is_not_a_move() -> None:
    assert not _moved((0, 33, 1728, 1084), (1, 34, 1728, 1085))
    assert _moved((0, 33, 1728, 1084), (0, 33, 1400, 1084))


def test_a_run_of_moves_ends_in_one_detection(qapp) -> None:
    """Dragging the window is many small moves: the timer restarts on each."""
    timer = QTimer()
    timer.setSingleShot(True)
    timer.setInterval(10_000)
    win = SimpleNamespace(_recalibrate_timer=timer, _recalibrate_reason="")
    for _ in range(5):
        MainWindow._schedule_recalibrate(win, "Traktor window moved")
    assert timer.isActive() and win._recalibrate_reason == "Traktor window moved"
    timer.stop()


def test_a_triggered_detection_skips_the_cooldown(qapp, monkeypatch) -> None:
    runs: list[bool] = []
    monkeypatch.setattr(
        core.drag, "auto_calibrate_decks", lambda: runs.append(True) or {"A": (1.0, 2.0)}
    )
    monkeypatch.setattr(cal, "_CALIBRATE_OFF_THREAD", False)
    win = _calibrating_window(_last_auto_calibrate=time.time())  # just ran
    MainWindow._auto_calibrate_decks(win, "fallback")
    assert runs == [], "the fallback respects the cooldown"
    MainWindow._auto_calibrate_decks(win, "Traktor window moved", force=True)
    assert runs == [True], "a seen change does not wait"


def _calibrating_window(**state) -> SimpleNamespace:
    """A MainWindow stand-in that runs detection inline, as on Windows."""
    win = SimpleNamespace(
        **{"_last_auto_calibrate": 0.0, "_calibrating": False, "_calibrate_pending": None, **state}
    )
    win._calibrated = SimpleNamespace(emit=lambda *a: MainWindow._on_calibrated(win, *a))
    win._calibrate_worker = lambda *a: MainWindow._calibrate_worker(win, *a)
    win._auto_calibrate_decks = lambda *a: MainWindow._auto_calibrate_decks(win, *a)
    return win


def test_detection_runs_off_the_gui_thread_on_macos(qapp, monkeypatch) -> None:
    ran_on: list[str] = []
    done = threading.Event()
    monkeypatch.setattr(
        core.drag,
        "auto_calibrate_decks",
        lambda: ran_on.append(threading.current_thread().name) or {},
    )
    monkeypatch.setattr(cal, "_CALIBRATE_OFF_THREAD", True)
    win = _calibrating_window()
    win._calibrated = SimpleNamespace(emit=lambda *a: done.set())
    MainWindow._auto_calibrate_decks(win, "startup")
    assert done.wait(5)
    assert ran_on == ["deck-calibrate"]


def test_a_request_during_a_run_is_kept_and_runs_after(qapp, monkeypatch) -> None:
    runs: list[str] = []
    monkeypatch.setattr(
        core.drag, "auto_calibrate_decks", lambda: runs.append("run") or {"A": (1.0, 2.0)}
    )
    monkeypatch.setattr(cal, "_CALIBRATE_OFF_THREAD", False)
    win = _calibrating_window(_calibrating=True)  # one is in flight
    MainWindow._auto_calibrate_decks(win, "Traktor window moved", True)
    assert runs == [] and win._calibrate_pending == ("Traktor window moved", True)
    MainWindow._on_calibrated(win, "startup", {}, 0.0)  # the first one lands
    assert runs == ["run"]
    assert win._calibrating is False and win._calibrate_pending is None


def test_a_failed_detection_frees_the_next_run(qapp, monkeypatch) -> None:
    def _boom():
        raise OSError("capture failed")

    monkeypatch.setattr(core.drag, "auto_calibrate_decks", _boom)
    monkeypatch.setattr(cal, "_CALIBRATE_OFF_THREAD", False)
    win = _calibrating_window()
    MainWindow._auto_calibrate_decks(win, "startup")
    assert win._calibrating is False


def test_the_periodic_timer_is_only_a_fallback() -> None:
    assert mw._CALIBRATE_FALLBACK_MS >= 60_000
