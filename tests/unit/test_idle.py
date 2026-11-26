"""Tests for the display wake lock.

The app is driven from hardware, so during a set the mouse can sit still for an
hour and the screensaver covers the results. ``DisplayWakeLock`` holds an OS
power assertion for as long as Traktor is up.

The state machine is tested with the platform call stubbed, so it runs the same
everywhere. One test exercises the real macOS assertion and checks it shows up
in ``pmset -g assertions`` — stubbing every layer would prove only that the
stub works.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from core.idle import DisplayWakeLock


@pytest.fixture
def lock(monkeypatch):
    """A lock whose platform call always succeeds, on any platform."""
    lk = DisplayWakeLock("test reason")
    monkeypatch.setattr(DisplayWakeLock, "_mac_acquire", lambda self: True)
    monkeypatch.setattr(DisplayWakeLock, "_mac_release", lambda self: None)
    monkeypatch.setattr(DisplayWakeLock, "_win_set", staticmethod(lambda flags: True))
    monkeypatch.setattr("core.idle._IS_MAC", True)
    monkeypatch.setattr("core.idle._IS_WIN", False)
    return lk


# ── State machine ───────────────────────────────────────────────────────────


def test_starts_unheld() -> None:
    assert DisplayWakeLock().held is False


def test_acquire_then_release(lock) -> None:
    assert lock.acquire() is True
    assert lock.held is True
    lock.release()
    assert lock.held is False


def test_acquire_is_idempotent(lock, monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(DisplayWakeLock, "_mac_acquire", lambda self: (calls.append(1), True)[1])
    lock.acquire()
    lock.acquire()
    lock.acquire()
    assert len(calls) == 1, "a held lock must not re-assert"


def test_release_when_not_held_is_a_noop(lock, monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(DisplayWakeLock, "_mac_release", lambda self: calls.append(1))
    lock.release()
    assert calls == []


def test_set_held_mirrors_state(lock) -> None:
    lock.set_held(True)
    assert lock.held is True
    lock.set_held(True)
    assert lock.held is True
    lock.set_held(False)
    assert lock.held is False


def test_failed_acquire_leaves_lock_unheld(monkeypatch) -> None:
    monkeypatch.setattr("core.idle._IS_MAC", True)
    monkeypatch.setattr("core.idle._IS_WIN", False)
    monkeypatch.setattr(DisplayWakeLock, "_mac_acquire", lambda self: False)
    lk = DisplayWakeLock()
    assert lk.acquire() is False
    assert lk.held is False


def test_unsupported_platform_is_a_silent_noop(monkeypatch) -> None:
    monkeypatch.setattr("core.idle._IS_MAC", False)
    monkeypatch.setattr("core.idle._IS_WIN", False)
    lk = DisplayWakeLock()
    assert lk.acquire() is False
    assert lk.held is False
    lk.release()  # must not raise


def test_supported_matches_platform() -> None:
    assert DisplayWakeLock.supported() is (sys.platform in ("darwin", "win32"))


# ── The real thing ──────────────────────────────────────────────────────────


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS IOKit assertion")
def test_real_macos_assertion_is_visible_to_pmset() -> None:
    reason = "Kommander wake-lock self test"
    lk = DisplayWakeLock(reason)
    assert lk.acquire() is True
    try:
        out = subprocess.run(["pmset", "-g", "assertions"], capture_output=True, text=True).stdout
        assert reason in out, "the assertion never reached the power manager"
    finally:
        lk.release()
    after = subprocess.run(["pmset", "-g", "assertions"], capture_output=True, text=True).stdout
    assert reason not in after, "the assertion outlived its release"
