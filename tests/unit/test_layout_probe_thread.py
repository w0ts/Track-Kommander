"""The layout watch's Traktor capture runs off the GUI thread on macOS.

Every 3 s while snapped, LayoutWatch captured Traktor's window and scanned it
on the GUI thread. The reading now comes back through a queued signal.
"""

from __future__ import annotations

import os
import threading
import time
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6.QtWidgets")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication

import ui.mw_calibration as cal
from ui.main_window import MainWindow


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


class _Relay(QObject):
    read = Signal(object, object)


def _window(relay: _Relay) -> SimpleNamespace:
    relay.read.connect(lambda done, frac: done(frac))
    return SimpleNamespace(_layout_read=relay.read)


def _wait_for(pred, qapp, timeout: float = 2.0) -> None:
    end = time.monotonic() + timeout
    while not pred() and time.monotonic() < end:
        qapp.processEvents()
        time.sleep(0.005)


def test_the_capture_runs_on_a_thread_and_the_reading_lands_on_the_gui(qapp, monkeypatch) -> None:
    monkeypatch.setattr(cal, "_CALIBRATE_OFF_THREAD", True)
    relay = _Relay()
    win = _window(relay)
    probed_on: list[str] = []
    landed: list[tuple[float | None, str]] = []

    def probe():
        probed_on.append(threading.current_thread().name)
        return 0.42

    MainWindow._run_layout_probe(
        win, probe, lambda f: landed.append((f, threading.current_thread().name))
    )
    _wait_for(lambda: landed, qapp)
    assert probed_on == ["layout-probe"]
    assert landed == [(0.42, threading.main_thread().name)]


def test_a_probe_that_raises_still_reports_back(qapp, monkeypatch) -> None:
    """Otherwise LayoutWatch would wait on it forever and stop following."""
    monkeypatch.setattr(cal, "_CALIBRATE_OFF_THREAD", True)
    relay = _Relay()
    win = _window(relay)
    landed: list = []

    def probe():
        raise RuntimeError("capture failed")

    MainWindow._run_layout_probe(win, probe, landed.append)
    _wait_for(lambda: landed, qapp)
    assert landed == [None]


def test_windows_captures_inline(qapp, monkeypatch) -> None:
    """QScreen.grabWindow must stay on the GUI thread."""
    monkeypatch.setattr(cal, "_CALIBRATE_OFF_THREAD", False)
    probed_on: list[str] = []
    landed: list = []
    MainWindow._run_layout_probe(
        SimpleNamespace(),
        lambda: probed_on.append(threading.current_thread().name) or 0.3,
        landed.append,
    )
    assert probed_on == [threading.current_thread().name]
    assert landed == [0.3]
