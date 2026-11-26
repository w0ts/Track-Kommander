"""Starting Traktor, and landing this window over it.

The window opens already in place when Traktor is running, starts Traktor
when asked to (the auto-start setting, a double-click on the cover), and
then keeps trying to snap over Traktor's library until its window is there —
a cold start of Traktor can take a minute or two.

A mixin for :class:`ui.main_window.MainWindow`: the methods are the ones it
always had, and share its state.
"""

from __future__ import annotations

import logging
import sys


class TraktorStartMixin:
    """Showing in place, starting Traktor, and the first snap."""

    def show_positioned(self) -> None:
        """Show the window already snapped over Traktor when it's running, so it
        opens in place instead of appearing at a default spot and jumping there
        ~1.5 s later. Falls back to a normal show + the retry timer when Traktor
        isn't up yet (e.g. Kommander launched first).
        """
        if sys.platform in ("darwin", "win32"):
            from core.drag import snap_over_traktor_library

            try:
                if snap_over_traktor_library(self):
                    self._initial_snap_done = True
                    self.now_tab.mark_snapped()
                    self._bring_up_running_traktor()
                    return
            except Exception:
                logging.getLogger(__name__).debug(
                    "initial snap-before-show failed; showing normally",
                    exc_info=True,
                )
        self.show()
        self._bring_up_running_traktor()

    def _bring_up_running_traktor(self) -> None:
        """At startup, a Traktor already running comes up behind this window.

        Otherwise it may sit behind whatever else is open (an editor, a
        browser) while this window floats above it with nothing to belong to.
        Traktor started by the auto-start setting has its own path.
        """
        from core.traktor import is_traktor_running

        if not is_traktor_running():
            return
        from PySide6.QtCore import QTimer

        QTimer.singleShot(300, self._restore_traktor_and_raise)
        logging.getLogger(__name__).info("startup: Traktor running — brought up behind")

    def _maybe_autostart_traktor(self) -> None:
        """Launch Traktor at startup when the Settings option is enabled.

        Launched without focus (macOS ``open -g``) so Kommander stays on
        top; the initial-snap retry loop then auto-sizes the window over
        Traktor's library as soon as its window appears. A cold Traktor
        start can far exceed the default 30 s snap budget, so it is
        extended here.
        """
        from PySide6.QtCore import QSettings

        if not QSettings().value("traktor_autostart", False, type=bool):
            return
        from core.traktor import is_traktor_running

        if is_traktor_running():
            return
        log = logging.getLogger(__name__)
        if self._launch_traktor():
            log.info("Traktor auto-start: launched in the background")
        else:
            log.warning(
                "Traktor auto-start is enabled but no Traktor installation "
                "was found (or the launch failed)"
            )

    def _launch_traktor(self) -> bool:
        """Start Traktor in the background and keep Kommander on top of it.

        Extends the snap budget for a cold start and re-arms the snap, so
        the window lands over Traktor's library once its window appears.
        The caller schedules the first snap attempt if one is not already
        pending. Returns False when there is no Traktor to launch.
        """
        from core.traktor import launch_traktor

        if not launch_traktor():
            return False
        self._autostarted_traktor = True
        self._initial_snap_budget = 40  # 40 × 3 s = 2 min cold-start budget
        self._start_autostart_raise_watch()
        return True

    def _start_traktor_and_snap(self) -> None:
        """The cover was double-clicked with Traktor closed: start it, snap."""
        from PySide6.QtCore import QTimer

        if self._launch_traktor():
            logging.getLogger(__name__).info("Traktor started from the cover art")
            self._initial_snap_done = False
            self._initial_snap_attempts = 0
            QTimer.singleShot(3000, self._try_initial_snap)
        else:
            self.notice_banner.show_transient(
                "No Traktor installation found — choose one in Settings.", "warning"
            )

    def _start_autostart_raise_watch(self) -> None:
        """Keep Kommander on top while the auto-started Traktor loads.

        ``open -g`` starts Traktor without focus, but Traktor activates
        itself when it *finishes* loading — well after its window first
        appears — and covers Kommander. Once a second, if Traktor is the
        frontmost app, re-raise Kommander. Stops shortly after the D2
        bridge's first ping (Traktor fully loaded, focus grabs are over)
        or after a hard timeout; it never takes focus from any app other
        than Traktor, so switching elsewhere mid-startup stays possible.
        """
        import time

        from PySide6.QtCore import QTimer

        self._raise_watch_started = time.monotonic()
        self._raise_watch = QTimer(self)
        self._raise_watch.setInterval(1000)
        self._raise_watch.timeout.connect(self._autostart_raise_tick)
        self._raise_watch.start()

    def _autostart_raise_tick(self) -> None:
        import time

        from core.health import autostart_raise_done, traktor_frontmost

        now = time.monotonic()
        if autostart_raise_done(
            elapsed_s=now - self._raise_watch_started,
            first_ping_age_s=(
                now - self._first_ping_ts if self._first_ping_ts is not None else None
            ),
        ):
            self._raise_watch.stop()
            return
        if traktor_frontmost():
            self._force_raise()

    def _try_initial_snap(self) -> None:
        """Snap this window over Traktor's library — retry while Traktor opens.

        Mirrors the manual double-click-on-album-art behaviour. Gives up after
        the retry budget (~30 s, extended to ~2 min when we auto-started
        Traktor) so we don't keep polling forever when Traktor isn't running.
        """
        from PySide6.QtCore import QTimer

        from core.drag import snap_over_traktor_library

        if self._initial_snap_done:
            return
        if snap_over_traktor_library(self):
            self._initial_snap_done = True
            self.now_tab.mark_snapped()
            if self._autostarted_traktor:
                # Traktor's launch may have foregrounded its window — put
                # Kommander back on top for a fluid start.
                self._force_raise()
            return
        self._initial_snap_attempts += 1
        if self._initial_snap_attempts < self._initial_snap_budget:
            QTimer.singleShot(3000, self._try_initial_snap)
