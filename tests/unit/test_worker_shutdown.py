"""Letting go of a worker thread, and quitting while one is busy.

Qt ends the process — "QThread: Destroyed while thread is still running" —
when a thread object is destroyed before its thread has finished. There were
two ways to get there:

* the export's own "done" signal dropped the last reference to the worker
  while the worker was still returning from ``run()``;
* quitting waited three seconds for a worker and then closed anyway, and a
  file being copied to a slow stick takes longer than that.
"""

from __future__ import annotations

import threading
import time

import pytest
from PySide6.QtCore import QCoreApplication, QThread, Signal

from ui import worker_shutdown
from ui.worker_shutdown import (
    GIVE_UP_AFTER_S,
    keep_until_finished,
    may_close,
    stop_and_wait,
)


@pytest.fixture(scope="module")
def qapp():
    yield QCoreApplication.instance() or QCoreApplication([])


class _Worker(QThread):
    """Says it is done, then takes a little longer to actually finish —
    which is the window the export worker used to be destroyed in."""

    done = Signal()

    def __init__(self, linger_s: float = 0.1) -> None:
        super().__init__()
        self._linger_s = linger_s
        self._release = threading.Event()
        self.stopped = False

    def stop(self) -> None:
        self.stopped = True
        self._release.set()

    def run(self) -> None:
        self.done.emit()
        self._release.wait(self._linger_s)


def _until(condition, qapp, timeout_s: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        qapp.processEvents()
        if condition():
            return True
        time.sleep(0.01)
    return False


# ── Letting go ──────────────────────────────────────────────────────────────


def test_a_running_worker_is_kept_until_it_has_finished(qapp) -> None:
    worker = _Worker(linger_s=0.2)
    worker.start()
    assert _until(worker.isRunning, qapp)

    keep_until_finished(worker)

    assert worker in worker_shutdown.kept()
    assert _until(lambda: worker not in worker_shutdown.kept(), qapp)
    assert worker.isFinished()


def test_a_worker_that_has_finished_is_not_kept(qapp) -> None:
    worker = _Worker(linger_s=0.0)
    worker.start()
    assert worker.wait(2000)

    keep_until_finished(worker)

    assert worker not in worker_shutdown.kept()


def test_no_worker_is_fine() -> None:
    keep_until_finished(None)


# ── Stopping for a quit ─────────────────────────────────────────────────────


def test_a_worker_that_stops_in_time_is_not_in_the_way(qapp) -> None:
    worker = _Worker(linger_s=5.0)
    worker.start()
    assert _until(worker.isRunning, qapp)

    assert stop_and_wait([worker, None], wait_ms=2000) == []
    assert worker.stopped and worker.isFinished()


def test_a_worker_that_does_not_is_reported(qapp) -> None:
    class _Busy(_Worker):
        def stop(self) -> None:  # mid-file: cannot stop yet
            self.stopped = True

    worker = _Busy(linger_s=0.3)
    worker.start()
    assert _until(worker.isRunning, qapp)

    assert stop_and_wait([worker], wait_ms=10) == [worker]

    assert worker.wait(3000)


def test_a_worker_that_is_not_running_is_left_alone() -> None:
    worker = _Worker()
    assert stop_and_wait([worker], wait_ms=10) == []
    assert not worker.stopped


# ── Whether to close ────────────────────────────────────────────────────────


def test_nothing_in_the_way_means_close() -> None:
    assert may_close([], waiting_since=None, now=100.0)


def test_a_busy_worker_means_wait() -> None:
    assert not may_close([object()], waiting_since=None, now=100.0)
    assert not may_close([object()], waiting_since=100.0, now=100.0 + 10)


def test_waiting_has_an_end() -> None:
    """A worker that never finishes must not make the app impossible to
    quit."""
    assert may_close([object()], waiting_since=100.0, now=100.0 + GIVE_UP_AFTER_S + 1)
