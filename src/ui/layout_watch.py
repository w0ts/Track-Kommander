"""Follow Traktor's layout: when the browser moves, so does the overlay.

Traktor reports no layout property, so the browser line is read from a
capture of its window every few seconds while we are snapped over it. The
capture is of Traktor's window alone, so it sees through us — no hiding.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Protocol

log = logging.getLogger(__name__)

#: Fraction of the window height the browser line must move to count as a
#: layout change. Detection is exact to the pixel; this only absorbs noise.
_MOVE = 0.004


class Overlay(Protocol):
    def is_snapped(self) -> bool: ...
    def resnap(self) -> None: ...


ProbeRunner = Callable[[Callable[[], "float | None"], Callable[["float | None"], None]], None]


def _run_inline(probe: Callable[[], float | None], done: Callable[[float | None], None]) -> None:
    done(run_probe_safely(probe))


def run_probe_safely(probe: Callable[[], float | None]) -> float | None:
    """The probe's reading, or None if it raised — logged, and the next tick
    tries again rather than the watch waiting forever on a lost reading."""
    try:
        return probe()
    except Exception:
        log.exception("layout watch: probing Traktor's browser failed")
        return None


class LayoutWatch:
    """Call :meth:`tick` periodically; it re-snaps on a moved browser line.

    *run_probe* decides where the capture runs: inline by default, or on a
    thread that hands the reading back to :meth:`on_reading` on the GUI
    thread. A capture is a few megabytes copied and scanned; on the GUI
    thread every three seconds it showed up as a hitch.
    """

    def __init__(
        self,
        overlay: Overlay,
        *,
        probe: Callable[[], float | None] | None = None,
        run_probe: ProbeRunner | None = None,
    ) -> None:
        self._overlay = overlay
        if probe is None:
            from core.drag import browser_top_fraction

            probe = browser_top_fraction
        self._probe = probe
        self._run_probe = run_probe or _run_inline
        self._last: float | None = None
        self._in_flight = False

    def tick(self) -> None:
        snapped = self._overlay.is_snapped()
        if snapped != getattr(self, "_was_snapped", None):
            self._was_snapped = snapped
            log.info(
                "layout watch: %s",
                "following Traktor's browser"
                if snapped
                else "window is not where the snap put it — idle",
            )
        if not snapped or self._in_flight:
            return
        self._in_flight = True
        self._run_probe(self._probe, self.on_reading)

    def on_reading(self, frac: float | None) -> None:
        """One probe's result; called on the GUI thread."""
        self._in_flight = False
        if frac is None:
            return  # Traktor hidden or no browser: wait
        # The window may have been moved while the capture ran.
        if not self._overlay.is_snapped():
            return
        if self._last is None:
            self._last = frac
            return
        if abs(frac - self._last) >= _MOVE:
            log.info(
                "Traktor layout changed: browser line %.3f → %.3f, re-snapping", self._last, frac
            )
            self._last = frac
            self._overlay.resnap()
