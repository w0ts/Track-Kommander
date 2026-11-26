"""The window's health notices, update check and display wake lock.

These three share one timer and one set of state (the banner's notices, the
wake policy), and none of them touches the layout, docking or the deck
controls. They were methods of ``MainWindow`` and stay so: this is a mixin that
``MainWindow`` inherits, so ``self`` and every attribute the methods use are
unchanged. ``MainWindow.__init__`` still creates the state and starts the
timers.
"""

from __future__ import annotations

import logging
import threading

from PySide6.QtCore import QObject, Signal

from core.prefs import KEEP_DISPLAY_AWAKE
from core.runtime import in_traktor


class _UpdateRelay(QObject):
    """Carries the update check's answer from its thread to the GUI thread."""

    found = Signal(object)  # core.update_check.Release


class HealthMixin:
    """Health notices, the update check and the display wake lock."""

    # ── Health notices ────────────────────────────────────────────────────────

    def _start_health_check(self) -> None:
        """Warn the user when the Traktor connection or setup is broken.

        Every 5 s, sample runtime state and let ``core.health`` decide which
        notices belong in the banner. Silent while Traktor isn't running (per
        product decision), so there is no noise when the user isn't DJing.
        Cross-platform: the Traktor probe dispatches per OS, and the macOS
        permission checks report granted on Windows.
        """
        from PySide6.QtCore import QTimer

        self._health_timer = QTimer(self)
        self._health_timer.setInterval(5000)
        self._health_timer.timeout.connect(self._check_health)
        self._health_timer.start()

    def _check_health(self) -> None:
        import time

        from core.health import evaluate_health, traktor_running
        from core.permissions import (
            accessibility_granted,
            screen_recording_granted,
        )

        now = time.monotonic()
        up = traktor_running()

        # The display is held awake only while something is playing; with
        # Traktor gone there is nothing to read, so the policy is cleared
        # outright rather than left to run out its grace.
        if not up:
            self._wake_policy.traktor_gone()
        self._refresh_wake_lock()

        if not up:
            # Traktor closed: clear state so a later relaunch starts a fresh
            # grace period and counts as "never connected" (not "stopped"),
            # and skip the permission probes entirely — policy is silence.
            self._traktor_up_since = None
            self._last_ping_ts = None
            self._first_ping_ts = None
            self.notice_banner.set_notices(self._update_notices)
            # The banner stays silent while Traktor is closed, but Settings is
            # where the user goes to ask what is wrong — tell it the truth.
            self.settings_tab.set_notices((), traktor_up=False)
            return
        if self._traktor_up_since is None:
            self._traktor_up_since = now

        # Keep the "last message Xs ago" readout ageing even when nothing is
        # arriving — a frozen counter is exactly what a stalled bridge looks
        # like, and that has to be visible rather than inferred.
        self.settings_tab.set_bridge_stats(
            self._ping_count,
            now - self._last_ping_ts if self._last_ping_ts is not None else None,
        )

        # Probed once and handed to Settings too, which would otherwise probe
        # them again for the same answer.
        accessibility = accessibility_granted()
        screen_recording = screen_recording_granted()
        notices = evaluate_health(
            traktor_up=up,
            traktor_up_for_s=(now - self._traktor_up_since if self._traktor_up_since else 0.0),
            webhook_ok=self._webhook_ok,
            webhook_error=self._webhook_error,
            ping_age_s=(now - self._last_ping_ts if self._last_ping_ts is not None else None),
            accessibility=accessibility,
            screen_recording=screen_recording,
            inside_traktor=in_traktor(),
        )
        self.notice_banner.set_notices((*notices, *self._update_notices))
        self.settings_tab.set_notices(
            notices, traktor_up=True, permissions=(accessibility, screen_recording)
        )

    def _on_deck_state(self, deck: str, playing: bool) -> None:
        """A deck's play state from the bridge — the wake lock follows it."""
        import time

        self._wake_policy.deck(deck, playing, time.monotonic())
        self._refresh_wake_lock()

    def _refresh_wake_lock(self) -> None:
        """Reflect the policy (and the preference) into the OS assertion."""
        import time

        keep_awake = KEEP_DISPLAY_AWAKE.get()
        self._wake_lock.set_held(self._wake_policy.wanted(time.monotonic(), enabled=keep_awake))

    # ── Update check ──────────────────────────────────────────────────────────

    #: Seconds after launch before asking GitHub — off the startup path.
    _UPDATE_CHECK_DELAY_MS = 8000

    def _start_update_check(self) -> None:
        """Once per launch, unless turned off in Settings ▸ General. An add-on's notices
        (core.extensions) are asked either way: they need no network."""
        from PySide6.QtCore import QSettings, QTimer

        from core.extensions import mismatch_notices
        from core.log_bundle import app_version
        from core.update_check import SETTING_DEFAULT, SETTING_KEY

        self._addon_notices = (
            *mismatch_notices(app_version()),
            *self._extension_notices(app_version()),
        )
        self._update_notices = self._addon_notices

        if not QSettings().value(SETTING_KEY, SETTING_DEFAULT, type=bool):
            logging.getLogger(__name__).info("update check: off in Settings")
            return
        QTimer.singleShot(self._UPDATE_CHECK_DELAY_MS, self._run_update_check)

    def _run_update_check(self) -> None:
        """Ask GitHub on a daemon thread, answering through a signal.

        Not a QThread: the request blocks in urlopen for up to 10 s and cannot
        be interrupted, and Qt aborts the process if a QThread is destroyed
        while it runs — which quitting during those 10 s did. A daemon thread
        simply ends with the process. The relay is owned by this window, so
        the thread never holds its last reference: a relay freed by the thread
        while its answer was delivered crashed 0.0.8 (about_page._check_now).
        Created here, it lives on the GUI thread and the queued emit delivers
        the answer there.
        """
        from core import update_check
        from core.log_bundle import app_version

        relay = _UpdateRelay(self)
        relay.found.connect(self._on_update_found)
        current = app_version()

        def ask() -> None:
            release = update_check.check_for_update(current)
            if release is None:
                return
            try:
                relay.found.emit(release)
            except RuntimeError:  # TK quit while GitHub was asked
                logging.getLogger(__name__).info("update check: answered after quit")

        self._update_relay = relay
        threading.Thread(target=ask, name="update-check", daemon=True).start()

    def _on_update_found(self, release) -> None:
        from core import self_update
        from core.health import Notice
        from core.log_bundle import app_version

        self._update_release = release
        installable = release.installer is not None and self_update.supported()
        self._update_notices = (
            Notice(
                key=f"update-{release.version}",
                severity="info",
                text=f"Track Kommander {release.version} is out — you are running {app_version()}.",
                action="update",
                action_label="Update…" if installable else "Download",
            ),
            *self._addon_notices,
        )
        self._check_health()  # show it now, not on the next tick

    def offer_update(self, release) -> None:
        """A newer release found on request (Settings ▸ About): show it in the
        banner like the startup check does, and go straight to installing."""
        self._on_update_found(release)
        self._on_notice_action("update")

    @staticmethod
    def _extension_notices(version: str) -> tuple:
        from core.extensions import notice_sources

        notices = []
        for source in notice_sources():
            try:
                notice = source(version)
            except Exception:
                logging.getLogger(__name__).exception("an add-on's notice failed")
                continue
            if notice is not None:
                notices.append(notice)
        return tuple(notices)

    def _on_notice_action(self, action: str) -> None:
        """Handle a notice's action button: an add-on's first (core.extensions), then TK's."""
        if self._extension_action(action):
            return
        if action == "addon_page":  # core.addon_finder.PAGE_ACTION: the matching add-on's page
            from core.extensions import mismatched_page
            from ui.self_update_flow import open_page

            if mismatched_page():
                open_page(mismatched_page())
            return
        if action == "update" and self._update_release is not None:
            from ui.self_update_flow import run_update

            run_update(self, self._update_release, quit_app=self.close)
        elif action == "settings":
            self._open_settings()
        elif action == "accessibility":
            from core.permissions import (
                open_accessibility_settings,
                request_accessibility,
            )

            request_accessibility()
            open_accessibility_settings()
        elif action == "screen_recording":
            from core.permissions import (
                open_screen_recording_settings,
                request_screen_recording,
            )

            request_screen_recording()
            open_screen_recording_settings()
