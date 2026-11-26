"""The library checker dispatches one drag per slot, however the ticks fall.

Dispatching used to spin the event loop (processEvents, a 50 ms sleep,
processEvents) while the slot still read "waiting_drag" and no drag was
marked busy, so a pump tick due in that window re-entered and dragged the
same track a second time.
"""

from __future__ import annotations

import logging
import os

import pytest

pytest.importorskip("PySide6.QtWidgets")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, Signal
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

import ui.library_checker as lc
from core.drag import DragOutcome


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


class _Watcher(QObject):
    deck_loaded = Signal(str, dict)
    deck_position = Signal(str, float)
    deck_state = Signal(str, bool)

    def __init__(self) -> None:
        super().__init__()
        self.stops: list[str] = []
        self.fail_stop = False

    def queue_stop(self, deck: str) -> None:
        if self.fail_stop:
            raise RuntimeError("bridge gone")
        self.stops.append(deck)

    def queue_mute(self, deck: str) -> None:
        pass

    def queue_play(self, deck: str) -> None:
        pass


@pytest.fixture
def dialog(qapp, monkeypatch):
    drags: list[str] = []
    looks: list[int] = []
    monkeypatch.setattr(
        lc, "drag_to_deck", lambda src, deck: drags.append(deck) or DragOutcome(True)
    )
    monkeypatch.setattr(lc, "row_screen_center", lambda table, row: (10.0, 10.0))
    monkeypatch.setattr(lc, "is_traktor_visible", lambda: looks.append(1) or True)
    watcher = _Watcher()
    dlg = lc.LibraryCheckerDialog(watcher, ["/music/a.mp3"])
    dlg._main_queue.append(lc.CheckItem("/music/a.mp3"))
    dlg._running = True
    yield dlg, drags, looks, watcher
    dlg._running = False
    dlg.deleteLater()


def test_a_tick_during_dispatch_does_not_drag_again(dialog) -> None:
    dlg, drags, _looks, _w = dialog
    dlg._pump()  # claims the track and starts the dispatch
    slot = dlg._slots["A"]
    assert slot.state == "dispatching"
    dlg._pump()  # a tick that lands mid-dispatch
    dlg._pump()
    QTest.qWait(150)  # the window-server settle, then the drag
    assert drags == ["A"]
    assert slot.state == "loading"


def test_the_visibility_check_is_throttled(dialog) -> None:
    dlg, _drags, looks, _w = dialog
    for _ in range(5):
        dlg._pump()
    assert len(looks) == 1


def test_a_stopped_run_abandons_a_pending_dispatch(dialog) -> None:
    dlg, drags, _looks, _w = dialog
    dlg._pump()
    dlg._stop()
    QTest.qWait(150)
    assert drags == []
    assert not dlg._drag_busy


def test_a_failed_stop_is_logged(dialog, caplog) -> None:
    dlg, _drags, _looks, watcher = dialog
    watcher.fail_stop = True
    with caplog.at_level(logging.WARNING, logger="ui.library_checker"):
        dlg._stop()
    assert any(r.exc_info for r in caplog.records)
