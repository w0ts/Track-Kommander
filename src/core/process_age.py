"""How long a process has run, from ``ps`` (macOS and Linux; no psutil in TK).

Inside Traktor TK runs in Traktor's own process, so the age of this process is
how long ago Traktor started: the window in which Traktor puts the last
session's tracks back on its decks (see ``ui.mw_native``).
"""

from __future__ import annotations

import logging
import re
import subprocess

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


def process_age_s(pid: int) -> float | None:
    """Seconds since process *pid* started, or None when ps cannot tell."""
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
