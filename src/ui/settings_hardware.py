"""Settings, Controls tab: the HARDWARE CONTROL group for the S4 MK2."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from ui.style import T


class _HardwarePageMixin:
    """The S4 MK2 switch and status. Widgets and state belong to ``SettingsTab``."""

    def _build_hw_control_group(self) -> QGroupBox:
        grp = QGroupBox("HARDWARE CONTROL")
        outer = QHBoxLayout(grp)
        outer.setSpacing(16)

        left = QVBoxLayout()
        left.setSpacing(6)

        # Checkbox + status on one row
        s4_row = QHBoxLayout()
        s4_row.setSpacing(8)
        self.s4mk2_cb = QCheckBox("Enable Traktor Kontrol S4 MK2 (HID)")
        self.s4mk2_cb.setToolTip(
            "Reads the S4 MK2 in parallel with Traktor.\n"
            "Works on macOS — HID is shareable between applications."
        )
        self.s4mk2_cb.stateChanged.connect(self._on_s4mk2_changed)
        s4_row.addWidget(self.s4mk2_cb, stretch=1)
        s4_row.addSpacing(16)
        self.s4mk2_dot = QLabel("●")
        self.s4mk2_dot.setStyleSheet("color: #555577; font-size: 14px;")
        s4_row.addWidget(self.s4mk2_dot)
        self.s4mk2_status = QLabel("Not started")
        self.s4mk2_status.setStyleSheet(f"color: {T.TEXT_DIM};")
        s4_row.addWidget(self.s4mk2_status)
        left.addLayout(s4_row)

        # The S4's controls used to be listed here as a static grid. They are
        # configurable now, so the table below is the only place that says what
        # each one does — two copies would drift apart.
        s4_hint = QLabel(
            "Hold <b>Right Shift</b> and use the browse encoder or the CH1–CH4 "
            "CUE buttons. Change what they do in the table below."
        )
        s4_hint.setTextFormat(Qt.TextFormat.RichText)
        s4_hint.setWordWrap(True)
        s4_hint.setStyleSheet(f"color: {T.TEXT_DIM}; font-size: 11px;")
        left.addWidget(s4_hint)

        # TSI file buttons
        btn_style = "QPushButton { text-align: left; padding: 4px 8px; font-size: 11px; }"
        lbl_style = (
            f"color: {T.TEXT_DIM}; font-size: 11px; padding: 4px 8px;"
            f" border: 1px solid #383838; border-radius: 4px;"
        )
        tsi_row = QHBoxLayout()
        tsi_row.setSpacing(6)
        tsi_src = QPushButton("📁  S4 Mk2 mapping.tsi  (this app)")
        tsi_src.setToolTip("Open the bundled Traktor/ folder in Finder.")
        tsi_src.setStyleSheet(btn_style)
        tsi_src.clicked.connect(self._open_bundled_traktor_root)
        tsi_row.addWidget(tsi_src)
        arrow = QLabel("→")
        arrow.setStyleSheet(f"color: {T.TEXT_DIM}; font-size: 14px;")
        arrow.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tsi_row.addWidget(arrow)
        tsi_dst = QLabel("🎛  Controller Manager  (File → Import)")
        tsi_dst.setStyleSheet(lbl_style)
        tsi_row.addWidget(tsi_dst)
        tsi_row.addStretch()
        left.addLayout(tsi_row)

        outer.addLayout(left, stretch=2)

        # Right column: TSI setup note
        tsi_info = QLabel(
            "In Traktor's <b>Controller Manager</b>, import <b>S4 Mk2 mapping.tsi</b> "
            "and <b>remove the default S4 MK2 mapping</b> — this prevents Traktor from "
            "seizing the device exclusively."
        )
        tsi_info.setTextFormat(Qt.TextFormat.RichText)
        tsi_info.setWordWrap(True)
        tsi_info.setAlignment(Qt.AlignmentFlag.AlignTop)
        tsi_info.setStyleSheet(f"color: {T.TEXT_DIM}; font-size: 11px;")
        outer.addWidget(tsi_info, stretch=1)

        return grp

    def _on_s4mk2_changed(self, state: int) -> None:
        self._settings.setValue("s4mk2_enabled", bool(state))
        self.s4mk2_config_changed.emit()

    def set_s4mk2_status(self, connected: bool, message: str = "") -> None:
        color = T.GREEN if connected else "#555577"
        self.s4mk2_dot.setStyleSheet(f"color: {color}; font-size: 14px;")
        self.s4mk2_status.setText(message or ("Connected" if connected else "Not connected"))
