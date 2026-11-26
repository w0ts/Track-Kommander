"""Finding the decks on Traktor's screen, and when to look again.

Deck letters and drop positions are read from a screenshot of Traktor (about
300 ms of image maths), so the window decides when that is worth doing: at
startup, when Traktor's layout flips, when its window has stopped moving, and
on a slow fallback timer. That scheduling was part of ``MainWindow``; it is a
mixin here so ``self`` and the timers ``MainWindow.__init__`` builds are
unchanged. The ``_calibrated`` and ``_layout_read`` signals stay on
``MainWindow``, because Qt signals only work on a ``QObject`` subclass.
"""

from __future__ import annotations

import logging
import sys
import threading

#: Deck drop positions are re-detected on change; this is only the fallback.
_CALIBRATE_FALLBACK_MS = 60_000

#: After Traktor's window or layout changes, wait this long for it to settle
#: (a window drag is many small moves) before re-detecting the decks.
_RECALIBRATE_SETTLE_MS = 800

# Deck detection (a Traktor screenshot + image maths, ~300 ms) runs on a
# thread where the capture allows it. Windows captures with QScreen.grabWindow,
# which must stay on the GUI thread.
_CALIBRATE_OFF_THREAD = sys.platform == "darwin"


class CalibrationMixin:
    """Deck-detection scheduling: throttle, thread, result."""

    def _run_layout_probe(self, probe, done) -> None:
        """Capture Traktor for LayoutWatch off the GUI thread where the
        capture allows it (macOS, as for deck detection); the reading is
        handed back to *done* on the GUI thread."""
        from ui.layout_watch import run_probe_safely

        if not _CALIBRATE_OFF_THREAD:
            done(run_probe_safely(probe))
            return

        def _work() -> None:
            frac = run_probe_safely(probe)
            try:
                self._layout_read.emit(done, frac)
            except RuntimeError:  # the window closed while we captured
                logging.getLogger(__name__).debug("layout probe: window gone")

        threading.Thread(target=_work, name="layout-probe", daemon=True).start()

    def _on_layout_changed(self, old: str, new: str) -> None:
        """Re-detect deck letters when Traktor's GUI layout flips.

        Wait briefly so Traktor has time to repaint before we screenshot.
        """
        from PySide6.QtCore import QTimer

        # Reset the throttle so this fires regardless of cooldown.
        self._last_auto_calibrate = 0.0
        QTimer.singleShot(
            500,
            lambda: self._auto_calibrate_decks(f"layout {old}→{new}"),
        )
        # If we're snapped over the library, re-anchor below the new deck row so
        # C/D stay visible in 4-deck (and the overlay reclaims space in 2-deck).
        # Slightly later so Traktor has finished repainting the new layout.
        QTimer.singleShot(700, self.now_tab.resnap_library_overlay)

    def _schedule_recalibrate(self, reason: str) -> None:
        """Re-detect deck positions once Traktor has stopped changing.

        Restarted by every change, so dragging Traktor's window across the
        screen costs one detection at the end, not one per step.
        """
        timer = getattr(self, "_recalibrate_timer", None)
        if timer is None:
            return
        self._recalibrate_reason = reason
        timer.start()

    def _auto_calibrate_decks(self, reason: str, force: bool = False) -> None:
        """Capture Traktor and auto-detect deck letters in the background.

        Throttled to one *successful* run per 5 s so a flurry of deck-loaded
        events doesn't spam screenshot captures. A failed run (Traktor not
        visible yet) doesn't burn the cooldown so the next event retries.

        The capture and detection run on a thread (macOS), off the GUI
        thread the UI watchdog caught them stalling. A request made while a
        run is in flight is kept and runs once it finishes — the window may
        have moved during the capture.
        """
        if getattr(self, "_integrated", False):
            return  # inside Traktor: loads are native, there are no drop positions to find
        import time

        now = time.time()
        # *force*: a change was seen (window moved, layout shifted) — the
        # positions are stale now, whatever the cooldown says.
        if not force and now - self._last_auto_calibrate < 5.0:
            return
        if self._calibrating:
            self._calibrate_pending = (reason, force)
            return
        self._calibrating = True
        if _CALIBRATE_OFF_THREAD:
            threading.Thread(
                target=self._calibrate_worker,
                args=(reason, now),
                name="deck-calibrate",
                daemon=True,
            ).start()
        else:
            self._calibrate_worker(reason, now)

    def _calibrate_worker(self, reason: str, started: float) -> None:
        """Run one detection; the result goes to _on_calibrated on the GUI thread."""
        from core.drag import auto_calibrate_decks

        try:
            saved = auto_calibrate_decks()
        except Exception:
            logging.getLogger(__name__).exception("auto-calibrate (%s) failed", reason)
            saved = {}
        try:
            self._calibrated.emit(reason, saved, started)
        except RuntimeError:  # the window closed while we captured
            logging.getLogger(__name__).debug("auto-calibrate (%s): window gone", reason)

    def _on_calibrated(self, reason: str, saved: dict, started: float) -> None:
        """Logs at INFO only when the saved deck set changes; periodic ticks
        that re-confirm the same layout fall through to DEBUG to avoid
        flooding the terminal during long scans."""
        self._calibrating = False
        if saved:
            self._last_auto_calibrate = started

        prev = getattr(self, "_last_calibrate_set", None)
        cur = frozenset(saved.keys())
        level = logging.INFO if cur != prev else logging.DEBUG
        self._last_calibrate_set = cur
        logging.getLogger(__name__).log(
            level,
            "auto-calibrate (%s): %d deck(s) saved%s",
            reason,
            len(saved),
            (" — " + ", ".join(sorted(saved))) if saved else "",
        )
        pending, self._calibrate_pending = self._calibrate_pending, None
        if pending is not None:
            self._auto_calibrate_decks(*pending)
