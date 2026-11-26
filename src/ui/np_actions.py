"""Row actions of the Now Playing tab: context menu, load to deck, hotcues.

What happens when the user acts on a result row: the right-click menu, loading
a track onto a deck (by drag or key press, with the accessibility check the
drag needs), firing hotcues and beatjumps, and re-analysing, revealing or
removing a track. They drive the table, the deck state and the signals the tab
owns, so they are a mixin the tab inherits rather than a separate object.
``NowPlayingTab`` in ``ui.now_playing`` provides those attributes.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QMenu

from core.db import remove_track
from core.extensions import add_track_menu_entries
from core.reveal import reveal_in_file_manager
from ui.np_constants import _DECK_LETTERS, _REVEAL_LABEL, _STOP_BEFORE_LOAD_MS
from workers.analysis import AnalysisWorker

_log = logging.getLogger(__name__)


class _ActionsMixin:
    """Row context menu, deck loading and per-track actions."""

    # ── Context menu ──────────────────────────────────────────────────────────

    def _show_context_menu(self, pos) -> None:
        row = self.table.rowAt(pos.y())
        if row < 0:
            return
        path = self.table._row_paths.get(row)
        if not path:
            return

        menu = QMenu(self)

        # One entry per deck at the top level — a submenu is an extra hover
        # for the thing the menu is mostly opened for. Only the decks Traktor
        # shows, unless a hidden one is in use.
        for deck in _DECK_LETTERS:
            if not self._deck_btns[deck].isVisibleTo(self._deck_grid):
                continue
            playing = deck in self._playing_decks
            on_air = playing and self._deck_volume.get(deck, 0.0) > 0.1
            if on_air:
                label = f"Load into deck {deck}  ▶ on air"
            elif playing:
                label = f"Load into deck {deck}  ▶ playing"
            else:
                label = f"Load into deck {deck}"
            act = menu.addAction(label)
            act.triggered.connect(lambda _, d=deck, p=path: self._load_to_deck(p, d))

        menu.addSeparator()

        reanalyse_act = menu.addAction("Re-analyse track")
        reanalyse_act.triggered.connect(lambda: self._reanalyse(path))

        hotcues_act = menu.addAction("Recompute hotcues")
        hotcues_act.triggered.connect(lambda: self.recompute_hotcues_requested.emit(path))

        discogs_act = menu.addAction("Look up on Discogs")
        discogs_act.triggered.connect(lambda: self.discogs_lookup_requested.emit(path))

        add_track_menu_entries(menu, path)

        menu.addSeparator()
        reveal_act = menu.addAction(_REVEAL_LABEL)
        reveal_act.triggered.connect(lambda: self._reveal(path))

        remove_act = menu.addAction("Remove from database")
        remove_act.triggered.connect(lambda: self._remove_from_db(path, row))

        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _require_accessibility(self) -> bool:
        """Return True if Accessibility is granted; otherwise prompt the user.

        Drag-loading relies on synthesized Quartz mouse events, which macOS
        silently drops without Accessibility permission. Rather than let the
        load fail invisibly, surface an actionable dialog that triggers the
        TCC prompt and opens the right System Settings pane.
        """
        from core.permissions import (
            accessibility_granted,
            open_accessibility_settings,
            request_accessibility,
        )
        from core.runtime import in_traktor

        # Inside Traktor a load is a direct call (libtk), never a synthesized drag: no
        # permission to ask for (User, 2026-11-24 — the prompt also stopped Auto DJ's loads).
        if in_traktor() or accessibility_granted():
            return True

        from ui.dialogs import confirm

        open_it = confirm(
            self,
            "Accessibility permission required",
            "Track Kommander can't load tracks onto your decks.\n\n"
            "Loading uses a synthesized drag-and-drop, which macOS blocks "
            "until you grant Accessibility permission to this app.\n\n"
            "Open System Settings → Privacy & Security → Accessibility and "
            "enable Track Kommander, then try loading again.",
            yes_text="Open System Settings",
            no_text="Cancel",
        )

        if open_it:
            # Fire the TCC prompt (registers the app the first time) then open
            # the pane so the user can flip the toggle even if the prompt is
            # silent on repeat calls.
            request_accessibility()
            open_accessibility_settings()
        return False

    def _load_to_deck(self, path: str, deck: str) -> None:
        if not self._require_accessibility():
            _log.warning(
                "load_to_deck: aborted for deck %s — Accessibility not granted.",
                deck,
            )
            return
        playing = deck in self._playing_decks
        on_air = self._deck_volume.get(deck, 0.0) > 0.1

        if playing and on_air:
            from ui.dialogs import confirm

            if not confirm(
                self,
                f"Deck {deck} is live",
                f"Deck {deck} is currently playing and on air.\nStop it and load the new track?",
            ):
                return

        # Remember which deck we just loaded — keyboard hotcue shortcuts (1–8)
        # target this deck. Set early so the user can fire a hotcue while the
        # drag/QML pipeline is still processing.
        self._last_loaded_deck = deck

        from core.runtime import in_traktor

        if in_traktor():
            # Inside Traktor there is nothing to drag (TK's table is hidden): Traktor loads the
            # file by path through libtk, then the automation mode applies (NativeDeckControl).
            # Auto DJ's loads come here too (User, 2026-11-24: "no automation works").
            self.load_to_deck_requested.emit(path, deck, False, self._transpose_for(path))
            return

        # Drag is the only load mechanism standalone. Search-in-library fallback is
        # disabled by design: it produces wrong-track loads when the search
        # string isn't unique, and it disturbs the operator's browser view.
        #
        # The QML webhook is still notified after a successful drag so it can
        # run the post-load automation (mute fader, switch headphone cue,
        # sync+play, AutoMix). That command carries ``skip_load=True`` so the
        # QML doesn't re-load the track via search.
        from PySide6.QtWidgets import QApplication

        from core.drag import drag_to_deck, row_screen_center

        row = next((r for r, p in self.table._row_paths.items() if p == path), None)
        if row is None:
            _log.warning(
                "load_to_deck: cannot locate result row for path=%r — load "
                "aborted. Make sure the track is visible in the results.",
                path,
            )
            return

        def _do_load() -> None:
            # ResultsTable.startDrag drags whatever row is *current*, so make the
            # target row current (and selected) right before the drag fires.
            self.table.setCurrentCell(row, 0)
            self.table.selectRow(row)
            QApplication.processEvents()

            src = row_screen_center(self.table, row)
            if src is None:
                _log.warning(
                    "load_to_deck: row %r for path=%r has no on-screen rect.",
                    row,
                    path,
                )
                self._flash_deck_load_failed(deck)
                self.notice_requested.emit(
                    f"Couldn't load onto deck {deck} — the result row is not visible on screen.",
                    "warning",
                )
                return
            outcome = drag_to_deck(src, deck)
            if not outcome:
                self._flash_deck_load_failed(deck)
                _log.warning(
                    "load_to_deck: drag could not start for deck %s — %s",
                    deck,
                    outcome.reason,
                )
                self.notice_requested.emit(
                    f"Couldn't load onto deck {deck}: {outcome.reason or 'unknown error.'}",
                    "warning",
                )
                return
            _log.debug("load_to_deck: drag dispatched for deck %s", deck)
            # Drive the QML automation chain (mute / cue / sync+play / AutoMix)
            # WITHOUT re-loading (the drag already placed the track).
            self.load_to_deck_requested.emit(path, deck, True, self._transpose_for(path))

        if playing:
            self.stop_deck_requested.emit(deck)
            # Give the stop time to reach Traktor and take effect before the
            # drag lands: Traktor refuses a load onto a playing deck. The
            # bridge long-polls, so a command arrives in well under 300 ms
            # (D2 files older than v6 poll every 100 ms — also covered).
            QTimer.singleShot(_STOP_BEFORE_LOAD_MS, _do_load)
        else:
            _do_load()

    def _load_selected_to_deck(self, deck: str) -> None:
        """Load the currently selected table row into the given deck (manual)."""
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return
        path = self.table._row_paths.get(rows[0].row())
        if path:
            self._load_to_deck(path, deck)

    # Public aliases used by hardware control watcher signals
    def load_selected_to_deck(self, deck: str) -> None:
        self._load_selected_to_deck(deck)

    def trigger_hotcue(self, index: int) -> None:
        """Fire hotcue *index* (1–8) on the most recently loaded deck.

        No-op if no deck has been loaded this session — Traktor wouldn't
        know which deck to act on. The keyboard shortcut handler
        (MainWindow eventFilter) calls this for keys 1-8.
        """
        deck = self._last_loaded_deck
        if not deck:
            _log.debug("hotcue %d ignored — no deck loaded yet this session", index)
            return
        _log.debug("hotcue %d → deck %s", index, deck)
        self.hotcue_requested.emit(deck, int(index))

    # JumpSize enum values from Traktor's qml/Defines/JumpSize.qml.
    BEATJUMP_BWD_8 = -10
    BEATJUMP_FWD_8 = 10
    BEATJUMP_BWD_16 = -11
    BEATJUMP_FWD_16 = 11

    def trigger_beatjump(self, value: int) -> None:
        """Beat-jump the last-loaded deck by ``value`` (JumpSize enum).

        See class constants ``BEATJUMP_{BWD,FWD}_{8,16}``. No-op if no
        deck has been loaded this session.
        """
        deck = self._last_loaded_deck
        if not deck:
            _log.debug("beatjump %d ignored — no deck loaded yet this session", value)
            return
        _log.debug("beatjump %d → deck %s", value, deck)
        self.beatjump_requested.emit(deck, int(value))

    def _load_selected_to_free_deck(self) -> None:
        """Enter key: load selected row into the first deck that is not playing+on-air."""
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return
        path = self.table._row_paths.get(rows[0].row())
        if not path:
            return
        # Find first deck that is NOT active (not playing AND on-air simultaneously)
        free = next(
            (
                d
                for d in _DECK_LETTERS
                if not (d in self._playing_decks and self._deck_volume.get(d, 0.0) > 0.1)
            ),
            None,
        )
        if free is None:
            return  # all four decks are active — do nothing
        self._auto_loaded_deck = free
        self._auto_loaded_path = path
        self._load_to_deck(path, free)

    def _stop_auto_deck(self) -> None:
        """Stop the deck we auto-loaded via Enter, if it is still playing our track."""
        if not self._auto_loaded_deck:
            return
        deck = self._auto_loaded_deck
        self._auto_loaded_deck = None
        self._auto_loaded_path = None
        if deck in self._playing_decks:
            self.stop_deck_requested.emit(deck)

    def _reveal(self, path: str) -> None:
        if not reveal_in_file_manager(path):
            from ui.dialogs import warn

            warn(self, "File not found", f"Not on disk:\n{path}")

    def _reanalyse(self, path: str) -> None:
        # One at a time: replacing the reference to a running worker would
        # destroy a live QThread (Qt aborts), and the first one's finish would
        # re-enable the table while the second still runs.
        running = self._reanalyse_worker
        if running is not None and running.isRunning():
            self.notice_requested.emit(
                "A track is already being re-analysed — try again when it is done.",
                "warning",
            )
            return
        self.table.setEnabled(False)
        # Held in self._reanalyse_worker until the next re-analyse, which the
        # guard above only allows once this one has stopped running.
        self._reanalyse_worker = AnalysisWorker([path], force=True)
        self._reanalyse_worker.finished.connect(
            lambda *_: (self.table.setEnabled(True), self._refresh())
        )

        def _show_reanalyse_error(msg: str) -> None:
            from ui.dialogs import warn

            warn(self, "Re-analyse error", msg)

        self._reanalyse_worker.error.connect(_show_reanalyse_error)
        self._reanalyse_worker.start()

    def _remove_from_db(self, path: str, row: int) -> None:
        from ui.dialogs import confirm

        if not confirm(
            self,
            "Remove track",
            f"Remove from database?\n\n{Path(path).name}",
        ):
            return
        remove_track(path)
        # Keep the user where they were: on the track they had selected, or,
        # if that is the one removed, on the row that moves up into its place
        # (the one above when it was the last).
        paths = self.table._row_paths
        sel_rows = self.table.selectionModel().selectedRows()
        selected = paths.get(sel_rows[0].row()) if sel_rows else None
        if selected is None or selected == path:
            selected = paths.get(row + 1) or paths.get(row - 1)
        loaded = self._loaded
        # Rebuild from the result lists rather than deleting the one table
        # row: every row below it would otherwise keep its old index into
        # _filtered_results and drag, load or describe the wrong track.
        self._all_results = [r for r in self._all_results if r["row"]["path"] != path]
        self._apply_filter(select_path=selected, min_rows=loaded - 1)
