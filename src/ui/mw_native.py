"""The main window's side of the native integration inside Traktor.

libtk (``traktor-native/``, injected into Traktor) posts what happens in
Traktor's own UI: a click on a button in its band (``/uiAction``), a track
loaded or read natively (``/nativeTrack``), the opener (``/show``). These
handlers turn that into what TK does. A mixin of ``MainWindow``: they use its
tabs, panels and the mirrors that keep Traktor's band and list in step.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

from PySide6.QtGui import QCursor

from core.collection_source import set_native_cues, set_native_source
from core.playlists import set_native_playlists
from core.process_age import process_age_s
from ui.auto_mode_popup import AutoModePopup
from ui.band_pages import BAND_PAGES, all_pages, page_of, popup_key
from ui.matching_popup import MatchingPopup
from ui.native_icons import push_band_icons
from ui.np_constants import _AUTO_MODES
from ui.panel_popup import PanelPopup
from workers import native_cmd
from workers.native_audio import POLL_MS, NativeAudio
from workers.native_collection import attach_collection, attach_cues, attach_playlists
from workers.native_deck_control import NativeDeckControl
from workers.native_deck_state import NativeDeckState
from workers.native_props import NativeProps

log = logging.getLogger(__name__)

#: Loads Traktor reports this soon after TK attached (or reconnected) are the decks' state at
#: start, not loads: the automation mode leaves them alone.
STARTUP_LOADS_S = 3.0
#: Traktor puts the last session's tracks back on its decks this long after it starts (measured
#: 15 s on 2026-11-24, after TK had attached): those are not loads either, or AutoMix starts a deck
#: on its own at launch. Three times the measured delay.
RESTORE_S = 45.0


def loads_count_from() -> float:
    """time.monotonic() from which a deck load gets the automation mode."""
    age = process_age_s(os.getpid())  # inside Traktor: how long ago Traktor started
    restore_left = RESTORE_S - age if age is not None else 0.0
    return time.monotonic() + max(STARTUP_LOADS_S, restore_left)


class NativeBandMixin:
    """Handlers for what libtk reports from inside Traktor."""

    def _attach_native_decks(self, watcher) -> None:
        """Inside Traktor: deck state and deck commands by direct calls, not the D2 bridge.

        Built once and kept: Traktor's change callbacks are registered for good, so a
        reconnect (a new watcher) only re-points them. Without libtk's property API there
        is no fallback: the D2 bridge is never used inside Traktor (User, 2026-11-24).
        """
        native = getattr(self, "_native_decks", None)
        if native is None:
            props = NativeProps.attach()
            if props is None:
                log.error(
                    "inside Traktor without libtk's property API: TK cannot see or drive the decks"
                )
                return
            state = NativeDeckState(props, watcher, self)
            control = NativeDeckControl(props, state.resync, self)
            self._native_decks = (props, state, control)
            watcher.attach_native(control.execute)
            # The decks' state at start is not a load: no mode applies to what is already loaded.
            self._native_loads_from = loads_count_from()
            watcher.deck_loaded.connect(self._on_native_deck_loaded)
            state.start()  # emits the current state once
            self._attach_native_collection()
            push_band_icons()  # TK's own icons in Traktor's band: grey off, black lit
            from core.traktor_folders import set_native_setting, traktor_music_folders
            from workers import native_columns
            from workers.native_settings import setting

            set_native_setting(setting)  # Traktor's settings from its memory, never the .tsi
            native_columns.set_roots(traktor_music_folders())  # for the DIR SHORT column
            self._hide_missing_pages()
            self._attach_band_states()
            self._attach_native_audio()
            self._attach_auto_library(watcher)
            return
        _props, state, control = native
        state.set_hub(watcher)
        watcher.attach_native(control.execute)
        self._native_loads_from = loads_count_from()  # resync re-emits the decks
        watcher.deck_loaded.connect(self._on_native_deck_loaded)
        state.resync()

    def _on_native_deck_loaded(self, deck: str, info: dict) -> None:
        """Inside Traktor every load goes through Traktor (its lists, TK's Results playlist,
        a controller), so TK's automation mode applies to each one, as it does to TK's own loads
        standalone (User, 2026-11-24: "every load"). Not to TK's own loads (already given the mode
        by NativeDeckControl), nor to the decks' state at start."""
        from core.prefs import AUTOMATION_MODE

        path = str(info.get("file_path") or "")
        native = getattr(self, "_native_decks", None)
        mode = AUTOMATION_MODE.get()
        if not path or native is None or self._watcher is None:
            return
        if time.monotonic() < getattr(self, "_native_loads_from", 0.0):
            log.info(
                "automation: deck %s gets %s at start (Traktor's last session): left alone",
                deck,
                Path(path).name,
            )
            return
        if mode == "off":
            log.info("automation: off — deck %s load of %s left alone", deck, Path(path).name)
            return
        if native[2].loaded_by_tk(deck, path):
            log.info(
                "automation: deck %s load of %s was TK's own (mode already applied)",
                deck,
                Path(path).name,
            )
            return
        log.info(
            "automation: %s on deck %s for %s (loaded in Traktor)", mode, deck, Path(path).name
        )
        self._watcher.queue_load(path, deck, skip_load=True)

    def _attach_band_states(self) -> None:
        """Band icons turn blue while their job runs: hotcues, tagging, USB export, and an
        add-on's buttons that say so (core.extensions.BandButton.busy)."""
        from ui.band_extensions import has_busy_states
        from ui.native_icons import push_state_icon
        from workers.band_states import BandStates, read_states, worker_running

        lib = self.lib_tab

        def jobs() -> dict[str, bool]:
            return {
                "L_HOTCUES": worker_running(getattr(lib, "_hc_worker", None)),
                "L_TAGGING": worker_running(getattr(lib, "_discogs_worker", None)),
                "L_EXPORT": worker_running(getattr(lib, "_export_worker", None)),
            }

        self._band_states = BandStates(
            push_state_icon, read_states if has_busy_states() else None, self, local=jobs
        )
        self._band_states.start()

    def _attach_auto_library(self, watcher) -> None:
        """No Scan page inside Traktor: new tracks are analysed, gone ones dropped, by themselves."""
        from core.collection_source import collection_paths
        from core.db import get_all_analyzed_paths, remove_tracks_not_in
        from core.traktor import find_traktor_nml
        from workers.analysis import AnalysisWorker
        from workers.auto_library import WORKERS, AutoLibrary

        def start(paths: list[str]) -> AnalysisWorker:
            worker = AnalysisWorker(paths, force=False, n_workers=WORKERS, sync_db=False)
            worker.finished.connect(lambda *_a: self.lib_tab._update_stats())
            worker.start()
            return worker

        auto = AutoLibrary(
            lambda: collection_paths(find_traktor_nml()),
            get_all_analyzed_paths,
            remove_tracks_not_in,
            lambda: bool(self.now_tab._playing_decks),
            start,
            parent=self,
        )
        watcher.deck_state.connect(auto.on_deck_state)
        auto.start()
        self._auto_library = auto

    def _attach_native_audio(self) -> None:
        """Traktor's audio device per situation: no auto-switch to the S4, manual picks kept."""
        from PySide6.QtCore import QTimer

        from core.runtime import user_data_dir

        audio = NativeAudio.attach(user_data_dir() / "audio-situations.json")
        if audio is None:
            log.warning("integrated mode without libtk's audio hooks: Traktor picks devices itself")
            return
        self._native_audio = audio
        timer = QTimer(self)
        timer.setInterval(POLL_MS)
        timer.timeout.connect(audio.situations.poll)
        timer.start()
        self._native_audio_timer = timer
        audio.situations.poll()  # this setup's remembered device, now

    def _hide_missing_pages(self) -> None:
        """A band page whose tab this TK does not have goes, and so do libtk's spare buttons no
        add-on took (ui.band_extensions).

        Settings is one button inside Traktor: its page keeps its own tab row (Matching ·
        Tagging and an add-on's pages), the Discogs/Spotify set-up on its Tagging tab
        (SettingsTab.fold_for_traktor). No General, Traktor and Controls there: their install,
        connection and interface rows do not apply, and the list inside Traktor is Traktor's own,
        browsed with Traktor's keys and S4 mapping (TK's shortcuts only ever applied to TK's
        window)."""
        tabs = set(getattr(self.settings_tab, "_tab_pages", {}))
        self.settings_tab.fold_for_traktor()
        from core.extensions import traktor_hooks

        for hook in traktor_hooks():  # an add-on's own set-up inside Traktor
            try:
                hook(self)
            except Exception:
                log.exception("an add-on's set-up inside Traktor failed")
        self.lib_tab.hide_grid_fixes()
        self.lib_tab.hide_auto_playlists()
        self.lib_tab.hide_collection_fill()
        # Kept by themselves inside Traktor: the library follows the collection (auto_library),
        # the Kommander date playlists are refreshed by TK itself (workers/native_playlists.py).
        gone = {"L_SCAN", "L_PLAYLISTS"}
        from ui.band_extensions import buttons_to_hide, slot_visibility

        for (
            slot,
            hidden,
        ) in slot_visibility().items():  # libtk starts them hidden: show what is taken
            if not hidden:
                try:
                    native_cmd.hide_band_button(slot, hidden=False)
                except (OSError, native_cmd.NativeCmdError) as exc:
                    log.warning("band button %s not shown: %s", slot, exc)
        gone |= {slot for slot, hidden in slot_visibility().items() if hidden}
        lib_tabs = set(getattr(self.lib_tab, "_tab_pages", {}))
        for button in buttons_to_hide({**BAND_PAGES, **all_pages()}, gone, tabs, lib_tabs):
            try:
                native_cmd.hide_band_button(button)
            except (OSError, native_cmd.NativeCmdError) as exc:
                log.warning("band button %s not hidden: %s", button, exc)
        from core.extensions import on_addon_shown

        on_addon_shown(self._follow_addon_shown)

    def _follow_addon_shown(self, shown: bool) -> None:
        """An add-on shown or hidden (its own setting): its band buttons and its tabs follow."""
        from ui.band_extensions import slot_visibility

        for slot, hidden in slot_visibility().items():
            try:
                native_cmd.hide_band_button(slot, hidden=hidden)
            except (OSError, native_cmd.NativeCmdError) as exc:
                log.warning("band button %s not changed: %s", slot, exc)
        self.lib_tab.show_extension_tabs(shown)
        self.settings_tab.show_extension_tabs(shown)

    def _attach_native_collection(self) -> None:
        """Inside Traktor: the collection and the playlist tree from Traktor's memory, not the NML."""
        from core.runtime import user_data_dir

        scratch = user_data_dir() / "traktor-native"
        collection = attach_collection(scratch)
        playlists = attach_playlists(scratch)
        cues = attach_cues(scratch)
        if collection is None or playlists is None or cues is None:
            # No fallback: the NML is never read inside Traktor (User, 2026-11-24).
            log.error("inside Traktor without libtk's dumps: TK cannot read Traktor's collection")
            return
        self._native_collection = (collection, playlists, cues)
        set_native_source(collection)
        set_native_playlists(playlists)
        set_native_cues(cues)  # dumped when first asked: only the hotcue tool reads them
        collection.refresh()  # the first snapshots now, on the main thread
        playlists.refresh()
        from PySide6.QtCore import QTimer

        # The Kommander date playlists, kept by TK itself inside Traktor (no NML smartlists).
        QTimer.singleShot(5000, self._refresh_date_playlists)

    def _refresh_date_playlists(self) -> None:
        from workers.native_playlists import refresh

        try:
            result = refresh()
        except Exception:  # a failed refresh must never take TK down with it
            log.exception("date playlists: refresh failed")
            return
        if result is not None and (result.created or result.refilled or result.deleted):
            native = getattr(self, "_native_collection", None)
            if native is not None:
                native[1].refresh()  # the playlist tree TK shows follows the change

    def _toggle_panel_popup(self, key: str) -> None:
        """Settings, Library or Stats in a window of its own, from Traktor's band.

        A page button (S_GENERAL, L_SCAN …) pops up its whole page: only inside Traktor
        (embed_tk.show_page) does each tab stand alone."""
        key = popup_key(key)
        pages = {
            "SETTINGS": ("Settings", lambda: self.settings_tab),
            "LIB": ("Library", lambda: self.lib_tab),
            "STATS": ("Metrics & Stats", self._stats_page),
        }
        popup = self._panel_popups.get(key)
        if popup is None:
            if self._panels.current_panel:
                self._panels.close_panel()  # the page cannot be in two places
            title, page = pages[key]
            popup = PanelPopup(title, page())
            self._panel_popups[key] = popup
        popup.toggle(QCursor.pos())

    def _on_native_track(self, info: dict) -> None:
        """Track metadata libtk read natively from Traktor's C++ (not the QML
        bridge). When it carries a deck letter (a load), feed the now-playing
        deck view the same way the QML deckLoaded bridge does — a more robust
        path that survives the QML bridge dropping. Browse/ad-hoc reads (no
        deck) are ignored here for now."""
        if self._watcher is not None and self._watcher.native_state:
            return  # native deck state reports loads already, with the full metadata
        deck = str(info.get("deck", "")).upper()
        if deck in ("A", "B", "C", "D"):
            self.now_tab.update_deck(deck, info)

    def _extension_action(self, action: str) -> bool:
        """An add-on handles *action* (core.extensions.add_action_handler): True when one did."""
        from core.extensions import action_handlers

        for handler in action_handlers():
            try:
                if handler(self, action):
                    return True
            except Exception:
                log.exception("an add-on failed on band action %s", action)
                return True
        return False

    def _on_ui_action(self, action: str) -> None:
        """A button in libtk's native band inside Traktor was clicked.

        A–D act like a click on that deck button (clicking the selected deck
        again deselects it); SETTINGS, LIB and STATS open that page in a
        window of its own over Traktor; AUTO cycles the auto mode; KOMMANDER (the "Track Kommander" node in Traktor's tree was
        selected) sends our results to Traktor's list and keeps it updated;
        MATCHING opens (or closes) the matching cards under the mouse.
        The watcher has already checked the name.
        """
        if action == "KOMMANDER":
            self._native_list.activate()
        elif action == "BAND":  # the band was just drawn: show our source deck and cards
            self._native_deck.resend()
            self._native_cards.resend()
            self._native_auto.resend()
        elif self._extension_action(action):
            pass  # an add-on's (its native page's clicks, …)
        elif action.startswith("CARD:"):  # a card in the band: toggle that axis
            self.now_tab._toggle_axis_active(action[5:])
            self._native_cards.poll()  # answer now, not on the next 0.5 s poll
        elif action == "MATCHING":
            if self._matching_popup is None:
                self._matching_popup = MatchingPopup(self.now_tab)
            self._matching_popup.toggle(QCursor.pos())
        elif action in ("A", "B", "C", "D"):
            self.now_tab._deck_btns[action].click()
        elif action == "LIST":  # the band's list button: back to the playlist
            self._show_list()
        elif action in ("SETTINGS", "LIB") or page_of(action) is not None:
            self._toggle_panel_popup(action)  # inside Traktor: that page replaces the list
        elif action == "AUTO":  # the next mode, shown in the modes panel (User, 2026-11-25)
            self.now_tab._next_auto_mode()
            if self._auto_mode_popup is None:
                self._auto_mode_popup = AutoModePopup(self.now_tab._set_auto_mode)
            current = _AUTO_MODES[self.now_tab._auto_mode_idx][0]
            self._auto_mode_popup.show_mode(QCursor.pos(), current)

    def _show_list(self) -> None:
        """Back to the playlist: close the Settings / Library / Stats windows.

        Inside Traktor the pages replace the list instead; there this is
        swapped for hiding the page (traktor-native/re/embed_tk.py).
        """
        for popup in getattr(self, "_panel_popups", {}).values():
            popup.hide()

    def _on_ui_card(self, key: str, field: str, value: int) -> None:
        """A card slider moved in Traktor's band: move TK's own slider.

        Setting the slider is what dragging it in TK does: the axis state,
        the settings and the re-rank follow from its valueChanged.
        """
        refs = self.now_tab._axis_widgets.get(key)
        slider = refs.get("val" if field == "value" else "strength") if refs else None
        if slider is not None and slider.value() != value:
            slider.setValue(value)
            self._native_cards.poll()  # echo at once (the band ignores it mid-drag)

    def _on_show_requested(self) -> None:
        """A native opener (libtk, injected into Traktor) asked us to show.

        The user clicked the "Track Kommander" entry in Traktor's own browser
        tree; libtk hooked that selection and posted ``/show``. Un-minimise or
        un-hide if needed, then raise above Traktor the same way the docking
        code does when Kommander is restored. Runs on the GUI thread: the
        signal is emitted from the watcher thread and Qt queues it here.
        """
        if getattr(self, "_integrated", False):
            return  # TK lives inside Traktor: there is no window to show
        if self.isMinimized():
            self.showNormal()
        elif not self.isVisible():
            self.show()
        self._force_raise()
