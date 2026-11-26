"""How long a process has run: ``ps`` on macOS and Linux, the process's creation time on Windows
(no ``ps`` there, and no psutil in TK).

Inside Traktor TK runs in Traktor's own process, so the age of this process is
how long ago Traktor started: the window in which Traktor puts the last
session's tracks back on its decks (see ``ui.mw_native``).
"""

from __future__ import annotations

import ctypes
import logging
import re
import subprocess
import sys

from core import platform_modules

log = logging.getLogger(__name__)

# ps's etime: [[dd-]hh:]mm:ss
_ETIME = re.compile(r"^(?:(?:(\d+)-)?(\d+):)?(\d+):(\d+)$")
_PS_TIMEOUT_S = 2.0


def parse_etime(text: str) -> float | None:
    """Seconds from ps's elapsed time ("03:24", "01:02:03", "2-01:00:00"); None if not one."""
    found = _ETIME.match(text.strip())
    if found is None:
        return None
    days, hours, minutes, seconds = (int(g) if g else 0 for g in found.groups())
    return float(((days * 24 + hours) * 60 + minutes) * 60 + seconds)


#: OpenProcess access right enough for GetProcessTimes, granted even for other users' processes.
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
#: FILETIME counts 100 ns ticks.
_TICKS_PER_S = 10_000_000


def _windows_age_s(pid: int) -> float | None:
    """Seconds since process *pid* started (Windows: its creation time), None if not found."""
    win = platform_modules.win_ctypes()
    kernel32 = win.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, 0, pid)
    if not handle:
        return None
    try:
        created, exited, kernel, user, now = (ctypes.c_uint64() for _ in range(5))
        if not kernel32.GetProcessTimes(
            ctypes.c_void_p(handle),
            ctypes.byref(created),
            ctypes.byref(exited),
            ctypes.byref(kernel),
            ctypes.byref(user),
        ):
            log.warning("process age of %d: GetProcessTimes failed (%d)", pid, win.get_last_error())
            return None
        kernel32.GetSystemTimeAsFileTime(ctypes.byref(now))
        return max(0.0, (now.value - created.value) / _TICKS_PER_S)
    finally:
        kernel32.CloseHandle(handle)


def process_age_s(pid: int) -> float | None:
    """Seconds since process *pid* started, or None when that cannot be told."""
    if sys.platform == "win32":
        return _windows_age_s(pid)
    try:
        done = subprocess.run(
            ["ps", "-o", "etime=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=_PS_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("process age of %d: ps failed: %s", pid, exc)
        return None
    return parse_etime(done.stdout) if done.returncode == 0 else None
