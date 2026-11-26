"""Settings, Traktor tab: which install is in use, and the two preferences.

The install picker decides where the collection, the QML and the app
itself resolve to, so everything else in Kommander follows this choice.
"""

from __future__ import annotations

import sys

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
)

from core.idle import DisplayWakeLock
from core.prefs import KEEP_DISPLAY_AWAKE
from core.traktor_install import SETTINGS_KEY as TRAKTOR_INSTALL_KEY
from core.traktor_install import choose as choose_install
from core.traktor_install import discover as discover_installs
from ui.settings_common import (
    SYNC_ALL_ON_CONNECT_KEY,
)
from ui.style import T


class _TraktorPageMixin:
    """Install picker and preferences. Widgets and state belong to ``SettingsTab``."""

    # ── Traktor tab, section 1: which Traktor ──────────────────────────────

    def _build_install_group(self):
        """Which installation everything — collection included — resolves to."""
        grp = QGroupBox("TRAKTOR")
        lay = QVBoxLayout(grp)
        lay.setSpacing(8)

        # Only worth a picker when there is a choice; the resolved paths below
        # answer "which one is it using?" either way.
        self.install_combo: QComboBox | None = None
        self._installs = discover_installs()
        if self._installs:
            row = QHBoxLayout()
            row.setSpacing(8)
            lbl = QLabel("Version:")
            lbl.setStyleSheet(f"color: {T.TEXT_DIM};")
            row.addWidget(lbl)
            self.install_combo = QComboBox()
            for inst in self._installs:
                self.install_combo.addItem(inst.label, str(inst.app_path))
            self.install_combo.setToolTip(
                "Which Traktor installation Kommander launches, watches,\n"
                "reads the collection from, and installs the D2 bridge into.\n"
                "All of them follow this one choice."
            )
            self.install_combo.currentIndexChanged.connect(self._on_install_changed)
            row.addWidget(self.install_combo, stretch=1)
            lay.addLayout(row)

        # Always show what actually resolved — a wrong pick is otherwise
        # invisible until something behaves strangely.
        self.install_paths_lbl = QLabel("")
        self.install_paths_lbl.setStyleSheet(f"color: {T.TEXT_DIM}; font-size: 10px;")
        self.install_paths_lbl.setWordWrap(True)
        lay.addWidget(self.install_paths_lbl)
        self._refresh_install_paths()
        return grp

    # ── Section 2: the two preferences ─────────────────────────────────────

    def _build_traktor_prefs_group(self):
        grp = QGroupBox("PREFERENCES")
        lay = QVBoxLayout(grp)
        lay.setSpacing(8)

        # macOS + Windows only — launch_traktor() has no other backends.
        self.traktor_autostart_cb: QCheckBox | None = None
        if sys.platform in ("darwin", "win32"):
            self.traktor_autostart_cb = QCheckBox(
                "Start Traktor automatically when Kommander launches"
            )
            self.traktor_autostart_cb.setToolTip(
                "If Traktor isn't running when Kommander starts, launch it in\n"
                "the background — Kommander stays on top and snaps over the\n"
                "library as soon as Traktor's window appears."
            )
            self.traktor_autostart_cb.stateChanged.connect(self._on_traktor_autostart_changed)
            lay.addWidget(self.traktor_autostart_cb)

        # Traktor's Sync is per deck and there is no "all decks" switch
        # anywhere in it — not in Preferences, not in a mapping. The bridge
        # can write every deck's sync.enabled, so Kommander offers it here.
        self.sync_all_cb = QCheckBox("Turn Sync on for every deck when Traktor connects")
        self.sync_all_cb.setToolTip(
            "Traktor only has a per-deck Sync button and no way to arm them\n"
            "all. With this on, Kommander presses it for you on decks A–D as\n"
            "soon as the bridge connects, and again whenever a track is\n"
            "loaded — a deck armed while empty does not always keep it."
        )
        self.sync_all_cb.stateChanged.connect(self._on_sync_all_changed)
        lay.addWidget(self.sync_all_cb)

        # OS power assertions (IOKit / SetThreadExecutionState) — no
        # permission grant and no cursor nudging.
        self.keep_awake_cb: QCheckBox | None = None
        if DisplayWakeLock.supported():
            self.keep_awake_cb = QCheckBox("Keep the display awake while Traktor is playing")
            self.keep_awake_cb.setToolTip(
                "Stops the screensaver and display sleep while a deck is\n"
                "playing — the mouse may not move for an hour while you're on\n"
                "the controller. Lets go a minute and a half after the last\n"
                "deck stops, and at once when Traktor closes."
            )
            self.keep_awake_cb.stateChanged.connect(self._on_keep_awake_changed)
            lay.addWidget(self.keep_awake_cb)

        if not lay.count():
            note = QLabel("Nothing to configure on this platform.")
            note.setStyleSheet(f"color: {T.TEXT_DIM}; font-size: 11px;")
            lay.addWidget(note)
        return grp

    def _current_install(self):
        """The install the picker is on, or the auto-chosen one."""
        stored = self._settings.value(TRAKTOR_INSTALL_KEY, "", type=str)
        return choose_install(self._installs, stored_path=stored)

    def _on_install_changed(self, idx: int) -> None:
        if self.install_combo is None or idx < 0:
            return
        path = self.install_combo.itemData(idx)
        if not path:
            return
        # The collection follows the install — everything that reads the
        # library resolves it from this one choice.
        self._settings.setValue(TRAKTOR_INSTALL_KEY, path)
        self._settings.sync()
        self._refresh_install_paths()
        self.traktor_config_changed.emit()

    def _refresh_install_paths(self) -> None:
        """Show the paths the current choice resolves to."""
        inst = self._current_install()
        if inst is None:
            self.install_paths_lbl.setText("No Traktor installation found in the usual locations.")
            return
        if self.install_combo is not None:
            wanted = str(inst.app_path)
            for i in range(self.install_combo.count()):
                if self.install_combo.itemData(i) == wanted:
                    self.install_combo.blockSignals(True)
                    self.install_combo.setCurrentIndex(i)
                    self.install_combo.blockSignals(False)
                    break
        self.install_paths_lbl.setText(
            f"{inst.label}\n"
            f"app: {inst.app_path}\n"
            f"qml: {inst.csi_dir or '— not found'}\n"
            f"lib: {inst.nml_path or '— not found'}"
        )
        # Section 3 does not exist yet the first time this runs.
        if hasattr(self, "d2_device_msg"):
            self._refresh_d2_device()

    def _on_sync_all_changed(self, state: int) -> None:
        self._settings.setValue(SYNC_ALL_ON_CONNECT_KEY, state == 2)

    def _on_keep_awake_changed(self, state: int) -> None:
        KEEP_DISPLAY_AWAKE.set(state == 2, self._settings)

    def _on_traktor_autostart_changed(self, state: int) -> None:
        self._settings.setValue("traktor_autostart", bool(state))
