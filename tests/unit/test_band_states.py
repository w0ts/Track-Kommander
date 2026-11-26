"""The add-on's band icons follow its state, pushed only on a change."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from workers.band_states import BandStates

_app = QApplication.instance() or QApplication([])


def test_an_icon_is_pushed_only_when_its_state_changes() -> None:
    pushed: list[tuple[str, bool]] = []
    states = BandStates(lambda key, busy: pushed.append((key, busy)) or True, read=dict)
    states.apply({"X1": True, "X2": False})
    states.apply({"X1": True, "X2": False})  # nothing new
    states.apply({"X1": False, "X2": True})
    assert pushed == [
        ("X1", True),
        ("X2", False),
        ("X1", False),
        ("X2", True),
    ]


def test_a_push_libtk_refused_is_tried_again() -> None:
    answers = iter([False, True])
    pushed: list[str] = []
    states = BandStates(lambda key, busy: pushed.append(key) or next(answers), read=dict)
    states.apply({"X1": True})
    states.apply({"X1": True})
    assert pushed == ["X1", "X1"]


def test_the_drawn_icons_fill_the_band_size() -> None:
    from ui.native_icons import ICON_H, ICON_W, OFF_ARGB, icon_pixels

    for key in ("L_EXPORT", "L_HOTCUES", "L_SPOTIFY", "X2"):
        pixels = icon_pixels(key, OFF_ARGB)
        assert pixels.shape == (ICON_W * ICON_H,)
        assert (pixels >> 24).max() == 255  # real ink, in the grey asked for
        assert {int(p) & 0xFFFFFF for p in pixels} == {OFF_ARGB & 0xFFFFFF}


def test_tks_own_jobs_turn_their_icons_blue_while_they_run() -> None:
    """Hotcues, Tagging and Export (User, 2026-11-24): read on the main thread, no add-on."""
    from workers.band_states import worker_running

    class Worker:
        def __init__(self, running: bool) -> None:
            self.running = running

        def isRunning(self) -> bool:  # stand-in for QThread.isRunning
            return self.running

    hc = Worker(True)
    pushed: list[tuple[str, bool]] = []
    states = BandStates(
        lambda key, busy: pushed.append((key, busy)) or True,
        read=None,
        local=lambda: {"L_HOTCUES": worker_running(hc), "L_EXPORT": worker_running(None)},
    )
    states.poll_local()
    hc.running = False
    states.poll_local()
    assert pushed == [("L_HOTCUES", True), ("L_EXPORT", False), ("L_HOTCUES", False)]
    states.poll()  # no slow states to read: nothing started
