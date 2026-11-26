"""Deck state updates and beat-grid capture for the Now Playing tab.

The public entry points MainWindow and the Traktor watcher call as decks
change (loaded track, play state, tempo, key knob, position, on-air volume),
the info line under the cover they feed, and the beat-grid offset capture
behind the deck right-click menu. They read and write the deck dictionaries
and buttons the tab owns, so they are a mixin the tab inherits rather than a
separate object. ``NowPlayingTab`` in ``ui.now_playing`` provides those
attributes.
"""

from __future__ import annotations

import logging
import time as _time
from pathlib import Path

from PySide6.QtWidgets import QMenu

from core.extensions import add_track_menu_entries
from core.grid_fix import GridFix
from core.matcher import find_track_by_path
from ui.np_constants import _DECK_LETTERS
from ui.np_format import _beats, info_line_html, live_source_bpm
from ui.np_tooltip import _row_tooltip

_log = logging.getLogger(__name__)


class _DeckApiMixin:
    """Deck updates from Traktor, the info line and grid-offset capture."""

    # ── Public API called by MainWindow / watcher signals ─────────────────────

    def update_deck_phase(self, deck: str, phase: float) -> None:
        """A phase reading from the bridge, for the grid-offset capture."""
        self._phases.add(deck, phase)

    def grid_capture_state(self, deck: str) -> tuple[GridFix | None, str]:
        """What capturing this deck's grid offset would do right now.

        Returns the fix that would be queued and a label for it, or ``None``
        and the reason it cannot be. Only a settled reading counts: with the
        tempos locked the offset left by a jog nudge is the grid error, but
        with the tempos apart the phase runs and wraps and means nothing.
        """
        info = self._deck_info.get(deck) or {}
        path = info.get("file_path", "")
        if not path:
            return None, f"Deck {deck}: no track loaded"
        bpm = float(info.get("bpm") or 0)
        if bpm <= 0:
            return None, f"Deck {deck}: no BPM — analyse this track first"
        if not self._phases.settled(deck):
            return None, (f"Deck {deck}: the tempo is not locked — the phase is still moving")
        phase = self._phases.latest(deck) or 0.0

        from datetime import datetime

        fix = GridFix(
            path=path,
            deck=deck,
            phase=phase,
            bpm=bpm,
            title=info.get("title", ""),
            artist=info.get("artist", ""),
            captured_at=datetime.now().isoformat(timespec="seconds"),
        )
        return fix, f"Fix phase   {fix.offset_ms:+.0f} ms   ({_beats(phase)})"

    def capture_grid_offset(self, deck: str) -> bool:
        """Queue this deck's standing phase offset as a beat-grid fix."""
        fix, label = self.grid_capture_state(deck)
        if fix is None:
            self.notice_requested.emit(label + ".", "warning")
            return False
        self._grid_queue.add(fix)
        self.grid_fix_captured.emit()
        self.notice_requested.emit(
            f"Grid fix queued: {Path(fix.path).name} {fix.offset_ms:+.0f} ms "
            f"({_beats(fix.phase)}). "
            "Apply it in Library → Hotcues once Traktor is closed.",
            "info",
        )
        return True

    def _show_deck_menu(self, deck: str, pos) -> None:
        """Right-click on a deck button: fix that deck's beat grid.

        The entry carries the offset it would record, or the reason it cannot
        — a menu that explains itself beats a silent no-op.
        """
        menu = QMenu(self)
        fix, label = self.grid_capture_state(deck)
        act = menu.addAction(label if fix else label + "…")
        if fix is None:
            act.setEnabled(False)
        else:
            act.setToolTip(
                "Queue this deck's standing phase offset as a beat-grid "
                "correction, to write to the collection once Traktor is closed."
            )
            act.triggered.connect(lambda: self.capture_grid_offset(deck))
        add_track_menu_entries(menu, (self._deck_info.get(deck) or {}).get("file_path", ""))
        btn = self._deck_btns.get(deck)
        menu.exec(btn.mapToGlobal(pos) if btn is not None else pos)

    def set_deck_playing(self, deck: str, playing: bool) -> None:
        was_playing = deck in self._playing_decks
        if playing:
            self._playing_decks.add(deck)
            if not was_playing:  # genuine play-start event
                self._deck_play_start[deck] = _time.time()
        else:
            self._playing_decks.discard(deck)
            self._deck_play_start.pop(deck, None)
        self._refresh_deck_buttons()
        # The live tempo falls back to the master clock only while the
        # selected deck plays, so a play/stop of that deck changes the info
        # line even though the source track stays the same.
        if playing != was_playing and deck == self._selected_deck():
            self._refresh_info_line()
        self._auto_select_active_deck()

    def update_deck(self, deck: str, info: dict) -> None:
        """Called when a track is loaded into a Traktor deck — does NOT change NOW PLAYING."""
        self._deck_info[deck] = info
        self._deck_progress[deck] = 0.0
        if btn := self._deck_btns.get(deck):
            btn.set_progress(0.0)
        self._refresh_deck_buttons()
        # If Traktor confirms a different track loaded onto our auto-loaded deck,
        # the user must have overridden it — clear the auto-load tracking.
        if deck == self._auto_loaded_deck:
            confirmed_path = info.get("file_path", "")
            if confirmed_path and confirmed_path != self._auto_loaded_path:
                self._auto_loaded_deck = None
                self._auto_loaded_path = None

    def _deck_tooltip(self, deck: str) -> str:
        """Rich tooltip for a deck button — the deck's loaded track shown with the
        same layout as a result row (metric bars + current-track marker). Reuses
        the track's existing result row (so it carries the match score) when it's
        in the current results, else builds a minimal row from the DB."""
        info = self._deck_info.get(deck) or {}
        path = info.get("file_path", "")
        if not path:
            return ""
        r = next((x for x in self._all_results if x["row"]["path"] == path), None)
        if r is None:
            row = find_track_by_path(path)
            if row is None:
                return ""
            r = {"row": row, "composite": None, "axis_scores": {}}
        return _row_tooltip(r, self._source)

    def update_deck_position(self, deck: str, position: float) -> None:
        """Called every keepAlive (~1 s) with the deck's playhead position (0.0–1.0)."""
        self._deck_progress[deck] = position
        if btn := self._deck_btns.get(deck):
            btn.set_progress(position)

    def update_deck_on_air(self, deck: str, volume: float) -> None:
        """Called with the channel fader volume whenever it changes."""
        self._deck_volume[deck] = volume
        self._refresh_deck_buttons()
        self._auto_select_active_deck()

    def update_master_tempo(self, bpm: float) -> None:
        """Called whenever the Traktor master clock BPM changes.

        The master clock reflects the actual playing tempo (pitch-fader included).
        Starts a 5-second debounce before re-running matching so we don't thrash
        the results list while the DJ is still moving the pitch fader.
        """
        prev = self._master_bpm
        self._master_bpm = bpm
        self._refresh_info_line()
        # With no source track the results are a plain library listing, which
        # the master tempo has no bearing on — re-ranking would just rebuild
        # the table every few seconds while the pitch fader moves.
        if self._source is not None and abs(bpm - prev) > 0.05:
            self._tempo_debounce.start()

    def _refresh_info_line(self) -> None:
        """Single info line: tempo and key, each written and as it sounds."""
        if self._source is None:
            self.info_lbl.setText("")
            return

        self.info_lbl.setText(
            info_line_html(
                camelot=self._source["camelot"] or None,
                db_bpm=self._source["bpm"] or 0.0,
                master_bpm=self._live_bpm(),
                semitones=self._source_transpose(),
            )
        )

    def _selected_deck(self) -> str | None:
        return next((letter for letter, b in self._deck_btns.items() if b.isChecked()), None)

    def _live_bpm(self) -> float:
        return live_source_bpm(
            self._selected_deck(),
            self._deck_tempo,
            master_bpm=self._master_bpm,
            playing=self._playing_decks,
        )

    def update_deck_tempo(self, deck: str, bpm: float) -> None:
        """A deck's playing tempo, from the 1 s keep-alive."""
        prev = self._deck_tempo.get(deck, 0.0)
        if abs(bpm - prev) <= 0.005:
            return
        self._deck_tempo[deck] = bpm
        if deck != self._selected_deck():
            return
        self._refresh_info_line()
        if self._source is not None and abs(bpm - prev) > 0.05:
            self._tempo_debounce.start()

    def _source_transpose(self) -> int:
        """How far the Key knob is turned on the deck the source came from.

        Zero unless that deck is the selected one: the knob belongs to a
        deck, and the info line describes the deck we are matching against.
        """
        selected = next((letter for letter, b in self._deck_btns.items() if b.isChecked()), None)
        return self._deck_key_adjust.get(selected, 0) if selected else 0

    def update_deck_key_adjust(self, deck: str, semitones: int) -> None:
        """Traktor's Key knob moved — by the DJ's hand or by our own load."""
        if self._deck_key_adjust.get(deck, 0) == semitones:
            return
        self._deck_key_adjust[deck] = semitones
        self._refresh_info_line()

    def _auto_select_active_deck(self) -> None:
        """Select the NOW PLAYING deck only when exactly one deck is playing + on-air.

        Auto-switching is skipped if the user has manually picked a different deck
        since the last time auto-select ran.
        """
        active = [
            d
            for d in _DECK_LETTERS
            if d in self._playing_decks and self._deck_volume.get(d, 0.0) > 0.1
        ]
        if len(active) != 1:
            return
        target = active[0]
        current = next((letter for letter, b in self._deck_btns.items() if b.isChecked()), None)
        # Skip auto-switch only if a baseline has been established AND the user has
        # since manually picked a different deck.  While _auto_selected_deck is None
        # (no successful auto-select yet) we always allow the switch so the initial
        # state works even if the user clicked a deck before auto-select first ran.
        if self._auto_selected_deck is not None and current != self._auto_selected_deck:
            return
        self._auto_selected_deck = target
        self._select_deck(target)
