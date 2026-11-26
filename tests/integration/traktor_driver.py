"""Drive Traktor Pro from the OS layer (macOS).

Thin helpers for activating the app, sending keystrokes (Traktor mappings), and
dropping a file into a deck. The drag uses Quartz events from a Finder proxy
icon — Finder lets you drag a file from the title bar of an open window, which
gives us a stable on-screen source we can simulate from.

This module is intentionally small: it handles the ``how do we touch
Traktor`` mechanics; assertions live in tests.
"""

from __future__ import annotations

import logging
import subprocess
import sys
import time
from pathlib import Path

# Reuse the existing window/deck helpers.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "src"))

from core.drag import (  # noqa: E402
    TRAKTOR_OWNER_NAMES,
    _deck_drop_pos,
    _onscreen_windows,
    _post_mouse,
    _traktor_bounds,
)

_log = logging.getLogger(__name__)


class DriverError(RuntimeError):
    """Raised when an OS-level interaction with Traktor fails."""


def _osascript(script: str, timeout: float = 5.0) -> str:
    """Run an AppleScript snippet and return stdout (stripped)."""
    proc = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if proc.returncode != 0:
        raise DriverError(f"osascript failed (rc={proc.returncode}): {proc.stderr.strip()}")
    return proc.stdout.strip()


def is_traktor_running() -> bool:
    wins = _onscreen_windows()
    return any(w.get("kCGWindowOwnerName") in TRAKTOR_OWNER_NAMES for w in wins)


def wait_for_traktor(timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if is_traktor_running():
            return
        time.sleep(0.5)
    raise DriverError(f"Traktor did not appear within {timeout}s")


def activate_traktor() -> None:
    """Bring Traktor to the foreground."""
    for name in TRAKTOR_OWNER_NAMES:
        try:
            _osascript(f'tell application "{name}" to activate')
            time.sleep(0.3)
            return
        except DriverError:
            continue
    raise DriverError("Could not activate any known Traktor process")


def send_keystroke(key: str, modifiers: tuple[str, ...] = ()) -> None:
    """Send a keystroke to the frontmost app via AppleScript.

    ``modifiers`` items must be one of: command, option, control, shift.
    """
    mods = ", ".join(f"{m} down" for m in modifiers)
    using = f" using {{{mods}}}" if mods else ""
    safe = key.replace('"', '\\"')
    _osascript(f'tell application "System Events" to keystroke "{safe}"{using}')


def deck_drop_position(deck: str) -> tuple[float, float]:
    """Return the screen (x, y) drop point for ``deck`` (A/B/C/D)."""
    bounds = _traktor_bounds(_onscreen_windows())
    if not bounds:
        raise DriverError("Traktor window not found")
    return _deck_drop_pos(deck, bounds)


def _finder_proxy_icon_position(file_path: Path) -> tuple[float, float]:
    """Reveal ``file_path`` in Finder and return the title-bar proxy icon centre.

    The proxy icon is the small file icon next to the document title in the
    Finder window's title bar. It's draggable like the file itself, so we use
    it as a stable on-screen source for the drag.
    """
    posix = str(file_path).replace('"', '\\"')
    script = f'''
        tell application "Finder"
            activate
            reveal POSIX file "{posix}"
        end tell
        delay 0.3
        tell application "System Events" to tell process "Finder"
            set winPos to position of window 1
            set winSize to size of window 1
            return (item 1 of winPos as text) & "," & (item 2 of winPos as text) & "," ¬
                 & (item 1 of winSize as text) & "," & (item 2 of winSize as text)
        end tell
    '''
    out = _osascript(script, timeout=8.0)
    try:
        x, y, w, _h = (float(v) for v in out.split(","))
    except ValueError as e:
        raise DriverError(f"Could not parse Finder window geometry: {out!r}") from e
    # Title bar is ~28px tall; proxy icon sits ~half-way across the title text.
    # We aim slightly left of centre to land on the icon, not the filename.
    return x + w / 2 - 80, y + 14


def _drag(src: tuple[float, float], dst: tuple[float, float]) -> None:
    import Quartz  # local import: macOS only

    sx, sy = src
    dx, dy = dst
    _post_mouse(Quartz.kCGEventMouseMoved, (sx, sy))
    _post_mouse(Quartz.kCGEventLeftMouseDown, (sx, sy))
    time.sleep(0.10)
    # Step the drag to give Traktor's drop target time to register.
    steps = 12
    for i in range(1, steps + 1):
        t = i / steps
        _post_mouse(
            Quartz.kCGEventLeftMouseDragged,
            (sx + (dx - sx) * t, sy + (dy - sy) * t),
        )
        time.sleep(0.02)
    _post_mouse(Quartz.kCGEventLeftMouseUp, (dx, dy))


def drop_file_to_deck(file_path: Path, deck: str) -> None:
    """Drop ``file_path`` (audio file) onto Traktor's deck ``A``|``B``|``C``|``D``."""
    if not file_path.exists():
        raise DriverError(f"file does not exist: {file_path}")
    src = _finder_proxy_icon_position(file_path)
    dst = deck_drop_position(deck)
    _log.info("drop_file_to_deck: %s → deck %s  src=%s dst=%s", file_path.name, deck, src, dst)
    activate_traktor()  # ensure deck drop zone is hit, not Finder
    time.sleep(0.2)
    _drag(src, dst)
