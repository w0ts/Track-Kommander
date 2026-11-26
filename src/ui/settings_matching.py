"""Settings, Matching tab: the COMPUTE group and the matching preferences.

COMPUTE decides which features a scan works out at all; the BPM, harmonic
and transpose controls and the result columns decide how matches are
ranked and shown. The MATCHING group's widgets are built in
``SettingsTab._build_ui``; the slots that react to them are here.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QGridLayout,
    QGroupBox,
    QLabel,
    QVBoxLayout,
)

from core.features import key_range_key
from core.prefs import BPM_WINDOW_PCT
from ui.now_playing import rescol_key
from ui.style import T


class _MatchingPageMixin:
    """Compute and matching slots. Widgets and state belong to ``SettingsTab``."""

    # ── Matching slots ──────────────────────────────────────────────────────────

    def _build_compute_group(self) -> QGroupBox:
        """Which features a scan computes at all — the first of two switches.

        This one decides what analysis costs. The second, in the library
        stats window, decides which of the computed features rank tracks and
        costs nothing. A feature switched off here keeps whatever it last
        computed; switching it back on backfills only that feature.
        """
        from core.features import axis_compute_key
        from core.scan_plan import computable_features

        grp = QGroupBox("COMPUTE")
        lay = QVBoxLayout(grp)
        lay.setSpacing(6)
        note = QLabel(
            "What a library scan works out for each track. Turning one off "
            "makes scans faster and leaves that feature as it is; turning it "
            "back on backfills only that feature. Which of them actually rank "
            "tracks is set in the library stats window."
        )
        note.setWordWrap(True)
        note.setStyleSheet(f"color: {T.TEXT_DIM}; font-size: 11px;")
        lay.addWidget(note)

        self.compute_boxes: dict[str, QCheckBox] = {}
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(2)
        computed = list(computable_features())
        for i, spec in enumerate(computed):
            cb = QCheckBox(spec.label)
            cb.setToolTip(f"{spec.desc or spec.label}\n\nCost: {spec.cost}")
            cb.setChecked(self._settings.value(axis_compute_key(spec.key), True, type=bool))
            cb.toggled.connect(lambda on, k=spec.key: self._on_compute_toggled(k, on))
            self.compute_boxes[spec.key] = cb
            grid.addWidget(cb, i // 3, i % 3)
        lay.addLayout(grid)

        self.compute_summary = QLabel("")
        self.compute_summary.setStyleSheet(f"color: {T.TEXT_DIM}; font-size: 11px;")
        lay.addWidget(self.compute_summary)
        self._refresh_compute_summary()
        return grp

    def _on_compute_toggled(self, key: str, on: bool) -> None:
        from core.features import axis_compute_key

        self._settings.setValue(axis_compute_key(key), bool(on))
        self._refresh_compute_summary()

    def _refresh_compute_summary(self) -> None:
        on = sum(1 for cb in self.compute_boxes.values() if cb.isChecked())
        total = len(self.compute_boxes)
        from core.features import BY_KEY

        heavy = sum(
            1
            for k, cb in self.compute_boxes.items()
            if cb.isChecked() and BY_KEY[k].cost == "heavy"
        )
        self.compute_summary.setText(
            f"{on} of {total} features computed" + (f" — {heavy} of them heavy." if heavy else ".")
        )

    def _on_transpose_range_changed(self, value: int) -> None:
        self._settings.setValue(key_range_key(), int(value))
        self.matching_config_changed.emit()

    def _on_bpm_restrict_changed(self, state: int) -> None:
        enabled = bool(state)
        self.bpm_slider.setEnabled(enabled)
        self.bpm_pct_lbl.setEnabled(enabled)
        self._update_bpm_label()
        self._settings.setValue("bpm_restrict", enabled)
        self.matching_config_changed.emit()

    def _on_harmonic_only_changed(self, state: int) -> None:
        self._settings.setValue("harmonic_only", bool(state))
        self.matching_config_changed.emit()

    def _on_bpm_slider_changed(self, value: int) -> None:
        self._update_bpm_label()
        BPM_WINDOW_PCT.set(value, self._settings)
        self.matching_config_changed.emit()

    def _update_bpm_label(self) -> None:
        self.bpm_pct_lbl.setText(
            f"±{self.bpm_slider.value()}%" if self.bpm_restrict_cb.isChecked() else "—"
        )

    def _on_col_toggled(self, cid: str, checked: bool) -> None:
        self._settings.setValue(rescol_key(cid), checked)
        self.columns_config_changed.emit()
