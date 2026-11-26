"""Main-thread (UI) watchdog — finds what makes the window lag.

A QTimer ticks on the Qt main thread every ``TK_WATCHDOG_HEARTBEAT_MS`` and
stamps a monotonic timestamp. A daemon thread polls that timestamp; when the
gap since the last tick passes ``TK_WATCHDOG_STALL_MS`` the event loop is
blocked, so the thread takes the main thread's Python stack with
:func:`sys._current_frames` and logs it on the ``lag`` logger. While the stall
lasts it keeps sampling (identical stacks collapse to one line), so a slow
loop shows where it spends its time, not just where it started.

It only observes. Sampling another thread's frames is what py-spy does, and
it is safe in CPython for diagnostics.

A stall that began while a modal dialog owned the loop (a message box, the
native file panel — which does not service Qt timers at all) is tagged as
such: the user was reading a dialog, not waiting on us.

Ported from Cath-Axis ``infrastructure/ui_watchdog.py``, without its
dock-layout diagnostic (this app has no docks).

Switched on in Settings ▸ General ("Log UI lags"), off by default; it
starts and stops at once. It costs one 20 Hz timer and logs only on a stall.

Environment:
    TK_WATCHDOG=0 / =1              — force off / on, whatever Settings says
    TK_WATCHDOG_HEARTBEAT_MS        — heartbeat tick on the GUI thread (50)
    TK_WATCHDOG_STALL_MS            — stall threshold (150)
    TK_WATCHDOG_POLL_S              — background poll interval (0.05)
    TK_WATCHDOG_RESAMPLE_MS         — min gap between samples of one stall (250)
    TK_WATCHDOG_NATIVE_SAMPLE=1     — macOS: when the stall has no frame of
                                      ours (stuck in Qt / AppKit), run
                                      ``/usr/bin/sample`` once per stall and
                                      write the native stack next to log.log
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time
import traceback
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer

from core import log_bundle

log = logging.getLogger("lag")

_SAMPLE_BIN = "/usr/bin/sample"
# <src>/ui/ui_watchdog.py → <src>: a stack with no frame under it is stuck in
# Qt / the OS below app.exec(), where the Python sample says nothing.
_OUR_CODE = str(Path(__file__).resolve().parent.parent)


@dataclass(frozen=True)
class WatchdogConfig:
    heartbeat_ms: int = 50
    stall_ms: int = 150
    poll_s: float = 0.05
    resample_ms: int = 250
    native_sample: bool = False
    native_sample_s: int = 1
    native_sample_dir: str = ""

    @classmethod
    def from_env(cls, env: dict[str, str] | os._Environ, sample_dir: str) -> WatchdogConfig:
        native = (
            env.get("TK_WATCHDOG_NATIVE_SAMPLE") == "1"
            and sys.platform == "darwin"
            and os.path.exists(_SAMPLE_BIN)
        )
        return cls(
            heartbeat_ms=int(env.get("TK_WATCHDOG_HEARTBEAT_MS", "50")),
            stall_ms=int(env.get("TK_WATCHDOG_STALL_MS", "150")),
            poll_s=float(env.get("TK_WATCHDOG_POLL_S", "0.05")),
            resample_ms=int(env.get("TK_WATCHDOG_RESAMPLE_MS", "250")),
            native_sample=native,
            native_sample_dir=sample_dir,
        )


class MainThreadWatchdog:
    """Detects Qt event-loop stalls and logs the main thread's stack.

    Lives as long as ``parent`` (the QApplication): the timer is its child,
    and the daemon thread stops when the parent is destroyed.
    """

    def __init__(self, parent: QObject, cfg: WatchdogConfig) -> None:
        main_ident = threading.main_thread().ident
        if main_ident is None:
            raise RuntimeError("main thread has no ident — cannot watch it")
        self._cfg = cfg
        self._main_ident: int = main_ident
        self._lock = threading.Lock()
        self._last_tick_ns = time.monotonic_ns()
        # Whether a modal dialog owned the loop at the last tick; stamped with
        # the timestamp so a stall is described by the state it began in.
        self._modal_at_last_tick = False
        self._stop = threading.Event()
        self._stall_started_ns: int | None = None
        self._last_sample_ns = 0
        self._last_stack_hash: int | None = None
        self._native_captured = False
        self.stalls = 0  # finished stalls, for tests and the exit line
        self.worst_ms = 0.0
        self._timer = QTimer(parent)
        self._timer.setInterval(cfg.heartbeat_ms)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.timeout.connect(self._on_heartbeat)
        self._thread = threading.Thread(target=self._watch_loop, name="ui-watchdog", daemon=True)
        parent.destroyed.connect(self._stop.set)

    def start(self) -> None:
        self._timer.start()
        self._thread.start()

    def stop(self) -> None:
        self._timer.stop()
        self._timer.deleteLater()
        self._stop.set()

    def join(self, timeout: float | None = None) -> None:
        self._thread.join(timeout)

    # ── GUI thread ────────────────────────────────────────────────────────

    def _on_heartbeat(self) -> None:
        from PySide6.QtWidgets import QApplication

        modal = QApplication.instance() is not None and QApplication.activeModalWidget() is not None
        with self._lock:
            self._last_tick_ns = time.monotonic_ns()
            self._modal_at_last_tick = modal

    # ── watchdog thread ───────────────────────────────────────────────────

    def _watch_loop(self) -> None:
        threshold_ns = self._cfg.stall_ms * 1_000_000
        resample_ns = self._cfg.resample_ms * 1_000_000
        while not self._stop.wait(self._cfg.poll_s):
            now = time.monotonic_ns()
            with self._lock:
                gap_ns = now - self._last_tick_ns
                modal = self._modal_at_last_tick
            if gap_ns >= threshold_ns:
                if self._stall_started_ns is None:
                    self._stall_started_ns = now - gap_ns
                    self._last_sample_ns = 0
                    self._last_stack_hash = None
                    self._native_captured = False
                if now - self._last_sample_ns >= resample_ns:
                    self._sample_stack(gap_ns / 1_000_000, modal=modal)
                    self._last_sample_ns = now
            elif self._stall_started_ns is not None:
                self._end_stall((now - self._stall_started_ns) / 1_000_000)

    def _end_stall(self, duration_ms: float) -> None:
        self.stalls += 1
        self.worst_ms = max(self.worst_ms, duration_ms)
        log.warning("UI recovered after %.0f ms", duration_ms)
        self._stall_started_ns = None
        self._last_stack_hash = None

    def _sample_stack(self, gap_ms: float, *, modal: bool) -> None:
        note = " [a modal dialog owns the loop]" if modal else ""
        frame = sys._current_frames().get(self._main_ident)
        if frame is None:
            log.warning("main thread stalled %.0f ms%s (no frame)", gap_ms, note)
            return
        stack = "".join(traceback.format_stack(frame))
        in_our_code = _OUR_CODE in stack
        if self._cfg.native_sample and not self._native_captured and not in_our_code and not modal:
            self._native_captured = True
            self._capture_native_stack(gap_ms)
        stack_hash = hash(stack)
        if stack_hash == self._last_stack_hash:
            log.warning("  …still stalled at the same frame (%.0f ms so far)", gap_ms)
            return
        self._last_stack_hash = stack_hash
        log.warning("main thread stalled %.0f ms%s — stack:\n%s", gap_ms, note, stack)

    def _capture_native_stack(self, gap_ms: float) -> None:
        """Run macOS ``sample`` on our own pid; it writes its report itself."""
        out = os.path.join(
            self._cfg.native_sample_dir,
            f"native_stall_{time.strftime('%Y%m%d_%H%M%S')}_{os.getpid()}.txt",
        )
        try:
            subprocess.Popen(
                [
                    _SAMPLE_BIN,
                    str(os.getpid()),
                    str(self._cfg.native_sample_s),
                    "10",
                    "-mayDie",
                    "-file",
                    out,
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.STDOUT,
            )
        except OSError as exc:
            log.warning("native stack capture failed to launch: %r", exc)
            return
        log.warning(
            "native stall (%.0f ms, no frame of ours) — sampling %ds → %s",
            gap_ms,
            self._cfg.native_sample_s,
            out,
        )


SETTING_KEY = "ui_watchdog"  # Settings ▸ General; off by default
SETTING_DEFAULT = False

# Keeps the watchdog (and its thread) alive while it runs.
_INSTALLED: MainThreadWatchdog | None = None


def watchdog_wanted(env: dict[str, str] | os._Environ, setting: bool) -> bool:
    """The Settings checkbox, unless ``TK_WATCHDOG`` is 0 or 1."""
    forced = env.get("TK_WATCHDOG")
    if forced in ("0", "1"):
        return forced == "1"
    return setting


def running_watchdog() -> MainThreadWatchdog | None:
    return _INSTALLED


def watchdog_state() -> str:
    """The watchdog's line in the log bundle's report."""
    wd = running_watchdog()
    if wd is None:
        return "off"
    return f"on, {wd.stalls} stall(s), worst {wd.worst_ms:.0f} ms"


# The report lives in core, which cannot see this module; it is told here.
log_bundle.add_probe("UI lag logging", watchdog_state)


def set_ui_watchdog(on: bool) -> MainThreadWatchdog | None:
    """Start or stop the watchdog on the QApplication; takes effect at once."""
    global _INSTALLED
    if not on:
        if _INSTALLED is not None:
            _INSTALLED.stop()
            log.info("UI watchdog off")
            _INSTALLED = None
        return None
    if _INSTALLED is not None:
        return _INSTALLED
    from PySide6.QtWidgets import QApplication

    from core.runtime import user_log_dir

    app = QApplication.instance()
    if app is None:
        return None
    cfg = WatchdogConfig.from_env(os.environ, str(user_log_dir()))
    _INSTALLED = MainThreadWatchdog(app, cfg)
    _INSTALLED.start()
    log.info(
        "UI watchdog armed: heartbeat=%dms threshold=%dms resample=%dms native-sample=%s",
        cfg.heartbeat_ms,
        cfg.stall_ms,
        cfg.resample_ms,
        "on" if cfg.native_sample else "off",
    )
    return _INSTALLED


def install_ui_watchdog() -> MainThreadWatchdog | None:
    """At startup: start the watchdog if Settings (or TK_WATCHDOG) asks for it."""
    from PySide6.QtCore import QSettings

    setting = QSettings().value(SETTING_KEY, SETTING_DEFAULT, type=bool)
    return set_ui_watchdog(watchdog_wanted(os.environ, setting))
