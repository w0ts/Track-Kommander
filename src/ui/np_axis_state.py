"""Matching-axis state for the Now Playing tab: transpose, toggles, persistence.

Which axes are active or synced, how far the Key knob may move, and how the
axis settings are collected for a ranking call and written back. The card
widgets that show this state are in ``np_axis_cards``; the two are separate
mixins only to keep each file small, and both work on state the tab owns
(``_settings``, ``_axis_widgets``, the source row). ``NowPlayingTab`` in
``ui.now_playing`` provides those attributes.
"""

from __future__ import annotations

from core.features import (
    ActiveAxis,
    axis_active_key,
    axis_strength_key,
    axis_sync_key,
    axis_value_key,
)


class _AxisStateMixin:
    """Axis activation, sync, Key-knob transpose and settings round-trip."""

    def _transpose_for(self, path: str) -> int:
        """The Key-knob shift this track's match needs, in semitones.

        Zero in every case but a Key-T match that asked for one — and zero
        is sent, not skipped: a deck keeps the shift it was last left on, so
        loading in strict Key mode after a transposed mix would otherwise
        play the new track detuned by the old track's number.
        """
        from core.features import KeyMode

        if self._key_mode() is not KeyMode.TRANSPOSE:
            return 0
        for r in self._all_results:
            if r["row"]["path"] == path:
                return int(r.get("transpose") or 0)
        return 0

    def _transpose_range(self) -> int:
        """Semitones the Key axis may move, or 0 when it is not transposing.

        The limit is a setting (Settings → Matching): three semitones suits
        most crates, but how far a track can be moved before it stops
        sounding like itself is a matter of taste and genre.
        """
        from core.features import KeyMode, transpose_range

        if self._key_mode() is not KeyMode.TRANSPOSE:
            return 0
        return transpose_range()

    def _key_mode(self):
        """Key · Key-T · off, as the card is currently set."""
        from core.features import DEFAULT_KEY_MODE, KeyMode, axis_key_mode_key

        return KeyMode.from_name(
            self._settings.value(axis_key_mode_key(), DEFAULT_KEY_MODE.name, type=str)
        )

    def _cycle_key_mode(self) -> None:
        """The Key card's label was clicked: Key → Key-T → off → Key.

        One axis with three states rather than two axes asking the same
        question — only one answer belongs in the column.
        """
        from core.features import axis_key_mode_key, next_key_mode

        mode = next_key_mode(self._key_mode())
        self._settings.setValue(axis_key_mode_key(), mode.name)
        self._set_axis_active("key", mode.active)
        refs = self._axis_widgets.get("key")
        if refs is not None:
            refs["active"] = mode.active
            refs["label"].setText(mode.label)
            self._style_axis_row("key")
        self._refresh()

    def current_axes(self) -> list:
        """The matching axes as set now (core.features.ActiveAxis) — for the
        stats page's history replay."""
        return self._collect_axes()

    def _collect_axes(self) -> list:
        axes = []
        for key, refs in self._axis_widgets.items():
            # Off dimension → strength 0, so the matcher drops it entirely
            # while the slider's value is preserved for when it's re-enabled.
            strength = (refs["strength"].value() / 100.0) if refs.get("active", True) else 0.0
            value = refs["val"].value() / 100.0 if "val" in refs else 0.0
            # sync travels with the axis so the matcher can tell a target the
            # user dialled in from one mirrored off the current track — the
            # difference decides whether the axis can still rank with no track
            # loaded (see core.matcher.rank_matches_absolute).
            axes.append(
                ActiveAxis(
                    key=key,
                    value=value,
                    strength=strength,
                    sync=self._axis_sync(key),
                    max_semitones=(self._transpose_range() if key == "key" else 0),
                )
            )
        return axes

    def flush_settings(self) -> None:
        """Force pending QSettings writes (axis state, BPM restrict, …) to disk.

        Changes are normally flushed by a 600 ms debounce and on ``aboutToQuit``;
        this is called from the window's closeEvent so an abrupt quit of the
        frozen app can't drop the last edit before it persists.
        """
        self._persist_timer.stop()
        self._settings.sync()

    def _set_axis_active(self, key: str, active: bool) -> None:
        refs = self._axis_widgets.get(key)
        if not refs:
            return
        refs["active"] = active
        self._settings.setValue(axis_active_key(key), active)
        self._persist_timer.start()

    def _set_axis_sync(self, key: str, sync: bool) -> None:
        refs = self._axis_widgets.get(key)
        if not refs:
            return
        refs["sync"] = sync
        self._settings.setValue(axis_sync_key(key), sync)
        self._persist_timer.start()

    @staticmethod
    def _default_strength(spec) -> int:
        return 60 if spec.kind == "similarity" else (50 if spec.relational else 30)

    def _toggle_axis_active(self, key: str) -> None:
        refs = self._axis_widgets.get(key)
        if not refs:
            return
        turning_on = not self._axis_active(key)
        self._set_axis_active(key, turning_on)
        if turning_on:
            # Turning a dimension on with strength 0 would be a no-op — give it
            # back a sensible weight so it actually participates.
            if refs["strength"].value() == 0:
                s = self._default_strength(refs["spec"])
                refs["strength"].blockSignals(True)
                refs["strength"].setValue(s)
                refs["strength"].blockSignals(False)
                self._settings.setValue(axis_strength_key(key), s)
            if self._axis_sync(key):
                self._sync_axis_value_from_source(key)
        self._style_axis_row(key)
        self._refresh()

    def _toggle_axis_sync(self, key: str) -> None:
        # Turning sync on also turns the dimension on.
        self._set_axis_active(key, True)
        self._set_axis_sync(key, not self._axis_sync(key))
        if self._axis_sync(key):
            self._sync_axis_value_from_source(key)
        self._style_axis_row(key)
        self._refresh()

    def _on_axis_value_changed(self, key: str, value: int) -> None:
        self._settings.setValue(axis_value_key(key), value)
        self._persist_timer.start()
        # Touching the value drops the sync lock and turns the dimension on.
        if self._axis_sync(key):
            self._set_axis_sync(key, False)
        if not self._axis_active(key):
            self._set_axis_active(key, True)
        self._style_axis_row(key)
        self._schedule_refresh()

    def _on_axis_strength_changed(self, key: str, value: int) -> None:
        self._settings.setValue(axis_strength_key(key), value)
        self._persist_timer.start()
        # Strength is the on/off too: 0 disables the dimension, >0 enables it.
        self._set_axis_active(key, value > 0)
        self._style_axis_row(key)
        self._schedule_refresh()
