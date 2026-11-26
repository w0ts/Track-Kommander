"""The main-thread watchdog: finds stalls of the Qt event loop and logs the
GUI thread's stack; off unless Settings (or TK_WATCHDOG) turns it on."""

from __future__ import annotations

import logging
import time

import pytest

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtWidgets import QApplication

from ui import ui_watchdog as wd


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


def _pump(app, seconds: float) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.005)


def _busy_main_thread_for(seconds: float) -> None:
    time.sleep(seconds)


def test_env_overrides_the_setting() -> None:
    assert wd.watchdog_wanted({}, False) is False
    assert wd.watchdog_wanted({}, True) is True
    assert wd.watchdog_wanted({"TK_WATCHDOG": "1"}, False) is True
    assert wd.watchdog_wanted({"TK_WATCHDOG": "0"}, True) is False


def test_config_defaults_and_env(tmp_path) -> None:
    cfg = wd.WatchdogConfig.from_env({"TK_WATCHDOG_STALL_MS": "300"}, str(tmp_path))
    assert cfg.stall_ms == 300
    assert cfg.heartbeat_ms == 50
    assert cfg.native_sample is False  # opt-in only


def test_a_stall_logs_the_blocking_frame(qapp, caplog) -> None:
    cfg = wd.WatchdogConfig(heartbeat_ms=10, stall_ms=80, poll_s=0.01, resample_ms=1000)
    dog = wd.MainThreadWatchdog(qapp, cfg)
    dog.start()
    try:
        with caplog.at_level(logging.WARNING, logger="lag"):
            _pump(qapp, 0.1)
            _busy_main_thread_for(0.3)
            _pump(qapp, 0.15)
    finally:
        dog.stop()
        dog.join(1)
    text = caplog.text
    assert "main thread stalled" in text
    assert "_busy_main_thread_for" in text
    assert "UI recovered after" in text
    assert dog.stalls == 1
    assert dog.worst_ms >= 200


def test_no_stall_no_log(qapp, caplog) -> None:
    cfg = wd.WatchdogConfig(heartbeat_ms=10, stall_ms=150, poll_s=0.01)
    dog = wd.MainThreadWatchdog(qapp, cfg)
    dog.start()
    try:
        with caplog.at_level(logging.WARNING, logger="lag"):
            _pump(qapp, 0.2)
    finally:
        dog.stop()
        dog.join(1)
    assert "stalled" not in caplog.text
    assert dog.stalls == 0


def test_set_ui_watchdog_starts_and_stops(qapp, monkeypatch) -> None:
    monkeypatch.setattr(wd, "_INSTALLED", None)
    dog = wd.set_ui_watchdog(True)
    assert dog is not None and wd.running_watchdog() is dog
    assert wd.set_ui_watchdog(True) is dog  # idempotent
    assert wd.set_ui_watchdog(False) is None
    dog.join(1)
    assert wd.running_watchdog() is None


def test_the_log_bundle_is_told_about_the_watchdog(qapp, monkeypatch) -> None:
    """core cannot import the watchdog, so it reports itself to the bundle."""
    assert ("UI lag logging", wd.watchdog_state) in wd.log_bundle._extra_probes
    monkeypatch.setattr(wd, "_INSTALLED", None)
    assert wd.watchdog_state() == "off"
    dog = wd.set_ui_watchdog(True)
    try:
        assert wd.watchdog_state().startswith("on, 0 stall(s), worst ")
    finally:
        wd.set_ui_watchdog(False)
        dog.join(1)
