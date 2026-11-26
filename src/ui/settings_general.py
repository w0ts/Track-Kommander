"""Settings, General tab: the INTERFACE group and the AutoMix ramp.

Tooltip lifetime, logging switches, the update check and the log
buttons: cross-cutting options that belong to no other page.
"""

from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from core.prefs import RAMP_BARS, VERBOSE_LOG
from ui.style import T


class _GeneralPageMixin:
    """The INTERFACE group. Widgets and state belong to ``SettingsTab``."""

    def _build_general_group(self) -> QGroupBox:
        """Cross-cutting bits that belong to no other group."""
        grp = QGroupBox("INTERFACE")
        lay = QVBoxLayout(grp)
        lay.setSpacing(8)

        # Result-row tooltip lifetime. Qt derives a tooltip's auto-hide delay
        # from its text length, and the row tooltip is a big HTML block with
        # cover art — left to Qt it effectively never goes away.
        tip_row = QHBoxLayout()
        tip_row.setSpacing(8)
        tip_lbl = QLabel("Track tooltips:")
        tip_lbl.setStyleSheet(f"color: {T.TEXT_DIM};")
        tip_row.addWidget(tip_lbl)
        self.tooltip_combo = QComboBox()
        self.tooltip_combo.addItem("Off", 0)
        for secs in (3, 5, 8, 15, 30):
            self.tooltip_combo.addItem(f"{secs} seconds", secs * 1000)
        self.tooltip_combo.setFixedWidth(130)
        self.tooltip_combo.setToolTip(
            "The score breakdown shown when resting on a track in the\n"
            "results or on a deck button. Off hides them; otherwise, how\n"
            "long one stays up. They also go as soon as you move the mouse."
        )
        self.tooltip_combo.currentIndexChanged.connect(self._on_tooltip_ms_changed)
        tip_row.addWidget(self.tooltip_combo)
        tip_row.addStretch()
        lay.addLayout(tip_row)

        # Warnings the user silenced with "Don't show this again" are otherwise
        # unreachable — there has to be a way back.
        warn_row = QHBoxLayout()
        warn_row.setSpacing(8)
        self.reset_warnings_btn = QPushButton("Show all warnings again")
        self.reset_warnings_btn.setToolTip(
            'Bring back every warning dismissed with "Don\'t show this again".'
        )
        self.reset_warnings_btn.clicked.connect(self._on_reset_warnings)
        warn_row.addWidget(self.reset_warnings_btn)
        self.reset_warnings_lbl = QLabel("")
        self.reset_warnings_lbl.setStyleSheet(f"color: {T.TEXT_DIM}; font-size: 11px;")
        warn_row.addWidget(self.reset_warnings_lbl)
        warn_row.addStretch()
        lay.addLayout(warn_row)

        # The log is INFO by default. DEBUG is for chasing something, and a
        # frozen app cannot easily be handed TK_LOG_LEVEL — so it is here.
        self.verbose_log_cb = QCheckBox("Verbose logging")
        self.verbose_log_cb.setToolTip(
            "Write DEBUG lines to log.log as well. Off, the log holds INFO and "
            "up.\nTakes effect at once; TK_LOG_LEVEL in the environment "
            "overrides it."
        )
        self.verbose_log_cb.toggled.connect(self._on_verbose_log_toggled)
        lay.addWidget(self.verbose_log_cb)

        self.ui_watchdog_cb = QCheckBox("Log UI lags")
        self.ui_watchdog_cb.setToolTip(
            "Whenever the window stops responding for more than 150 ms, write\n"
            "what it was busy with to log.log. For finding what makes it lag.\n"
            "Takes effect at once; TK_WATCHDOG=0 or 1 in the environment\n"
            "overrides it."
        )
        self.ui_watchdog_cb.toggled.connect(self._on_ui_watchdog_toggled)
        lay.addWidget(self.ui_watchdog_cb)

        from core.update_check import SETTING_KEY as _UPDATE_KEY

        self._update_key = _UPDATE_KEY
        self.update_check_cb = QCheckBox("Check for new versions at startup")
        self.update_check_cb.setToolTip(
            "Once per launch, ask GitHub whether a newer Track Kommander has\n"
            "been released, and say so above the results if it has. Sends\n"
            "nothing about you or your library."
        )
        self.update_check_cb.toggled.connect(
            lambda on: self._settings.setValue(self._update_key, bool(on))
        )
        lay.addWidget(self.update_check_cb)

        # The Debug menu's two actions, here too: on Windows the menu bar
        # is hidden, and this is where someone looks when asked for a log.
        from ui.log_actions import share_log, show_log_folder

        log_row = QHBoxLayout()
        self.show_log_btn = QPushButton("Show log folder")
        self.show_log_btn.clicked.connect(lambda: show_log_folder(self))
        log_row.addWidget(self.show_log_btn)
        self.share_log_btn = QPushButton("Share log…")
        self.share_log_btn.setToolTip(
            "Zip the logs and a short diagnostics report onto the Desktop,\n"
            "ready to send. No settings and no music are included."
        )
        self.share_log_btn.clicked.connect(lambda: share_log(self))
        log_row.addWidget(self.share_log_btn)
        log_row.addStretch()
        lay.addLayout(log_row)
        return grp

    def _on_tooltip_ms_changed(self, idx: int) -> None:
        if idx < 0:
            return
        self._settings.setValue("results_tooltip_ms", self.tooltip_combo.itemData(idx))
        self.tooltip_config_changed.emit()

    def _on_verbose_log_toggled(self, on: bool) -> None:
        import logging
        import os

        from core.runtime import log_level

        VERBOSE_LOG.set(bool(on), self._settings)
        logging.root.setLevel(log_level(os.environ, verbose=bool(on)))

    def _on_ui_watchdog_toggled(self, on: bool) -> None:
        import os

        from ui.ui_watchdog import SETTING_KEY, set_ui_watchdog, watchdog_wanted

        self._settings.setValue(SETTING_KEY, bool(on))
        set_ui_watchdog(watchdog_wanted(os.environ, bool(on)))

    def _on_reset_warnings(self) -> None:
        from ui.dialogs import reset_dismissed_warnings

        reset_dismissed_warnings()
        self.reset_warnings_lbl.setText("Restored.")
        QTimer.singleShot(2500, lambda: self.reset_warnings_lbl.setText(""))

    # ── AutoMix slot ──────────────────────────────────────────────────────────

    def _on_ramp_bars_changed(self, _idx: int) -> None:
        bars = self.ramp_bars_combo.currentData()
        if bars is not None:
            RAMP_BARS.set(bars, self._settings)
