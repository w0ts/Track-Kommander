"""The application's main window.

``main`` (the entry point) lives in :mod:`ui.app`; it is re-exported here so the
entry script and older imports keep working.
"""

from __future__ import annotations

import logging
import sys
import time

from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QVBoxLayout,
    QWidget,
)

from core import actions, keyforward, profile
from core.idle import DisplayWakeLock, PlaybackWakePolicy
from ui.app import main
from ui.auto_mode_popup import AutoModePopup
from ui.library import LibraryTab
from ui.matching_popup import MatchingPopup
from ui.mw_calibration import (
    _CALIBRATE_FALLBACK_MS,
    _RECALIBRATE_SETTLE_MS,
    CalibrationMixin,
)
from ui.mw_docking import _HOVER_POLL_MS, _TRAKTOR_VISIBLE_POLL_MS, DockingMixin
from ui.mw_health import HealthMixin
from ui.mw_keys import build_action_handlers, owns_key_input
from ui.mw_native import NativeBandMixin
from ui.mw_traktor_start import TraktorStartMixin
from ui.native_list import (
    NativeAutoMirror,
    NativeCardsMirror,
    NativeDeckMirror,
    NativeListPusher,
)
from ui.now_playing import NO_TRANSPOSE, NowPlayingTab
from ui.panel_popup import PanelPopup
from ui.settings import SettingsTab
from ui.worker_shutdown import GIVE_UP_AFTER_S, may_close, stop_and_wait
from workers.s4mk2_watcher import S4MK2Watcher, s4mk2_connected
from workers.webhook import WebhookWatcher

__all__ = ["MainWindow", "build_action_handlers", "main", "owns_key_input"]


#: How long a quit waits, the first time, for each worker to stop.
_QUIT_WAIT_MS = 3000

#: How often a quit that is waiting for a worker looks again.
_QUIT_RETRY_MS = 250

#: How often an S4 that is switched on but not plugged in is looked for.
_S4_LOOK_AGAIN_MS = 5000


class MainWindow(
    CalibrationMixin, DockingMixin, HealthMixin, NativeBandMixin, TraktorStartMixin, QMainWindow
):
    # reason, saved offsets, started-at: a deck detection finished (any thread).
    _calibrated = Signal(str, object, float)
    # done-callback, reading: a LayoutWatch probe finished (any thread).
    _layout_read = Signal(object, object)

    def __init__(self, *, integrated: bool = False):
        """*integrated*: TK runs INSIDE Traktor (traktor-native, macOS only). This window is
        then never shown, so everything that only serves a window over Traktor — docking,
        snapping, app-switch and hover tracking, the deck screen-captures for drag-and-drop
        (loads are native there), starting Traktor — is left out."""
        super().__init__()
        from core.extensions import load_addons

        load_addons()  # before the tabs, pages and band are built: they show what it adds
        self._integrated = integrated
        self.setWindowTitle(profile.app_name())  # a beta copy says so
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint)
        self.resize(1000, 720)
        self._watcher: WebhookWatcher | None = None
        self._s4mk2: S4MK2Watcher | None = None
        self._stats_tab = None  # built the first time Stats is opened
        self._last_auto_calibrate: float = 0.0
        self._calibrating = False
        # A detection asked for while one runs: (reason, force), run after it.
        self._calibrate_pending: tuple[str, bool] | None = None
        self._calibrated.connect(self._on_calibrated)
        self._layout_read.connect(lambda done, frac: done(frac))
        # Health-check state — sampled by _check_health on a timer.
        self._last_ping_ts: float | None = None  # monotonic ts of last D2 POST
        self._first_ping_ts: float | None = None  # monotonic ts of first D2 POST
        self._webhook_ok: bool = False
        self._webhook_error: str | None = None
        self._traktor_up_since: float | None = None  # monotonic ts first seen up
        # Count of valid POSTs received from the D2 bridge. Surfaced in
        # Settings: "Connected" only means a ping arrived once, while this says
        # whether traffic is still flowing.
        self._ping_count: int = 0
        self._port: int = profile.webhook_port()  # a beta copy has its own, not 12345
        # Keeps the display awake while Traktor is *playing* — a set can run
        # for an hour without the mouse moving, and the screensaver would
        # cover the thing the DJ is reading. Not merely while Traktor is up:
        # that kept the screen lit all evening for an idle Traktor. The
        # policy is fed from the bridge's deck states and re-read every
        # health tick, so a stopped bridge or a changed preference is noticed
        # within seconds.
        self._wake_lock = DisplayWakeLock()
        self._wake_policy = PlaybackWakePolicy()
        self._build_ui()
        self._apply_connection(self._port)
        self._apply_s4mk2()
        self._start_health_check()
        # However the app quits — the window closed or not — the threads stop.
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self._on_about_to_quit)
        # A newer release, once the startup check finds one: shown beside the
        # health notices on every tick — including while Traktor is closed,
        # since it is not a health warning.
        # An add-on's notices (an out-of-date add-on) are told the same way.
        self._update_notices: tuple = ()
        self._update_release = None  # core.update_check.Release, once found
        self._addon_notices: tuple = ()
        self._start_update_check()

        # Auto-detect deck letters once on startup, then every 15 s. Traktor
        # doesn't expose GUI layout via AppProperty (verified by probing every
        # AppProperty path NI uses in their own QML), so a screenshot poll is
        # the only way to catch a 2-deck ↔ 4-deck switch. 15 s strikes a
        # balance between responsiveness and CPU; the function early-exits
        # after a 5 s success-cooldown.
        #
        # Screenshot calibration + window auto-snap use the platform window
        # backend (Quartz on macOS, Win32 in core/drag_win.py on Windows). Both
        # are supported, so schedule the timers on either; on Linux (no backend)
        # they'd just log "Traktor not found", so skip them there.
        self._initial_snap_done = False
        self._initial_snap_attempts = 0
        self._initial_snap_budget = 10  # × 3 s retry interval = 30 s
        self._autostarted_traktor = False
        #: Docked = sitting where the library snap put it. Docked, the cover
        #: art is framed, and while Traktor can be seen — on screen, and it
        #: or this app is the active one — the window is kept always on top:
        #: it reads as the bottom part of Traktor. Nothing reacts to clicks:
        #: clicking Traktor gives Traktor focus and this window stays above.
        self._docked = False
        self._on_top = False
        from PySide6.QtCore import QTimer as _QTimer

        # Traktor being hidden, minimised, quit or covered by another app has
        # no event we can rely on across both platforms; a cheap check a few
        # times a second, run only while docked, covers them all.
        self._traktor_visible_timer = _QTimer(self)
        self._traktor_visible_timer.setInterval(_TRAKTOR_VISIBLE_POLL_MS)
        self._traktor_visible_timer.timeout.connect(self._update_on_top)
        self._traktor_rect: tuple[float, float, float, float] | None = None
        #: The last app other than this one seen in front: "traktor" or
        #: "other". Coming back from another app (an editor) while docked
        #: brings Traktor up behind this window; coming back from Traktor
        #: needs nothing — it is right behind already, and pairing then would
        #: pull it forward every time the pointer crosses back.
        self._last_front = "traktor"
        if integrated:
            return
        QApplication.instance().applicationStateChanged.connect(self._on_app_state)
        # The same decision the moment the active app changes, instead of up
        # to 300 ms later — clicking Traktor over an editor otherwise leaves
        # this window covered until the next check, which stays as the net.
        from ui.app_switch_watch import AppSwitchWatch

        self._app_switch_watch = AppSwitchWatch(self._on_app_switch)
        self._app_switch_watch.start()
        self._hover_timer = _QTimer(self)
        self._hover_timer.setInterval(_HOVER_POLL_MS)
        self._hover_timer.timeout.connect(self._hover_focus_tick)
        # When Kommander comes back from hidden (macOS Cmd+H) or minimized,
        # bring Traktor back up with it and re-raise Kommander on top —
        # the pair reappears together. Windows/minimize is caught in
        # changeEvent; the macOS Cmd+H hide needs an AppKit observer
        # because Qt never sees it.
        self._unhide_observer: object | None = None
        if sys.platform == "darwin":
            from ui.mac_unhide import install_unhide_observer

            self._unhide_observer = install_unhide_observer(self._on_app_unhidden)

        if sys.platform in ("darwin", "win32"):
            from PySide6.QtCore import QTimer

            self._maybe_autostart_traktor()
            QTimer.singleShot(3000, lambda: self._auto_calibrate_decks("startup"))
            QTimer.singleShot(5000, self._start_extensions)

            # Auto-snap the window over Traktor's library on startup — same
            # action the user gets by double-clicking the album art. Retries a
            # few times because Traktor may still be opening when we start.
            QTimer.singleShot(1500, self._try_initial_snap)
            # Deck drop positions are re-detected when Traktor's window moves
            # or its browser shifts (see _schedule_recalibrate); this slow
            # timer is only the fallback — undocked, or a change missed.
            self._calibrate_timer = QTimer(self)
            self._calibrate_timer.setInterval(_CALIBRATE_FALLBACK_MS)
            self._calibrate_timer.timeout.connect(lambda: self._auto_calibrate_decks("fallback"))
            self._calibrate_timer.start()
            self._recalibrate_timer = QTimer(self)
            self._recalibrate_timer.setSingleShot(True)
            self._recalibrate_timer.setInterval(_RECALIBRATE_SETTLE_MS)
            self._recalibrate_timer.timeout.connect(
                lambda: self._auto_calibrate_decks(self._recalibrate_reason, force=True)
            )
            self._recalibrate_reason = ""

            # Traktor's layout (2↔4 decks, deck sizes) moves the browser; the
            # snapped overlay follows it. Cheap: one window capture and a row
            # scan, only while snapped.
            from ui.layout_watch import LayoutWatch

            class _Snapped:
                def is_snapped(_s) -> bool:
                    return self.now_tab.is_snapped()

                def resnap(_s) -> None:
                    self.now_tab.resnap_library_overlay()
                    # The browser moved, so the decks above it did too.
                    self._schedule_recalibrate("Traktor layout changed")

            self._layout_watch = LayoutWatch(_Snapped(), run_probe=self._run_layout_probe)
            self._layout_timer = QTimer(self)
            self._layout_timer.setInterval(3_000)
            self._layout_timer.timeout.connect(self._layout_watch.tick)
            self._layout_timer.start()

    def _build_ui(self) -> None:
        self.now_tab = NowPlayingTab()
        self.now_tab.show_cover_art = not self._integrated  # see NowPlayingTab.show_cover_art
        self.lib_tab = LibraryTab()
        self.settings_tab = SettingsTab()

        # Notice banner (connection/permission warnings) stacked above the
        # Now Playing view. Hidden when there is nothing to warn about.
        from ui.notices import NoticeBanner

        self.notice_banner = NoticeBanner(on_action=self._on_notice_action)
        central = QWidget()
        central_lay = QVBoxLayout(central)
        central_lay.setContentsMargins(0, 0, 0, 0)
        central_lay.setSpacing(0)
        central_lay.addWidget(self.notice_banner)
        # Settings / Library / Stats open as pages in place of Now Playing:
        # separate dialogs kept ending up under the docked, on-top window.
        from ui.panel_stack import PanelStack

        self._panels = PanelStack(self.now_tab)
        central_lay.addWidget(self._panels, stretch=1)
        self.setCentralWidget(central)
        self.now_tab.notice_requested.connect(self.notice_banner.show_transient)

        self.now_tab.load_to_deck_requested.connect(self._handle_load_to_deck)
        self.now_tab.stop_deck_requested.connect(self._handle_stop_deck)
        self.now_tab.play_deck_requested.connect(self._handle_play_deck)
        self.now_tab.cancel_automix_requested.connect(self._handle_cancel_automix)
        self.now_tab.hotcue_requested.connect(self._handle_hotcue)
        self.now_tab.beatjump_requested.connect(self._handle_beatjump)
        self.now_tab.open_library_requested.connect(self._open_library)
        self.now_tab.open_settings_requested.connect(self._open_settings)
        self.now_tab.open_stats_requested.connect(self._open_stats)
        self.now_tab.start_traktor_requested.connect(self._start_traktor_and_snap)
        self.now_tab.snap_changed.connect(self._update_docked)
        self.now_tab.minimize_requested.connect(self._collapse_to_button)
        from workers import native_columns

        self._native_list = NativeListPusher(
            self.now_tab.result_paths,
            self,
            # TK SCORE in Traktor's list (only does something inside Traktor).
            also=lambda: native_columns.set_scores(self.now_tab.result_scores()),
        )
        self._matching_popup: MatchingPopup | None = None  # opened from Traktor's band
        self._auto_mode_popup: AutoModePopup | None = None  # the AUTO button in Traktor's band
        self._panel_popups: dict[str, PanelPopup] = {}  # Settings/Library/Stats from the band
        self.now_tab.results_changed.connect(self._native_list.results_changed)
        self._native_deck = NativeDeckMirror()
        self.now_tab.source_deck_changed.connect(self._native_deck.changed)
        self._native_cards = NativeCardsMirror(self.now_tab.axis_card_states, self)
        self._native_auto = NativeAutoMirror()
        self.now_tab.auto_state_changed.connect(self._native_auto.changed)
        # The tab announced its first state while it was being built, before
        # these were connected: start the mirrors from it.
        self._native_deck.deck = getattr(self.now_tab, "_announced_source", "") or ""
        mode, busy = getattr(self.now_tab, "_announced_auto", ("off", False))
        self._native_auto.mode, self._native_auto.busy = mode, busy
        self._mini_button = None
        self._shut = False  # _shut_down has run
        self._mini_follows_traktor = True  # set when minimised (ui.mw_docking)
        self._collapsed_docked = False
        self.now_tab.recompute_hotcues_requested.connect(self.lib_tab.recompute_hotcues_for)
        self.now_tab.discogs_lookup_requested.connect(self.lib_tab.lookup_discogs_for)
        self.now_tab.grid_fix_captured.connect(self.lib_tab.grid_fix_captured)

        self.settings_tab.s4mk2_config_changed.connect(self._apply_s4mk2)
        self.settings_tab.matching_config_changed.connect(self.now_tab.refresh_matching)
        self.settings_tab.columns_config_changed.connect(self.now_tab.reload_result_columns)
        self.settings_tab.reconnect_requested.connect(self._reconnect)
        self.settings_tab.tooltip_config_changed.connect(self.now_tab.reload_tooltip_duration)
        self.settings_tab.traktor_config_changed.connect(self.lib_tab.refresh_traktor_config)
        self.lib_tab.check_library_requested.connect(self._open_library_checker)
        self.lib_tab.library_changed.connect(self._on_library_changed)

        # Keyboard input for the whole app: run the few actions Kommander owns
        # and hand every other key to Traktor. An app-level event filter rather
        # than QShortcut, because a QShortcut resolves before the KeyPress is
        # delivered — which is how a binding once shadowed another silently —
        # and because the release has to be forwarded too (see core.keyforward).
        QApplication.instance().installEventFilter(self)

        # Cmd+F (macOS) / Ctrl+F → focus the filter box.
        find_sc = QShortcut(QKeySequence(QKeySequence.StandardKey.Find), self)
        find_sc.setContext(Qt.ShortcutContext.ApplicationShortcut)
        find_sc.activated.connect(self.now_tab.focus_search)

        # Cmd+, (macOS) / Ctrl+, → open Settings.
        # Use a QAction with PreferencesRole so macOS hoists it into the
        # application menu and binds the standard OS shortcut natively.
        prefs_action = QAction("Settings…", self)
        prefs_action.setShortcut(QKeySequence(QKeySequence.StandardKey.Preferences))
        prefs_action.setMenuRole(QAction.MenuRole.PreferencesRole)
        prefs_action.triggered.connect(self._open_settings)
        self.menuBar().addAction(prefs_action)
        # Also register a window-level shortcut as a fallback for non-mac
        # platforms (where there's no automatic menu hoisting).
        self.addAction(prefs_action)

        # Cmd/Ctrl+Shift+S → library statistics dashboard.
        stats_action = QAction("Library Stats…", self)
        stats_action.setShortcut(QKeySequence("Ctrl+Shift+S"))
        stats_action.triggered.connect(self._open_stats)
        self.menuBar().addAction(stats_action)
        self.addAction(stats_action)

        # Debug menu: the logs, for when something goes wrong on a machine
        # we cannot sit at. Settings → General has the same two buttons for
        # Windows, where this menu bar is hidden.
        from ui.log_actions import share_log, show_log_folder

        debug_menu = self.menuBar().addMenu("Debug")
        show_log_action = QAction("Show Log Folder", self)
        show_log_action.triggered.connect(lambda: show_log_folder(self))
        debug_menu.addAction(show_log_action)
        share_log_action = QAction("Share Log…", self)
        share_log_action.triggered.connect(lambda: share_log(self))
        debug_menu.addAction(share_log_action)

        # The menu-bar actions exist only so macOS hoists them into the native
        # app menu; elsewhere QMenuBar is an in-window widget that would render
        # them as stray buttons. The shortcuts stay active via addAction above.
        if sys.platform != "darwin":
            self.menuBar().setVisible(False)

        # Hide the status bar
        self.statusBar().setVisible(False)

    # ── Panel dialogs ─────────────────────────────────────────────────────────

    def _open_library(self) -> None:
        self._panels.toggle_panel("library", "Library", self.lib_tab)

    def _open_library_checker(self, paths: list) -> None:
        from ui.dialogs import warn
        from ui.library_checker import LibraryCheckerDialog

        if self._watcher is None:
            warn(
                self,
                "Webhook not running",
                "The Traktor webhook isn't connected — start it from Settings first.",
            )
            return
        dlg = LibraryCheckerDialog(self._watcher, paths, parent=self)
        dlg.show()

    def _open_settings(self) -> None:
        self._panels.toggle_panel("settings", "Settings", self.settings_tab)

    def _open_stats(self) -> None:
        self._panels.toggle_panel("stats", "Metrics & Stats", self._stats_page())

    def _stats_page(self) -> QWidget:
        """The Metrics & Stats page, built the first time it is wanted."""
        from ui.stats import StatsTab

        if self._stats_tab is None:
            self._stats_tab = StatsTab()
            # Toggling a feature in the discriminative list enables/disables it
            # as a matching axis in the now-playing panel.
            self._stats_tab.metric_toggled.connect(self.now_tab.set_metric_enabled)
            self._stats_tab.set_axes_provider(self.now_tab.current_axes)
        return self._stats_tab

    def _on_library_changed(self) -> None:
        """A scan wrote new features: the cached Stats page reloads the next
        time it is shown rather than showing the library as it was."""
        if self._stats_tab is not None:
            self._stats_tab.mark_stale()

    # ── Watcher management ────────────────────────────────────────────────────

    def _apply_connection(self, port: int) -> None:
        if self._watcher is not None:
            self._watcher.stop()
            self._watcher.wait(3000)
            self._watcher = None

        w = WebhookWatcher(port=port)
        w.track_changed.connect(self._on_track_changed)
        w.status.connect(self._on_watcher_status)
        w.is_running.connect(self._on_watcher_running)
        w.deck_state.connect(self.now_tab.set_deck_playing)
        w.deck_state.connect(self._on_deck_state)
        w.deck_loaded.connect(self.now_tab.update_deck)
        w.deck_loaded.connect(self._on_deck_loaded)
        w.deck_on_air.connect(self.now_tab.update_deck_on_air)
        w.deck_position.connect(self.now_tab.update_deck_position)
        w.deck_phase.connect(self.now_tab.update_deck_phase)
        w.deck_key_adjust.connect(self.now_tab.update_deck_key_adjust)
        w.deck_tempo.connect(self.now_tab.update_deck_tempo)
        w.master_tempo.connect(self.now_tab.update_master_tempo)
        w.traktor_ping.connect(self._on_traktor_ping)
        w.d2_version.connect(self.settings_tab.set_d2_version)
        w.layout_changed.connect(self._on_layout_changed)
        w.show_requested.connect(self._on_show_requested)
        w.native_track.connect(self._on_native_track)
        w.ui_action.connect(self._on_ui_action)
        w.ui_card.connect(self._on_ui_card)
        # Requests from the UI (stop, play, load, hotcues …) are not wired to
        # the watcher here: __init__ connects them once to handlers that read
        # self._watcher when they run. Connecting them here too queued every
        # stop twice, and one more time per reconnect.

        self._watcher = w
        w.start()
        if self._integrated:
            self._attach_native_decks(w)  # inside Traktor: no D2 bridge needed

    def _on_track_changed(self, track: str) -> None:
        self.now_tab.set_track(track)

    def _on_deck_loaded(self, deck: str, info: dict) -> None:
        # If a deck reports loaded but has no per-deck calibration, the user
        # has likely switched to a layout that exposes new decks (e.g. 2-deck
        # → 4-deck). Re-run silent detection so C/D get pinned.
        from PySide6.QtCore import QSettings

        s = QSettings()
        if s.value(f"drag_deck_{deck}_rx") is None:
            self._auto_calibrate_decks(reason=f"new deck {deck}")

        # Sync armed on an empty deck does not reliably survive the load, so
        # re-arm the deck that just got a track.
        self._arm_sync(deck)

    def moveEvent(self, event) -> None:
        super().moveEvent(event)
        self._update_docked()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._update_docked()

    def changeEvent(self, event) -> None:
        # Restore-from-minimized (taskbar/Dock click) is the Windows
        # counterpart of the macOS unhide — treat it the same way.
        if event.type() == QEvent.Type.WindowStateChange:
            was_min = bool(event.oldState() & Qt.WindowState.WindowMinimized)
            now_min = bool(self.windowState() & Qt.WindowState.WindowMinimized)
            if was_min and not now_min:
                self._on_app_unhidden()
        super().changeEvent(event)

    def _on_watcher_status(self, msg: str) -> None:
        positive_words = ("started", "listening", "watching", "found", "port")
        ok = any(w in msg.lower() for w in positive_words)
        self._webhook_error = None if ok else msg
        self.settings_tab.set_status(ok, msg)

    def _on_watcher_running(self, running: bool) -> None:
        self._webhook_ok = running
        cur_msg = self.settings_tab.status_msg.text()
        self.settings_tab.set_status(running, cur_msg)

    def _on_traktor_ping(self) -> None:
        import time

        reconnected = self._last_ping_ts is None
        self._last_ping_ts = time.monotonic()
        if self._first_ping_ts is None:
            self._first_ping_ts = self._last_ping_ts
        if reconnected:
            self._arm_sync()
        self._ping_count += 1
        self.settings_tab.set_traktor_active(True)
        self.settings_tab.set_bridge_stats(self._ping_count, 0.0)

    def _arm_sync(self, deck: str | None = None) -> None:
        """Turn Traktor's Sync on, if the user asked for that.

        Traktor's Sync is per deck with no way to arm all of them, so the user
        otherwise has to press four buttons at the start of every session.
        ``deck=None`` arms all four; passing one arms just that deck.
        """
        from PySide6.QtCore import QSettings

        from ui.settings import SYNC_ALL_ON_CONNECT_KEY

        if self._watcher is None:
            return
        if not QSettings().value(SYNC_ALL_ON_CONNECT_KEY, False, type=bool):
            return
        self._watcher.queue_sync(deck)

    def _reconnect(self) -> None:
        """Rebind the webhook server and ask the bridge to re-send its state.

        A user-driven recovery for a connection that has gone quiet, so neither
        Kommander nor Traktor has to be restarted to get it back.
        """
        logging.getLogger(__name__).info(
            "reconnect requested by user — rebinding webhook on port %d",
            self._port,
        )
        self._apply_connection(self._port)
        if self._watcher is not None:
            self._watcher.request_resync()
        self._arm_sync()

    def _handle_stop_deck(self, deck: str) -> None:
        if self._watcher:
            self._watcher.queue_stop(deck)

    def _handle_play_deck(self, deck: str) -> None:
        if self._watcher:
            self._watcher.queue_play(deck)

    def _handle_cancel_automix(self) -> None:
        if self._watcher:
            self._watcher.queue_cancel_automix()

    def _handle_hotcue(self, deck: str, index: int) -> None:
        if self._watcher:
            self._watcher.queue_hotcue(deck, index)

    def _handle_beatjump(self, deck: str, value: int) -> None:
        if self._watcher:
            self._watcher.queue_beatjump(deck, value)

    def _handle_load_to_deck(
        self, path: str, deck: str, skip_load: bool = False, transpose: int = NO_TRANSPOSE
    ) -> None:
        if self._watcher:
            self._watcher.queue_load(
                path,
                deck,
                skip_load=skip_load,
                transpose=None if transpose == NO_TRANSPOSE else transpose,
            )
        else:
            cb = QApplication.clipboard()
            if cb:
                cb.setText(path)

    def _apply_s4mk2(self) -> None:
        """Start or stop the S4 MK2 watcher based on current settings."""
        from PySide6.QtCore import QSettings

        if self._s4mk2 is not None:
            self._s4mk2.stop()
            self._s4mk2.wait(2000)
            self._s4mk2 = None

        if getattr(self, "_integrated", False):
            # Inside Traktor the S4 is driven by Traktor itself (User, 2026-11-24): TK's own
            # HID reading of it, and its settings, are not needed there.
            self.settings_tab.hide_hardware_group()
            return
        if not QSettings().value("s4mk2_enabled", False, type=bool):
            self.settings_tab.set_s4mk2_status(False, "Disabled")
            return

        if not s4mk2_connected():
            self.settings_tab.set_s4mk2_status(False, "Device not found — is it plugged in?")
            # Look again: a controller plugged in after the app started used
            # to stay unseen until the setting was switched off and on. One
            # look at a time, however often the setting is touched meanwhile.
            if not getattr(self, "_s4_looking", False):
                self._s4_looking = True
                QTimer.singleShot(_S4_LOOK_AGAIN_MS, self._look_for_s4_again)
            return

        w = S4MK2Watcher()
        # Controls resolve through the action registry rather than being wired
        # to fixed behaviour, so the S4 mapping is configurable.
        w.control.connect(self._on_s4_control)
        w.learned.connect(self.settings_tab.on_s4_control_learned)
        # Whether it is connected is the watcher's to say, and to go on
        # saying: it knows when the open failed and when the device went.
        w.link.connect(self.settings_tab.set_s4mk2_status)
        self.settings_tab.set_s4mk2_status(False, "Connecting…")
        w.start()
        self._s4mk2 = w
        self.settings_tab.set_s4_watcher(w)

    def _look_for_s4_again(self) -> None:
        self._s4_looking = False
        self._apply_s4mk2()

    def _run_action(self, action_id: str) -> bool:
        handler = build_action_handlers(self.now_tab).get(action_id)
        if handler is None:
            logging.getLogger(__name__).debug("no handler for action %s", action_id)
            return False
        handler()
        return True

    def eventFilter(self, obj, event) -> bool:
        etype = event.type()
        if etype == QEvent.Type.Show and getattr(self, "_on_top", False):
            self._lift_dialog(obj)
        # A key we forwarded is Traktor's until it comes back up: the release
        # belongs to the press, wherever focus has wandered to meanwhile.
        if etype == QEvent.Type.KeyRelease and not event.isAutoRepeat():
            vk = int(event.nativeVirtualKey())
            if keyforward.is_held(vk):
                keyforward.post_key(vk, int(event.modifiers().value), down=False)
                return True
        # Kommander is about to stop seeing the keyboard — no release will
        # arrive for anything still down, so let Traktor go.
        if etype in (QEvent.Type.ApplicationDeactivate, QEvent.Type.WindowDeactivate):
            keyforward.release_all()
        if etype == QEvent.Type.KeyPress and not event.isAutoRepeat():
            if not owns_key_input(QApplication.focusWidget()):
                # Kommander claims only what it needs — moving through the
                # result list and loading a deck.
                action_id = actions.match(event)
                if action_id is not None and self._run_action(action_id):
                    return True
                # Everything else goes to Traktor. Kommander sits on top and
                # holds focus, so without this the DJ's own Traktor mapping is
                # simply swallowed while they browse.
                if keyforward.should_forward(event) and keyforward.post_key(
                    int(event.nativeVirtualKey()),
                    int(event.modifiers().value),
                    down=True,
                ):
                    return True
        return super().eventFilter(obj, event)

    def _lift_dialog(self, obj) -> None:
        """A dialog shown while the window floats over Traktor floats too.
        Qt was meant to keep a dialog at its parent's level, but the Discogs
        review (parented to the Library page) opened underneath, 2026-10-04."""
        from PySide6.QtWidgets import QDialog, QMessageBox

        if obj is self or not isinstance(obj, (QDialog, QMessageBox)) or not obj.isWindow():
            return
        from ui.window_level import set_always_on_top

        QTimer.singleShot(0, lambda: set_always_on_top(obj, True))  # once its window exists

    def _on_s4_control(self, control: str) -> None:
        """An S4 MK2 control was pressed/turned — route it through the registry."""
        action_id = actions.match_s4(control)
        if action_id is not None:
            self._run_action(action_id)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        # DefWindowProc ignores the SC_SIZE command our WM_NCHITTEST answers
        # trigger unless the window style carries WS_THICKFRAME, which
        # FramelessWindowHint strips. Reapplied on every show because Qt
        # recreates the HWND (with fresh styles) on setWindowFlags changes.
        if sys.platform == "win32":
            from ui.win_resize import enable_sizing_frame

            enable_sizing_frame(int(self.winId()))
        # A recreated native window starts at the normal level.
        if getattr(self, "_on_top", False):
            from ui.window_level import set_always_on_top

            set_always_on_top(self, True)

    def nativeEvent(self, event_type, message):
        # Frameless windows have no native resize borders on Windows (macOS
        # keeps its resizable style mask); answer WM_NCHITTEST along the edges
        # so the OS supplies cursors, the sizing loop, and Aero snap.
        if (
            sys.platform == "win32"
            and event_type == b"windows_generic_MSG"
            and not (self.isMaximized() or self.isFullScreen())
        ):
            from ui.win_resize import hit_test_edges

            ht = hit_test_edges(int(message))
            if ht is not None:
                return True, ht
        return super().nativeEvent(event_type, message)

    def closeEvent(self, event) -> None:
        # Flush any pending settings (matching axis state, BPM restrict, column
        # widths) to disk before teardown so an abrupt quit can't lose the last
        # change made within the debounce window.
        self.now_tab.flush_settings()

        # Stop analysis workers first — they own ProcessPoolExecutors that must
        # be shut down before the process exits, otherwise worker processes are
        # orphaned and the app hangs waiting for them.
        # The export worker honours the same stop flag between files; left
        # running, Qt would destroy it mid-copy and abort the process.
        # A worker still busy after that — a large file half-way to a slow
        # stick — is waited for, with the window still open: closing over it
        # is the abort. The first look waits; the ones that follow only ask.
        waiting_since = getattr(self, "_closing_since", None)
        busy = stop_and_wait(self._workers(), wait_ms=_QUIT_WAIT_MS if waiting_since is None else 0)
        if not may_close(busy, waiting_since=waiting_since, now=time.monotonic()):
            if waiting_since is None:
                self._closing_since = time.monotonic()
                names = ", ".join(type(w).__name__ for w in busy)
                logging.getLogger(__name__).warning("quit: waiting for %s to finish", names)
                self.notice_banner.show_transient(
                    "Finishing what is being written, then closing…",
                    "warning",
                    msecs=int(GIVE_UP_AFTER_S * 1000),
                )
            event.ignore()
            QTimer.singleShot(_QUIT_RETRY_MS, self.close)
            return

        self._shut_down()
        event.accept()

    def _workers(self) -> tuple:
        return (
            getattr(self.lib_tab, "_worker", None),
            getattr(self.lib_tab, "_hc_worker", None),
            getattr(self.lib_tab, "_discogs_worker", None),
            getattr(self.lib_tab, "_export_worker", None),
            getattr(self.now_tab, "_reanalyse_worker", None),
            getattr(getattr(self._stats_tab, "_replay_dialog", None), "_worker", None),
        )

    def _on_about_to_quit(self) -> None:
        """The app quits without the window closing (hidden behind the
        minimised button): stop the workers and threads all the same."""
        if not self._shut:
            stop_and_wait(self._workers(), wait_ms=_QUIT_WAIT_MS)
        self._shut_down()

    def _start_extensions(self) -> None:
        """An add-on's start hooks (core.extensions), once the window is up."""
        from core.extensions import start_hooks

        for hook in start_hooks():
            try:
                hook(self)
            except Exception:
                logging.getLogger(__name__).exception("an add-on's start failed")

    def _shut_down(self) -> None:
        """Stop what runs beside the UI, once, however the app quits.

        From closeEvent, and from the app's aboutToQuit: quitting with the
        window hidden behind the minimised button never closed it, the
        webhook thread was still running, and Qt aborted on destroying it
        (2026-10-05).
        """
        if self._shut:
            return
        self._shut = True
        # Let the display sleep normally again. macOS would drop the assertion
        # with the process anyway, but Windows' execution state is per-thread
        # and outlives a messy exit, so release it explicitly on both.
        self._wake_lock.release()

        # A key still held when we quit would stay down in Traktor forever —
        # nothing is left to post its release.
        keyforward.release_all()

        # What an add-on runs beside TK goes with it (core.extensions quit hooks).
        from core.extensions import quit_hooks

        for hook in quit_hooks():
            try:
                hook(self)
            except Exception:
                logging.getLogger(__name__).exception("quit: an add-on's quit failed")

        if self._s4mk2 is not None:
            self._s4mk2.stop()
            self._s4mk2.wait(2000)
        if self._watcher is not None:
            self._watcher.stop()
            self._watcher.wait(3000)
