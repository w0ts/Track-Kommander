"""An error nobody caught still reaches the log, and the user.

There was no hook. An exception in a Qt slot or on a worker thread was
printed to the terminal the app was started from — which the built app does
not have — so the feature just did not happen: nothing in ``log.log``,
nothing on screen, and a bug report with no traceback in it.

And the library was opened before the application object existed, so a
database that would not open ended the process before there was a window to
say so in.
"""

from __future__ import annotations

import logging
import sqlite3
import sys
import threading

import pytest
from PySide6.QtCore import QCoreApplication, QTimer

from ui import unhandled
from ui.unhandled import install_exception_hooks, open_library


@pytest.fixture(scope="module")
def qapp():
    yield QCoreApplication.instance() or QCoreApplication([])


@pytest.fixture
def hooks(monkeypatch):
    """Install the hooks for one test and put the old ones back after."""
    monkeypatch.setattr(sys, "excepthook", sys.excepthook)
    monkeypatch.setattr(threading, "excepthook", threading.excepthook)
    # The default hooks print to stderr; the tests only want what ours add.
    monkeypatch.setattr(sys, "__excepthook__", lambda *a: None)
    told: list[str] = []
    install_exception_hooks(told.append)
    return told


def _raise(exc: BaseException):
    """*exc* as the hook receives it: raised, so it has a traceback."""
    try:
        raise exc
    except type(exc) as caught:
        return type(caught), caught, caught.__traceback__


# ── The hook ────────────────────────────────────────────────────────────────


def test_an_uncaught_error_is_logged_with_its_traceback(hooks, caplog) -> None:
    with caplog.at_level(logging.ERROR):
        sys.excepthook(*_raise(ValueError("the grid is wrong")))

    [record] = [r for r in caplog.records if r.name == "unhandled"]
    assert "the grid is wrong" in caplog.text
    assert record.exc_info is not None, "a log line without the traceback"


def test_the_user_is_told(hooks) -> None:
    sys.excepthook(*_raise(ValueError("the grid is wrong")))

    [text] = hooks
    assert "ValueError" in text and "log" in text.lower()


def test_a_burst_of_errors_is_one_message(hooks, monkeypatch, caplog) -> None:
    """A slot on a timer can fail ten times a second. Every one is logged;
    the user is told once."""
    now = {"t": 100.0}
    monkeypatch.setattr(unhandled, "_now", lambda: now["t"])

    with caplog.at_level(logging.ERROR):
        for _ in range(50):
            sys.excepthook(*_raise(ValueError("again")))
            now["t"] += 0.1
    assert len(hooks) == 1
    assert len([r for r in caplog.records if r.name == "unhandled"]) == 50

    now["t"] += unhandled.TELL_AGAIN_AFTER_S
    sys.excepthook(*_raise(ValueError("again")))
    assert len(hooks) == 2


def test_ctrl_c_is_not_an_error(hooks, caplog) -> None:
    with caplog.at_level(logging.ERROR):
        sys.excepthook(*_raise(KeyboardInterrupt()))

    assert hooks == []
    assert [r for r in caplog.records if r.name == "unhandled"] == []


def test_telling_the_user_cannot_make_it_worse(monkeypatch, caplog) -> None:
    monkeypatch.setattr(sys, "excepthook", sys.excepthook)
    monkeypatch.setattr(threading, "excepthook", threading.excepthook)
    monkeypatch.setattr(sys, "__excepthook__", lambda *a: None)

    def broken(_text: str) -> None:
        raise RuntimeError("the banner is gone")

    install_exception_hooks(broken)
    with caplog.at_level(logging.ERROR):
        sys.excepthook(*_raise(ValueError("the grid is wrong")))

    assert "the grid is wrong" in caplog.text


# ── Where errors actually come from ─────────────────────────────────────────


def test_an_error_on_a_thread_is_caught(hooks, caplog) -> None:
    def work() -> None:
        raise ValueError("the scan fell over")

    with caplog.at_level(logging.ERROR):
        thread = threading.Thread(target=work, name="scan")
        thread.start()
        thread.join()

    assert "the scan fell over" in caplog.text
    assert len(hooks) == 1


def test_an_error_in_a_qt_slot_is_caught(qapp, hooks, caplog) -> None:
    later: list[str] = []

    def carry_on() -> None:
        later.append("still running")
        qapp.quit()

    def slot() -> None:
        # What comes after the error is asked for here, so that it cannot
        # come before it. Three timers set side by side do not fire in the
        # order they are due once the loop has been held up: this is the
        # first exec() of a whole run, it starts with every widget the
        # earlier tests left to delete — 1362 of them on Windows, 420 ms —
        # and after that the 60 ms timer fired ahead of the 20 ms one.
        QTimer.singleShot(0, carry_on)
        raise ValueError("the slot fell over")

    # A loop that did stop must not hold the run up for ever.
    give_up = QTimer()
    give_up.setSingleShot(True)
    give_up.timeout.connect(qapp.quit)

    # Under exec(), as the application runs: that is where PySide6 hands a
    # slot's exception to the hook and carries on with the event loop.
    with caplog.at_level(logging.ERROR):
        QTimer.singleShot(0, slot)
        give_up.start(10_000)
        qapp.exec()
    give_up.stop()

    assert "the slot fell over" in caplog.text
    assert len(hooks) == 1
    assert later == ["still running"]


# ── Opening the library ─────────────────────────────────────────────────────


def test_a_library_that_opens_says_nothing() -> None:
    told: list[tuple[str, str]] = []

    assert open_library(lambda: None, lambda *a: told.append(a)) is True
    assert told == []


@pytest.mark.parametrize(
    "error",
    [
        sqlite3.OperationalError("database is locked"),
        sqlite3.DatabaseError("file is not a database"),
        OSError(30, "Read-only file system"),
    ],
)
def test_a_library_that_will_not_open_is_explained(error, caplog) -> None:
    told: list[tuple[str, str]] = []

    def init() -> None:
        raise error

    with caplog.at_level(logging.ERROR):
        assert open_library(init, lambda *a: told.append(a)) is False

    [(title, text)] = told
    assert "library" in title.lower()
    assert str(error) in text
    assert str(error) in caplog.text
