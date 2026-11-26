"""Automation modes, Auto DJ and played-track bookkeeping for the Now Playing tab.

The tab's automation button cycles off / play+cue / AutoMix / Auto DJ, and
Auto DJ watches the active deck to load the next match before the current
track ends. These methods share the tab's state (the deck dicts, the timers,
the results table), so they are a mixin the tab inherits rather than a
separate object: moving them into their own class would mean re-plumbing that
state. ``NowPlayingTab`` in ``ui.now_playing`` provides the attributes.
"""

from __future__ import annotations

import logging
import random
import time as _time
from pathlib import Path

from PySide6.QtCore import QPoint
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QMenu, QToolTip

from core.auto_dj_timing import mix_start_s
from core.prefs import AUTOMATION_MODE, RAMP_BARS
from ui.icons import glyph_icon
from ui.np_constants import (
    _AUTO_MODES,
    _DECK_LETTERS,
    _SIDE_ICON_PX,
    PLAYED_THRESHOLD_S,
)

_log = logging.getLogger(__name__)

LOAD_LEAD_S = 3.0  # the next track is loaded this long before its transition starts


class _AutoModeMixin:
    """Automation-mode button, the Auto DJ tick and the played-tracks timer."""

    # ── Automation mode ───────────────────────────────────────────────────────

    def _cycle_auto_mode(self) -> None:
        self._auto_mode_idx = (self._auto_mode_idx + 1) % len(_AUTO_MODES)
        AUTOMATION_MODE.set(_AUTO_MODES[self._auto_mode_idx][0], self._settings)
        self.cancel_automix_requested.emit()  # abort any in-progress fader/EQ ramp
        self._apply_auto_mode(show_tooltip=True)

    def _next_auto_mode(self) -> None:
        """The band's AUTO button inside Traktor: the next mode, as standalone TK's button
        (User, 2026-11-25), but a running fade carries on unless that mode is Off."""
        self._set_auto_mode(_AUTO_MODES[(self._auto_mode_idx + 1) % len(_AUTO_MODES)][0])

    def _set_auto_mode(self, key: str) -> None:
        """Switch to mode *key* (the AUTO menu inside Traktor); unknown keys change nothing.

        Unlike a click that cycles the mode, a running fade carries on unless the
        choice is Off: changing to another mode must not cut a fade-in halfway.
        """
        keys = [mode[0] for mode in _AUTO_MODES]
        if key not in keys:
            return
        self._auto_mode_idx = keys.index(key)
        AUTOMATION_MODE.set(key, self._settings)
        if key == "off":
            self.cancel_automix_requested.emit()
        _log.info("automation mode: %s", key)
        self._apply_auto_mode(show_tooltip=True)

    def _cancel_auto_transition(self) -> None:
        """Cancel an in-progress Auto DJ transition without changing mode."""
        self._auto_unlock_timer.stop()
        if not self._auto_loading:
            return
        self._auto_loading = False
        self.cancel_automix_requested.emit()
        self._update_auto_btn_state()

    def _apply_auto_mode(self, show_tooltip: bool = False) -> None:
        key, _icon, _color, tooltip = _AUTO_MODES[self._auto_mode_idx]
        self.auto_btn.setToolTip(tooltip)
        self._auto_loading = False  # cancel any in-progress transition on every mode change
        self._auto_unlock_timer.stop()
        self._update_auto_btn_state()
        # Only where the button is on screen: inside Traktor (integrated mode) TK's window is
        # never shown, and the tip came up at a meaningless spot far from Traktor.
        if show_tooltip and self.auto_btn.isVisible():
            QToolTip.showText(
                self.auto_btn.mapToGlobal(QPoint(0, self.auto_btn.height())),
                tooltip,
                self.auto_btn,
            )
        elif show_tooltip:
            # Inside Traktor: no mode text beside the band's AUTO button any more; its name under
            # the button for a moment (User, 2026-11-24). Nothing happens elsewhere.
            from ui import traktor_hint

            traktor_hint.show("AUTO", tooltip.split("\n")[0])
        if key == "auto":
            self._auto_timer.start()
        else:
            self._auto_timer.stop()

    def _update_auto_btn_state(self) -> None:
        """Refresh auto_btn colour to reflect armed / transition-in-progress state."""
        key, icon, base_color, _tooltip = _AUTO_MODES[self._auto_mode_idx]
        state = (key, key == "auto" and bool(self._auto_loading))
        if state != getattr(self, "_announced_auto", None):
            self._announced_auto = state
            self.auto_state_changed.emit(*state)
        if key == "auto":
            color = "#ffaa44" if self._auto_loading else "#bdf164"
        else:
            color = base_color
        # Same box as its siblings in the strip, whatever the glyph's font.
        self.auto_btn.setIcon(glyph_icon(icon, color, box=_SIDE_ICON_PX))
        self.auto_btn.setStyleSheet(
            "QPushButton { padding: 0; background: transparent; border: none; }"
            "QPushButton:hover { background: #373737; border-radius: 5px; }"
            "QPushButton:pressed { background: #448bf6; border-radius: 5px; }"
        )

    def _show_auto_btn_menu(self, pos) -> None:
        """Right-click context menu on the automation button."""
        key = _AUTO_MODES[self._auto_mode_idx][0]
        menu = QMenu(self)
        if key == "auto" and self._auto_loading:
            act = menu.addAction("Cancel transition")
            act.triggered.connect(self._cancel_auto_transition)
        else:
            menu.addAction("Left-click to cycle mode").setEnabled(False)
        menu.exec(self.auto_btn.mapToGlobal(pos))

    # ── Auto DJ ───────────────────────────────────────────────────────────────

    def _auto_mode_tick(self) -> None:
        """Fires every second while Auto DJ is active; triggers transitions on cue."""
        if _AUTO_MODES[self._auto_mode_idx][0] != "auto":
            self._auto_timer.stop()
            return

        if self._auto_loading:
            return  # transition already in flight

        # Need exactly one on-air deck to monitor
        on_air = [
            d
            for d in _DECK_LETTERS
            if d in self._playing_decks and self._deck_volume.get(d, 0.0) > 0.1
        ]
        if len(on_air) != 1:
            self._auto_why(f"waits for exactly one deck playing on air ({len(on_air)} now)")
            return

        active = on_air[0]
        info = self._deck_info.get(active, {})
        track_length = info.get("track_length", 0.0)
        if not track_length:
            self._auto_why(f"deck {active}: no track length known")
            return

        # The playhead when Traktor reports it (a track started halfway, or moved, mixes at its
        # real end); else the time since play started, as if from the top.
        progress = self._deck_progress.get(active)
        play_start = self._deck_play_start.get(active)
        if progress:
            remaining = max(0.0, track_length * (1.0 - progress))
        elif play_start is not None:
            remaining = max(0.0, track_length - (_time.time() - play_start))
        else:
            self._auto_why(f"deck {active}: neither its playhead nor its play start was seen")
            return

        # Total automix time: phase-1 + phase-2; the load comes LOAD_LEAD_S before it starts.
        ramp_bars = RAMP_BARS.get(self._settings)
        bpm = self._master_bpm or info.get("bpm") or 120.0
        bar_s = (60.0 / bpm) * 4.0
        trans_s = bar_s * ramp_bars * 2.0
        # At the track's last hotcue (its outro, User 2026-11-24), else ending with the track.
        cue = info.get("last_hotcue_s")
        start_at = mix_start_s(track_length, cue, trans_s)
        position = track_length - remaining

        if position < start_at - LOAD_LEAD_S:
            where = "at its last hotcue" if start_at == cue else "to end with the track"
            self._auto_why(f"deck {active}: mixes at {start_at:.0f} s ({where})")
            return  # nothing to do yet

        path = self._pick_auto_track()
        if not path:
            self._auto_why("no result to load (TK's list is empty)")
            return

        # Find first deck that is NOT currently on-air
        free = next(
            (
                d
                for d in _DECK_LETTERS
                if not (d in self._playing_decks and self._deck_volume.get(d, 0.0) > 0.1)
            ),
            None,
        )
        if free is None:
            self._auto_why("no free deck to load")
            return  # all decks busy — can't load anywhere

        # Lock against re-entry; release after the full transition + buffer.
        # NB: we do NOT preemptively mark `path` as played — the playtime tick
        # adds it to _session_played once it crosses the 60s threshold. To avoid
        # picking the same track twice in this transition window, _pick_auto_track
        # also excludes tracks currently loaded on any deck.
        self._auto_loading = True
        self._update_auto_btn_state()
        self._auto_unlock_timer.start(int((trans_s + 8.0) * 1000))

        _log.info(
            "Auto DJ: %.0fs remaining — loading %s → deck %s",
            remaining,
            Path(path).name,
            free,
        )
        self._load_to_deck(path, free)

    def _auto_why(self, reason: str) -> None:
        """Why Auto DJ waits, logged once per change (a mode that seems dead says why)."""
        if getattr(self, "_auto_last_why", None) != reason:
            self._auto_last_why = reason
            _log.info("Auto DJ: %s", reason)

    def _pick_auto_track(self) -> str | None:
        """Random track from the top-20 results that hasn't been played this session
        and isn't already loaded on a deck."""
        if not self._filtered_results:
            return None

        loaded = {(self._deck_info.get(d) or {}).get("file_path") for d in _DECK_LETTERS}
        loaded.discard(None)
        loaded.discard("")
        excluded = self._session_played | loaded

        TOP_N = 20
        pool = self._filtered_results[:TOP_N]
        unplayed = [r for r in pool if r["row"]["path"] not in excluded]

        if not unplayed:
            # Expand to top-40 before giving up on uniqueness
            pool = self._filtered_results[: min(TOP_N * 2, len(self._filtered_results))]
            unplayed = [r for r in pool if r["row"]["path"] not in excluded]

        if not unplayed:
            unplayed = pool  # all candidates played — loop back through top-N

        return random.choice(unplayed)["row"]["path"]

    def _on_auto_transition_done(self) -> None:
        """Called when the Auto DJ transition lock expires; refresh button state."""
        self._auto_loading = False
        self._update_auto_btn_state()

    def _tick_play_seconds(self) -> None:
        """Accumulate playtime for each playing deck's loaded track.

        Marks a track as 'played this session' once cumulative play time crosses
        PLAYED_THRESHOLD_S, then dims its visible rows in place (cheap repaint —
        no rerank).
        """
        interval_s = self._play_tick.interval() / 1000.0
        newly_played: list[str] = []
        for deck in self._playing_decks:
            path = (self._deck_info.get(deck) or {}).get("file_path")
            if not path:
                continue
            prev = self._track_play_seconds.get(path, 0.0)
            new = prev + interval_s
            self._track_play_seconds[path] = new
            if prev < PLAYED_THRESHOLD_S <= new and path not in self._session_played:
                self._session_played.add(path)
                newly_played.append(path)
        if newly_played:
            self._dim_played_rows(set(newly_played))

    def _dim_played_rows(self, paths: set[str]) -> None:
        """Re-foreground visible rows whose path just transitioned to 'played'.

        Dims the same cells _populate_row dims for a played track — the
        artist/album/title text, the detected-style labels and the plain
        informational columns — so the row looks as it will after the next
        rebuild. Metric cells keep their match colours, as they do there. No
        rerank, so this is cheap regardless of result count.
        """
        played_color = QColor("#666688")
        text_cols = [
            ci
            for ci, c in enumerate(self._col_model)
            if c["metric"] is None and c["id"] != "style_labels"
        ]
        style_cols = [ci for ci, c in enumerate(self._col_model) if c["id"] == "style_labels"]
        for ri, path in self.table._row_paths.items():
            if path not in paths:
                continue
            for ci in text_cols:
                it = self.table.item(ri, ci)
                if it is not None:
                    it.setForeground(played_color)
            if style_cols and ri < len(self._filtered_results):
                row = self._filtered_results[ri]["row"]
                for ci in style_cols:
                    self._set_genre_cell(ri, ci, row, dim=True)
