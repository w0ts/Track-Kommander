"""Runtime introspection helpers — distinguish dev mode from a built app.

PyInstaller sets ``sys.frozen = True`` and ``sys._MEIPASS`` to the bundle
data dir. Nuitka doesn't set either: the binary lives at
``…/Track Kommander.app/Contents/MacOS/TrackKommander`` and bundled
data is co-located in that directory. We detect either packaging via a
single ``is_frozen()`` and expose a uniform ``bundle_data_dir()`` so call
sites don't have to re-implement the heuristic.
"""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import Mapping
from pathlib import Path


def in_traktor() -> bool:
    """True when TK runs inside Traktor (integrated mode: traktor-native's launcher sets
    TK_EMBED). Pages hidden there run on their defaults, not on choices made standalone."""
    return bool(os.environ.get("TK_EMBED"))


def is_frozen() -> bool:
    """True if running from a PyInstaller or Nuitka bundle."""
    if getattr(sys, "frozen", False):
        return True
    # Inside Traktor (the patched copy, traktor-native's launcher sets TK_EMBED) TK runs from
    # its source tree, but the executable is Traktor's, inside an .app: without this, TK took
    # Traktor's bundle for its own (Traktor's version on the About page, CLAP looked for next
    # to Traktor's binary — 2026-11-24).
    if in_traktor():
        return False
    # Nuitka sets ``__compiled__`` on every compiled module; the entry
    # script ends up as the running module's ``__main__``.
    main_mod = sys.modules.get("__main__")
    if main_mod is not None and hasattr(main_mod, "__compiled__"):
        return True
    # Fallback: executable lives inside a .app bundle (covers Nuitka in
    # ``--mode=app`` even if ``__compiled__`` ever stops being injected).
    exe = Path(sys.executable).resolve()
    return ".app/Contents/MacOS/" in exe.as_posix()


def bundle_data_dir() -> Path:
    """Directory holding bundled (read-only, ships-with-the-app) data files.

    * PyInstaller: ``sys._MEIPASS`` (PyInstaller extracts data files here).
    * Nuitka ``--mode=app``: the directory of ``sys.executable``
      (``…/Track Kommander.app/Contents/MacOS``).
    * Dev: the project root (two levels above this file).
    """
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass)
    if is_frozen():
        return Path(sys.executable).resolve().parent
    # Dev: <repo>/src/core/runtime.py → <repo>/
    return Path(__file__).resolve().parent.parent.parent


def user_data_dir() -> Path:
    """OS-standard per-user, writable data directory for the app.

    Resolves via Qt's ``QStandardPaths.AppLocalDataLocation`` so the result
    is correct on macOS / Windows / Linux without us hand-rolling paths:

      * macOS:  ``~/Library/Application Support/Track Kommander/``
      * Linux:  ``~/.local/share/Track Kommander/``
      * Windows: ``%APPDATA%/Track Kommander/``

    Used for the SQLite DB, logs, and any future downloaded artefacts —
    never for read-only files that ship with the bundle (see
    :func:`bundle_data_dir` for those).

    The directory is created if missing. ``QApplication`` must already be
    set up by the caller's process (any GUI process satisfies this); the
    org/app name come from ``QApplication.setOrganizationName/setApplicationName``
    in ``main_window.main()``.
    """
    from PySide6.QtCore import QStandardPaths

    p = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation))
    p.mkdir(parents=True, exist_ok=True)
    return p


def user_log_dir() -> Path:
    """Writable log directory under the per-user data dir."""
    p = user_data_dir() / "logs"
    p.mkdir(parents=True, exist_ok=True)
    return p


#: Environment override for the log level, e.g. ``TK_LOG_LEVEL=DEBUG``.
LOG_LEVEL_ENV = "TK_LOG_LEVEL"


def log_level(env: Mapping[str, str], *, verbose: bool) -> int:
    """The root log level: INFO, or DEBUG when *verbose* (the Settings
    checkbox), with ``TK_LOG_LEVEL`` in the environment winning over both.

    DEBUG used to be the default, which made the log a stream of per-second
    deck-calibration lines that buried the one line that mattered.
    """
    name = env.get(LOG_LEVEL_ENV, "").strip().upper()
    if name:
        level = logging.getLevelName(name)
        if isinstance(level, int):
            return level
    return logging.DEBUG if verbose else logging.INFO
