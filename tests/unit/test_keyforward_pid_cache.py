"""Finding Traktor walks every running app; it must not happen per keystroke.

Every forwarded key is a press and a release, and each looked Traktor up
afresh through NSWorkspace. The pid is kept while that process is alive, and
looked up again once it is gone or a post fails.
"""

from __future__ import annotations

import os

import pytest

from core import keyforward as kf


class _App:
    def __init__(self) -> None:
        self.terminated = False

    def isTerminated(self) -> bool:
        return self.terminated


@pytest.fixture
def finds(monkeypatch):
    """Pretend to be macOS with a Traktor running as this very process."""
    monkeypatch.setattr(kf, "_IS_MAC", True)
    monkeypatch.setattr(kf, "_IS_WIN", False)
    monkeypatch.setattr(kf, "_traktor_app", None)
    monkeypatch.setattr(kf, "_no_traktor_until", 0.0)
    app = _App()
    calls: list[int] = []

    def find():
        calls.append(1)
        return (os.getpid(), app) if app is not None else None

    monkeypatch.setattr(kf, "_find_traktor_app", find)
    return calls, app


def test_a_running_traktor_is_looked_up_once(finds) -> None:
    calls, _app = finds
    assert kf.traktor_pid() == os.getpid()
    assert kf.traktor_pid() == os.getpid()
    assert kf.traktor_pid() == os.getpid()
    assert len(calls) == 1


def test_the_liveness_probe_never_signals_on_windows(finds, monkeypatch) -> None:
    """On Windows ``os.kill``'s "signal" 0 is CTRL_C_EVENT, sent to the
    target's console group — this process's own, here. On the GitHub runner
    it interrupted the whole suite 1150 tests in, twice."""
    monkeypatch.setattr(kf.sys, "platform", "win32")

    def kill(pid: int, sig: int) -> None:
        raise AssertionError(f"os.kill({pid}, {sig}) on Windows sends Ctrl-C")

    monkeypatch.setattr(kf.os, "kill", kill)
    assert kf.traktor_pid() == os.getpid()
    assert kf.traktor_pid() == os.getpid()  # the cached pid is probed here


def test_a_quit_traktor_is_looked_up_again(finds) -> None:
    calls, app = finds
    kf.traktor_pid()
    app.terminated = True
    kf.traktor_pid()
    assert len(calls) == 2


def test_a_failed_post_forgets_the_pid(finds, monkeypatch) -> None:
    calls, _app = finds
    monkeypatch.setattr(kf, "_post", lambda *a: False)
    assert kf.post_key(13, down=True) is False
    kf.traktor_pid()
    assert len(calls) == 2


def test_no_traktor_is_not_searched_for_on_every_key(monkeypatch) -> None:
    monkeypatch.setattr(kf, "_IS_MAC", True)
    monkeypatch.setattr(kf, "_traktor_app", None)
    monkeypatch.setattr(kf, "_no_traktor_until", 0.0)
    calls: list[int] = []
    monkeypatch.setattr(kf, "_find_traktor_app", lambda: calls.append(1) or None)
    assert kf.traktor_pid() is None
    assert kf.traktor_pid() is None
    assert len(calls) == 1


def test_off_macos_there_is_no_pid(monkeypatch) -> None:
    monkeypatch.setattr(kf, "_IS_MAC", False)
    assert kf.traktor_pid() is None
