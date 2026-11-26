"""Starting the application: logging, identity, the library, the window.

Everything here runs once, before or around the first ``MainWindow``, and none
of it is a method of that class. It lived at the bottom of ``ui.main_window``
until that file grew past what could be read in one sitting; it is a module of
its own so the window's code is only the window. ``ui.main_window`` re-exports
``main`` so the entry script's import does not change.
"""

from __future__ import annotations

import logging
import os
import sys
import threading

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from core.constants import APP_NAME
from core.db import init_db
from core.prefs import VERBOSE_LOG
from ui.style import STYLE, load_app_fonts
from ui.unhandled import install_exception_hooks, open_library

# Kept alive for the process lifetime so faulthandler's target file (opened in
# main()) isn't garbage-collected while it's still installed.
_CRASH_LOG_FH = None


def _install_stderr_filter() -> None:
    """Filter known-noise lines that C libraries write directly to fd 2.

    libmpg123 (via libsndfile) and ffmpeg/libav print malformed-MP3
    diagnostics straight to stderr with ``fprintf``, bypassing Python
    logging entirely. We splice fd 2 through an os.pipe(); a daemon
    thread reads lines from the pipe, drops the patterns we know are
    noise, and forwards the rest to the saved original stderr.

    Python's ``sys.stderr`` is repointed at the saved fd, so logging
    output and tracebacks bypass the filter and go straight to the
    terminal.
    """
    import os as _os
    import re as _re
    import sys as _sys
    import threading as _th

    _NOISE = _re.compile(
        r"^(Note:|Warning: Xing|"
        r"\[src/libmpg123/|"
        r"\[mp3float|\[mp3 @|\[aac |\[aac @|"
        r"Loading weights:)"
    )

    # This noise filter only matters when a real stderr stream exists (a dev
    # terminal, or a macOS .app which still has a valid fd 2). A Windows GUI
    # build (--windows-console-mode=disable) launched by double-click or a
    # shortcut has NO usable stderr: os.dup(2) may even succeed yet hand back a
    # descriptor wrapping an invalid OS handle, so fdopen()/dup2() then raise
    # "[WinError 6] The handle is invalid" and crash the whole app before any
    # window or log file is created. There is no fprintf noise to filter when
    # there's no stderr to write it to, so bail out cleanly on any failure.
    #
    # We open the saved copy BEFORE the dup2() that redirects fd 2, so a failure
    # here leaves fd 2 (and sys.stderr) untouched.
    try:
        saved_fd = _os.dup(2)
        saved = _os.fdopen(saved_fd, "w", buffering=1, encoding="utf-8", errors="replace")
    except OSError:
        try:
            _os.close(saved_fd)  # close the orphaned dup if fdopen failed
        except (OSError, NameError, UnboundLocalError):
            pass
        return

    r_fd, w_fd = _os.pipe()
    _os.dup2(w_fd, 2)
    _os.close(w_fd)

    def _pump() -> None:
        with _os.fdopen(r_fd, "r", buffering=1, encoding="utf-8", errors="replace") as src:
            for line in src:
                if not _NOISE.search(line):
                    saved.write(line)
                    saved.flush()

    _th.Thread(target=_pump, daemon=True, name="stderr-filter").start()
    _sys.stderr = saved  # Python logs/tracebacks → terminal, skipping pipe


def _quiet_pyav_logging() -> None:
    """Reduce PyAV/libav log verbosity (warnings about broken streams)."""
    try:
        import av.logging

        av.logging.set_level(av.logging.ERROR)
    except Exception:
        pass


# How long a thread may hold the GIL before one waiting for it gets a turn.
# Python's default, 5 ms, starved the GUI thread whenever a worker ran pure
# Python (the USB export's database write): every PyObjC / Qt call releases
# the GIL and waits up to 5 ms to take it back, so a loop over the running
# apps or the on-screen windows stalled the UI 200–1000 ms. Measured with a
# busy worker and 100 such calls per 50 ms tick: 5 ms → ticks 265 ms late
# (median); 0.5 ms → under 2 ms, for ~25% less worker throughput — paid only
# while the GUI thread actually wants the GIL.
_GIL_SWITCH_INTERVAL_S = 0.0005


def _favour_the_gui_thread() -> None:
    sys.setswitchinterval(_GIL_SWITCH_INTERVAL_S)


# Modules first imported on the GUI thread a few seconds after launch.
# scipy.ndimage (deck-letter detection in the startup auto-calibrate) took
# ~420 ms there, which the UI watchdog logged as a stall.
_WARM_IMPORTS = ("scipy.ndimage",)


def _warm_imports_in_background() -> threading.Thread:
    """Import slow modules on a thread so the GUI thread finds them loaded.

    If the GUI thread asks for one while it is still loading, the import lock
    makes it wait for the rest — never longer than importing it itself.
    """
    import importlib

    def _run() -> None:
        for name in _WARM_IMPORTS:
            try:
                importlib.import_module(name)
            except ImportError as exc:
                logging.getLogger(__name__).debug("warm import %s: %s", name, exc)

    thread = threading.Thread(target=_run, name="import-warmup", daemon=True)
    thread.start()
    return thread


class _ErrorRelay(QObject):
    """Carries an error message to the GUI thread from whichever thread the
    error happened on."""

    told = Signal(str)


def _say_and_wait(title: str, text: str) -> None:
    """A message box with no window behind it, for before there is one."""
    QMessageBox.critical(None, title, text)


def main() -> None:
    # Install OS-level stderr filter and quiet PyAV before any handler is
    # created, so the rest of main() and the workers all benefit.
    _install_stderr_filter()
    _quiet_pyav_logging()

    _fmt = logging.Formatter(
        "%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
        datefmt="%H:%M:%S",
    )
    # Console: WARNING+ only for noisy internal loggers.
    # File (log.log): everything — useful for debugging scan errors.
    _NOISY_PREFIXES = ("numba", "librosa", "audioread", "models")
    _NOISY_EXACT = {"analysis", "core.drag"}  # "analysis_worker" stays verbose

    class _NoisyFilter(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            name = record.name
            noisy = name in _NOISY_EXACT or any(name.startswith(p) for p in _NOISY_PREFIXES)
            if noisy:
                return record.levelno >= logging.WARNING
            return True

    _console = logging.StreamHandler()
    _console.setFormatter(_fmt)
    _console.addFilter(_NoisyFilter())

    # Set Qt app identity BEFORE anything that calls QStandardPaths or
    # QSettings — they read from QCoreApplication's static state and we use
    # them to resolve the user data dir for logs and the SQLite DB, plus
    # the QSettings plist on macOS.
    #
    # Combo chosen so paths come out clean on every OS:
    #   * applicationName only (no organizationName) → QStandardPaths
    #     gives us  ~/Library/Application Support/Track Kommander/
    #     (single-level — setting both would double-nest as <org>/<app>/).
    #   * organizationDomain → QSettings on macOS derives its plist
    #     namespace from it, so we get  com.trackkommander-app.<app>.plist
    #     instead of Qt's "com.trolltech.unknown-organization" fallback.
    from PySide6.QtCore import QCoreApplication

    from core import profile

    QCoreApplication.setApplicationName(profile.app_name())
    QCoreApplication.setOrganizationDomain("trackkommander.app")

    # Move data and preferences over from the pre-rename identity before
    # anything reads them. No-op after the first run. See core/migrate.py.
    from core import migrate

    if not profile.is_beta():  # the beta starts clean, with its own folder
        migrate.run(APP_NAME)

    from core.runtime import bundle_data_dir, user_log_dir

    # Write log into the OS-standard per-user data dir. Resolves to
    # ``~/Library/Application Support/Track Kommander/logs/log.log`` on
    # macOS, ``~/.local/share/…`` on Linux, ``%APPDATA%/…`` on Windows.
    _log_path = user_log_dir() / "log.log"
    _file = logging.FileHandler(str(_log_path), mode="w", encoding="utf-8")
    _file.setFormatter(_fmt)

    # INFO by default; DEBUG via TK_LOG_LEVEL or Settings → General.

    from core.runtime import log_level

    logging.root.setLevel(log_level(os.environ, verbose=VERBOSE_LOG.get()))
    logging.root.handlers = [_console, _file]

    # numba's compiler logs thousands of byteflow/ssa DEBUG lines per JIT
    # compile straight into log.log (the file handler has no noisy-filter).
    # That bloated a single scan's log to >100 KB and slowed analysis. The
    # _NoisyFilter only covers the console, so silence the logger itself —
    # WARNING+ is all we ever want from numba.
    logging.getLogger("numba").setLevel(logging.WARNING)

    # Native crashes (a segfault / illegal-instruction inside onnxruntime,
    # numba codegen or libsndfile on a worker thread) kill the process with no
    # Python traceback — the log just stops mid-scan. faulthandler installs
    # OS-level fault handlers that dump the Python stack of every thread at the
    # moment of the fault. We point it at a persistent append-mode file (the
    # main log.log is truncated each launch) so the next crash self-reports.
    global _CRASH_LOG_FH
    try:
        import faulthandler

        _CRASH_LOG_FH = open(user_log_dir() / "crash.log", "a", encoding="utf-8")
        faulthandler.enable(file=_CRASH_LOG_FH, all_threads=True)
    except Exception:
        logging.getLogger(__name__).debug("faulthandler setup failed", exc_info=True)

    if sys.platform == "darwin":
        try:
            import AppKit  # noqa: F401
            import Quartz  # noqa: F401

            logging.info("pyobjc available — Traktor browser search enabled")
        except ImportError:
            logging.warning(
                "pyobjc not found — Traktor browser search disabled (pip install pyobjc to enable)"
            )

    _warm_imports_in_background()
    _favour_the_gui_thread()

    app = QApplication(sys.argv)
    # After the application object, not before: a library that will not open
    # used to end the process with no window to say so in.
    if not open_library(init_db, _say_and_wait):
        sys.exit(1)
    _base = bundle_data_dir()
    # Per-platform icon: Qt on Windows cannot decode .icns (the window/taskbar
    # icon silently stayed empty and fell back to the low-res exe resource), so
    # pick the multi-size .ico there; macOS keeps .icns; .png is the fallback.
    _icons_dir = _base / "assets" / "icons"
    _icon_name = "tk.ico" if sys.platform == "win32" else "tk.icns"
    _icon = _icons_dir / _icon_name
    if not _icon.exists():
        _icon = _icons_dir / "tk.png"
    if _icon.exists():
        app.setWindowIcon(QIcon(str(_icon)))
    # Register the bundled UI font (Inter) before the stylesheet applies, so its
    # font-family resolves; falls back to Helvetica Neue if the file is missing.
    fams = load_app_fonts(_base / "assets" / "fonts")
    logging.getLogger(__name__).info("loaded UI fonts: %s", fams or "none (fallback)")
    # Screen/DPI diagnostics — one line that settles any "UI renders bigger /
    # smaller / blurrier than on the other OS" report from the field.
    _scr = app.primaryScreen()
    if _scr is not None:
        logging.getLogger(__name__).info(
            "screen: logical=%dx%d dpr=%.2f logicalDpi=%.0f physicalDpi=%.0f name=%r",
            _scr.geometry().width(),
            _scr.geometry().height(),
            _scr.devicePixelRatio(),
            _scr.logicalDotsPerInch(),
            _scr.physicalDotsPerInch(),
            _scr.name(),
        )
    # Settings ▸ General "Log UI lags": log the GUI thread's stack whenever
    # the event loop stalls. Off by default; see ui/ui_watchdog.py.
    from ui.ui_watchdog import install_ui_watchdog

    install_ui_watchdog()
    app.setStyleSheet(STYLE)
    from ui.style import apply_dark_tooltip_palette

    apply_dark_tooltip_palette()
    from ui.main_window import MainWindow

    win = MainWindow()
    # From here on an error nobody caught goes to the log and the banner.
    # Through a signal, because it can come from any thread.
    relay = _ErrorRelay(app)
    relay.told.connect(lambda text: win.notice_banner.show_transient(text, "error"))
    install_exception_hooks(relay.told.emit)
    # Open already positioned over Traktor when it's running (no visible jump);
    # falls back to a normal show + retry timer when Traktor isn't up yet.
    win.show_positioned()

    # Graceful Ctrl+C (SIGINT) handling. Qt's event loop runs in C++, so a
    # Python signal handler never executes while the loop is idle — Ctrl+C is
    # ignored until some Python bytecode runs. A short heartbeat timer hands
    # control back to the interpreter ~5x/s so the handler can fire; the handler
    # closes the window (running MainWindow.closeEvent, which stops the webhook,
    # S4 watcher and analysis workers) and quits the app cleanly.
    import signal

    from PySide6.QtCore import QTimer

    def _handle_sigint(*_args) -> None:
        logging.info("SIGINT (Ctrl+C) received — shutting down gracefully…")
        win.close()
        app.quit()

    signal.signal(signal.SIGINT, _handle_sigint)
    _sigint_heartbeat = QTimer()
    _sigint_heartbeat.timeout.connect(lambda: None)
    _sigint_heartbeat.start(200)

    sys.exit(app.exec())
