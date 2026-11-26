"""Quitting Traktor on the user's behalf — and never harder than politely.

Patching Traktor's QML needs Traktor closed, and the app used to stop dead
and say so, halfway through a two-click job. It now offers to quit Traktor
and start it again afterwards, which puts two obligations in this module:

* the quit is a **request** — Traktor writes its collection on the way out,
  so a force-kill would cost the session's edits;
* a Traktor that will not go is reported, not escalated, because the caller
  must leave its files alone in that case.
"""

from __future__ import annotations

import subprocess

import pytest

from core import traktor

TASKLIST = (
    '"Traktor Pro 4.exe","4242","Console","1","512 K"\n'
    '"Traktor Pro 4.exe","4243","Console","1","8 K"\n'
    '"Track Kommander.exe","99","Console","1","64 K"\n'
)


class Clock:
    """A clock that only moves when something sleeps on it."""

    def __init__(self) -> None:
        self.t = 0.0
        self.slept: list[float] = []

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.t += seconds


def test_waiting_ends_the_moment_traktor_is_gone() -> None:
    clock = Clock()
    calls = iter([True, True, False])
    assert traktor._wait_until_gone(
        30.0, running=lambda: next(calls), sleep=clock.sleep, now=clock.now
    )
    assert len(clock.slept) == 2


def test_waiting_gives_up_rather_than_hanging() -> None:
    clock = Clock()
    assert not traktor._wait_until_gone(2.0, running=lambda: True, sleep=clock.sleep, now=clock.now)
    assert sum(clock.slept) == pytest.approx(2.0)


def test_a_closed_traktor_is_not_asked_to_quit(monkeypatch) -> None:
    """Nothing is sent, and it is not a failure — it is already the goal."""
    monkeypatch.setattr(traktor, "is_traktor_running", lambda: False)
    monkeypatch.setattr(
        traktor, "_ask_traktor_to_quit", lambda: pytest.fail("asked a closed Traktor to quit")
    )
    assert traktor.quit_traktor() is True


def test_a_traktor_that_will_not_go_is_reported_not_forced(monkeypatch) -> None:
    """One request, then the truth. No second, harder attempt."""
    asks = []
    monkeypatch.setattr(traktor, "is_traktor_running", lambda: True)
    monkeypatch.setattr(traktor, "_ask_traktor_to_quit", lambda: asks.append(1) or True)
    monkeypatch.setattr(traktor, "_wait_until_gone", lambda *a, **kw: False)
    assert traktor.quit_traktor(1.0) is False
    assert len(asks) == 1


def test_the_caller_can_own_the_waiting(monkeypatch) -> None:
    """The GUI waits by pumping its event loop, so its sleep must be used —
    otherwise the window stops repainting and the OS calls it hung."""
    pumped: list[float] = []
    running = iter([True, True, False])
    monkeypatch.setattr(traktor, "is_traktor_running", lambda: next(running))
    monkeypatch.setattr(traktor, "_ask_traktor_to_quit", lambda: True)
    assert traktor.quit_traktor(5.0, sleep=pumped.append) is True
    assert pumped  # it waited on *our* sleep


def test_windows_asks_the_window_to_close(monkeypatch) -> None:
    """``taskkill`` without /F is the polite one — /F loses the collection."""
    sent: list[list[str]] = []

    def fake_run(argv, **kwargs):
        sent.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(traktor.sys, "platform", "win32")
    monkeypatch.setattr(traktor, "_traktor_images_windows", lambda: ["Traktor Pro 4.exe"])
    monkeypatch.setattr(subprocess, "run", fake_run)

    assert traktor._ask_traktor_to_quit() is True
    assert sent == [["taskkill", "/IM", "Traktor Pro 4.exe"]]
    assert not any("/F" in arg.upper() for argv in sent for arg in argv)


def test_windows_image_names_are_read_not_guessed(monkeypatch) -> None:
    monkeypatch.setattr(
        subprocess, "run", lambda argv, **kw: subprocess.CompletedProcess(argv, 0, TASKLIST, "")
    )
    assert traktor._traktor_images_windows() == ["Traktor Pro 4.exe"]


def test_no_traktor_process_means_nothing_to_kill(monkeypatch) -> None:
    monkeypatch.setattr(traktor.sys, "platform", "win32")
    monkeypatch.setattr(traktor, "_traktor_images_windows", list)
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: pytest.fail("ran taskkill with no image to kill")
    )
    assert traktor._ask_traktor_to_quit() is False
