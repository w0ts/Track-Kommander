"""Time left in one phase of a long job, from the rate that phase has shown.

Phases of an export — decoding, copying, writing analysis — run at rates
that have nothing to do with each other, so each gets its own estimate and
the previous phase never colours it.
"""

from __future__ import annotations

import time
from collections.abc import Callable

#: One sample is not a rate; two make a poor one but beat nothing.
_MIN_SAMPLES = 2

#: Between the progress text and the estimate, so a display can split them.
SEP = "  ·  "


def split_eta(text: str) -> tuple[str, str]:
    """``(progress text, estimate)`` — the estimate empty when there is none."""
    head, sep, eta = text.rpartition(SEP)
    return (head, eta) if sep else (text, "")


def format_eta(seconds: float | None) -> str:
    """``"about 3 min left"`` — rough on purpose, since the estimate is."""
    if seconds is None:
        return ""
    if seconds <= 0:
        return "almost done"
    if seconds < 60:
        return "under a minute left"
    minutes = round(seconds / 60)
    if minutes < 60:
        return f"about {minutes} min left"
    hours, rest = divmod(minutes, 60)
    return f"about {hours} h {rest:02d} left" if rest else f"about {hours} h left"


class Eta:
    """Call :meth:`start` at each phase, :meth:`remaining` as units finish."""

    def __init__(self, *, now: Callable[[], float] | None = None) -> None:
        self._now = now or time.monotonic
        self._t0 = self._now()
        self._of = 0

    def start(self, of: int) -> None:
        """A new phase of *of* units begins now."""
        self._t0 = self._now()
        self._of = max(0, of)

    def remaining(self, done: int) -> float | None:
        """Seconds left in the phase, or None while there is no rate yet."""
        if done >= self._of:
            return 0.0
        elapsed = self._now() - self._t0
        if done < _MIN_SAMPLES or elapsed <= 0:
            return None
        return (self._of - done) * elapsed / done

    def label(self, text: str, done: int) -> str:
        """*text* with the estimate appended, when there is one."""
        eta = format_eta(self.remaining(done))
        return f"{text}{SEP}{eta}" if eta else text
