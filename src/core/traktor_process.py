"""Is Traktor running, and which window is it — decided here, once.

Every part of the app that needs to know whether Traktor is up, which app is
in front, or where Traktor's window is asks this module. Before it, the same
question was written out in several places, each with its own rule:

* ``core.traktor.is_traktor_running`` — macOS ``pgrep -f -i`` on
  ``Traktor( Pro N)?.app/Contents/MacOS/Traktor( Pro N)?$`` (any version,
  any case); Windows "``traktor pro``" anywhere in ``tasklist``'s output.
* ``core.drag`` / ``core.health`` / ``core.keyforward`` / ``core.traktor``
  quit and bring-forward (macOS) — the app name, ``localizedName`` or the
  window's ``kCGWindowOwnerName``, exactly one of :data:`APP_NAMES`.
* ``workers.webhook`` — ``localizedName == "Traktor Pro 4"`` only, so a
  Traktor Pro 3 was never found.
* ``core.drag_win`` (Windows) — "``traktor pro``" anywhere in the *full*
  executable path, which also took every other program in Traktor's install
  folder; ``core.keyforward`` (Windows) — the executable's file name starts
  with "``traktor``", which also takes "Traktor Kontrol …" helpers.

The rule now, one per platform — the strictest one that the real Traktor,
and so every call site, still satisfies:

* **macOS: the application's name is exactly one of** :data:`APP_NAMES`
  (case-sensitive). It is the rule most sites already used, and the only one
  that works for both a running application and a window: the Quartz window
  list carries the owner's name, not its bundle id. ``pgrep`` keeps doing the
  "is it running" probe (the process table is right even when AppKit has not
  yet heard of an exit), with its pattern built from the same names.
* **Windows: the executable's file name starts with** :data:`EXE_PREFIX`
  (case-insensitive) — ``Traktor Pro 4.exe``, ``Traktor Pro 3.exe``. A prefix
  rather than the exact name because the image name is read, not guessed:
  a point release may spell it differently.

Which window: the **largest** on-screen Traktor window is Traktor's main one
(snapping, capture, drop targets). Keys on Windows still go to the first
visible Traktor window in z-order (:func:`core.keyforward.traktor_hwnd`),
which is the one that takes input; that choice is unverified on hardware and
was left as it was.

Platform modules are imported on use with :func:`importlib.import_module`:
AppKit and Quartz exist only on macOS, ``core.drag_win`` only on Windows.
"""

from __future__ import annotations

import csv
import ctypes
import importlib
import logging
import os
import subprocess
import sys
from ctypes import wintypes
from typing import Any

from core import platform_modules

_log = logging.getLogger(__name__)

_IS_WIN = sys.platform == "win32"
_IS_MAC = sys.platform == "darwin"

#: macOS: the names Traktor's application — and so its windows — carry.
APP_NAMES: tuple[str, ...] = ("Traktor Pro 4", "Traktor Pro 3", "Traktor")

#: Windows: what the file name of Traktor's executable starts with.
EXE_PREFIX = "traktor pro"

_NAMES_RX = "|".join(APP_NAMES)  # letters, digits and spaces only
#: ``pgrep -f`` pattern: the bundle executable, anchored at the end of the
#: command line so neither Track Kommander nor Traktor's crashpad_handler
#: (whose argv mentions the same path) can match.
PGREP_PATTERN = rf"({_NAMES_RX})\.app/Contents/MacOS/({_NAMES_RX})$"

Bounds = tuple[float, float, float, float]


# ── the rule ────────────────────────────────────────────────────────────────


def is_traktor_app_name(name: object) -> bool:
    """macOS: True when *name* (an app's or a window owner's) is Traktor's."""
    return name is not None and str(name) in APP_NAMES


def is_traktor_exe(path: str | None) -> bool:
    """Windows: True when the executable at *path* (or that file name) is
    Traktor."""
    if not path:
        return False
    base = path.replace("\\", "/").rsplit("/", 1)[-1]
    return base.lower().startswith(EXE_PREFIX)


# ── platform modules ────────────────────────────────────────────────────────


def _appkit() -> Any:
    return importlib.import_module("AppKit")


def _quartz() -> Any:
    return importlib.import_module("Quartz")


def _drag_win() -> Any:
    return importlib.import_module("core.drag_win")


# ── is it running ───────────────────────────────────────────────────────────


def windows_images() -> list[str]:
    """Running Traktor executable names, spelled as ``tasklist`` spells them.

    Read rather than guessed: ``taskkill`` needs the name the image actually
    has. Empty when ``tasklist`` cannot be run.
    """
    try:
        out = subprocess.run(
            ["tasklist", "/NH", "/FO", "CSV"],
            capture_output=True,
            text=True,
            timeout=4,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return []
    names: list[str] = []
    for row in csv.reader(out.stdout.splitlines()):
        if row and is_traktor_exe(row[0]) and row[0] not in names:
            names.append(row[0])
    return names


def _pgrep_running() -> bool:
    try:
        result = subprocess.run(
            ["pgrep", "-f", PGREP_PATTERN],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0 and bool(result.stdout.strip())


def running() -> bool:
    """True if a Traktor process is running. False on any error, so a
    missing tool never blocks the user."""
    if _IS_WIN:
        return bool(windows_images())
    return _pgrep_running()


def running_apps() -> list:
    """macOS: Traktor's ``NSRunningApplication`` objects. Raises where AppKit
    is missing — each caller has its own answer for "could not tell"."""
    workspace = _appkit().NSWorkspace.sharedWorkspace()
    return [
        app for app in workspace.runningApplications() if is_traktor_app_name(app.localizedName())
    ]


def first_app() -> tuple[int, Any] | None:
    """macOS: ``(pid, NSRunningApplication)`` of the running Traktor, or None.
    Raises as :func:`running_apps` does."""
    for app in running_apps():
        return int(app.processIdentifier()), app
    return None


# ── which app is in front ───────────────────────────────────────────────────


def _foreground_win() -> tuple[int, int]:
    """Windows: ``(hwnd, pid)`` of the foreground window; ``(0, 0)`` if none."""
    user32 = platform_modules.win_ctypes().windll.user32
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return 0, 0
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(hwnd), int(pid.value)


def _front_app_win() -> str:
    hwnd, pid = _foreground_win()
    if not hwnd:
        return "other"
    if pid == os.getpid():
        return "us"
    return "traktor" if _drag_win().is_traktor_hwnd(hwnd) else "other"


def _front_app_mac() -> str:
    front = _appkit().NSWorkspace.sharedWorkspace().frontmostApplication()
    if front is None:
        return "other"
    if front.processIdentifier() == os.getpid():
        return "us"
    return "traktor" if is_traktor_app_name(front.localizedName()) else "other"


def front_app() -> str:
    """Which app is active: ``"traktor"``, ``"us"`` or ``"other"``.

    ``"other"`` also when the check fails, so a broken check never makes a
    docked window float over, or take focus from, whatever the user is doing.
    """
    try:
        if _IS_WIN:
            return _front_app_win()
        if _IS_MAC:
            return _front_app_mac()
        return "other"
    except Exception:
        _log.debug("front_app: check failed", exc_info=True)
        return "other"


def front_app_name() -> str:
    """The active app's name, bundle id and pid, for the log (macOS)."""
    try:
        if _IS_MAC:
            front = _appkit().NSWorkspace.sharedWorkspace().frontmostApplication()
            if front is not None:
                return (
                    f"{front.localizedName()} ({front.bundleIdentifier()}, "
                    f"pid {front.processIdentifier()})"
                )
        return "?"
    except Exception:
        return "?"


# ── its window ──────────────────────────────────────────────────────────────


def mac_windows():
    """macOS: the Quartz window list, front to back, on-screen only."""
    quartz = _quartz()
    return quartz.CGWindowListCopyWindowInfo(
        quartz.kCGWindowListOptionOnScreenOnly | quartz.kCGWindowListExcludeDesktopElements,
        quartz.kCGNullWindowID,
    )


def _largest_traktor_window(windows) -> dict | None:
    """The Traktor window in *windows* with the largest area, or None.

    Traktor has several (main, browser previews, …); the largest skips the
    transient and auxiliary ones.
    """
    best, best_area = None, 0.0
    for win in windows:
        if not is_traktor_app_name(win.get("kCGWindowOwnerName")):
            continue
        b = win.get("kCGWindowBounds", {})
        area = float(b.get("Width", 0)) * float(b.get("Height", 0))
        if area > best_area:
            best, best_area = win, area
    return best


def mac_window_bounds(windows) -> Bounds | None:
    """macOS: ``(x, y, w, h)`` of Traktor's main window in *windows*, in
    logical points, or None."""
    win = _largest_traktor_window(windows)
    if win is None:
        return None
    b = win["kCGWindowBounds"]
    return (float(b["X"]), float(b["Y"]), float(b["Width"]), float(b["Height"]))


def mac_window_id(windows) -> int | None:
    """macOS: the ``kCGWindowNumber`` of Traktor's main window, or None."""
    win = _largest_traktor_window(windows)
    return None if win is None else win.get("kCGWindowNumber")


def window_bounds() -> Bounds | None:
    """Traktor's main on-screen window ``(x, y, w, h)``, or None.

    In the platform's own units: logical points on macOS, PHYSICAL pixels on
    Windows (where a minimized window counts as absent). Raises when the
    platform lookup fails — the callers differ on what that should mean.
    """
    if _IS_WIN:
        found = _drag_win().traktor_window()
        return None if not found else found[1]
    return mac_window_bounds(mac_windows())
