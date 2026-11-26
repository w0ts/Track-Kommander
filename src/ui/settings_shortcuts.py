"""Settings, Controls tab: keyboard shortcuts and the S4 learn mode.

One row per action with a keyboard binding and an S4 MK2 control, a
conflict warning, and the "Identify S4 control" button that arms the
watcher so a touched control reports its name.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from core import actions, keyforward
from ui.style import T
from workers.s4mk2_watcher import S4MK2Watcher


class _ShortcutsPageMixin:
    """The shortcuts table. Widgets and state belong to ``SettingsTab``."""

    # ── Shortcuts ───────────────────────────────────────────────────────────

    #: Explains why a whole group is empty, so 16 blank rows don't read as a bug.
    _GROUP_HINTS = {
        "Transport": "— unbound: these keys go to Traktor. Bind one to handle it here instead.",
    }

    def _build_shortcuts_group(self) -> QGroupBox:
        grp = QGroupBox("SHORTCUTS")
        lay = QVBoxLayout(grp)
        lay.setSpacing(8)

        note = QLabel(
            "Kommander only claims the keys it needs — moving through the "
            "result list and loading a deck. <b>Every other key is passed "
            "straight through to Traktor</b>, so your own Traktor mapping "
            "keeps working while this window has focus.<br><br>"
            "Click a shortcut to change it, then press the keys. Backspace "
            "clears it (sending that key to Traktor instead), Escape cancels. "
            "Keys are remembered by their position on the keyboard, so they "
            "stay put on AZERTY and QWERTZ."
        )
        note.setWordWrap(True)
        note.setTextFormat(Qt.TextFormat.RichText)
        note.setStyleSheet(f"color: {T.TEXT_DIM}; font-size: 11px;")
        lay.addWidget(note)

        if not keyforward.supported():
            warning = (
                "⚠  Forwarding keys to Traktor is not available on this "
                "platform. Unbound keys simply do nothing."
            )
        else:
            warning = ""

        if warning:
            warn = QLabel(warning)
            warn.setWordWrap(True)
            warn.setStyleSheet(f"color: {T.ORANGE}; font-size: 11px;")
            lay.addWidget(warn)

        self._shortcut_buttons: dict[str, object] = {}
        self._s4_buttons: dict[str, object] = {}

        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(4)
        for col, header in enumerate(("Action", "Keyboard", "S4 MK2")):
            h = QLabel(header)
            h.setStyleSheet(f"color: {T.TEXT_DIM}; font-size: 10px; font-weight: bold;")
            grid.addWidget(h, 0, col)

        row = 1
        for group in actions.GROUPS:
            members = [a for a in actions.ACTIONS if a.group == group]
            if not members:
                continue
            hint = self._GROUP_HINTS.get(group, "")
            gl = QLabel(group.upper() + (f"   {hint}" if hint else ""))
            gl.setStyleSheet(f"color: {T.TEXT_DIM}; font-size: 10px; margin-top: 6px;")
            grid.addWidget(gl, row, 0, 1, 3)
            row += 1
            for action in members:
                label = QLabel(action.label)
                label.setStyleSheet(f"color: {T.TEXT};")
                grid.addWidget(label, row, 0)
                grid.addWidget(self._make_key_button(action), row, 1)
                grid.addWidget(self._make_s4_button(action), row, 2)
                row += 1

        lay.addLayout(grid)

        self.shortcut_conflicts = QLabel("")
        self.shortcut_conflicts.setWordWrap(True)
        self.shortcut_conflicts.setStyleSheet(
            f"color: {T.ORANGE}; font-size: 11px; background: #2a1f00; "
            f"border: 1px solid {T.ORANGE}; border-radius: 4px; padding: 6px 8px;"
        )
        lay.addWidget(self.shortcut_conflicts)

        btn_row = QHBoxLayout()
        self.reset_shortcuts_btn = QPushButton("Restore default shortcuts")
        self.reset_shortcuts_btn.clicked.connect(self._on_reset_shortcuts)
        btn_row.addWidget(self.reset_shortcuts_btn)

        # Naming a button in a list is one thing; knowing which physical
        # control it is on the deck is another. This arms the watcher so the
        # next control reports itself instead of firing its action.
        self.s4_identify_btn = QPushButton("Identify S4 control…")
        self.s4_identify_btn.setCheckable(True)
        self.s4_identify_btn.setToolTip(
            "Press to arm, then touch a control on the S4 — it will be named\n"
            "here instead of doing what it is bound to."
        )
        self.s4_identify_btn.toggled.connect(self._on_s4_identify_toggled)
        btn_row.addWidget(self.s4_identify_btn)

        self.s4_learn_lbl = QLabel("")
        self.s4_learn_lbl.setStyleSheet(f"color: {T.TEXT_DIM}; font-size: 11px;")
        btn_row.addWidget(self.s4_learn_lbl)
        btn_row.addStretch()
        lay.addLayout(btn_row)

        self._refresh_conflicts()
        return grp

    def _make_key_button(self, action):
        from ui.shortcut_edit import ShortcutButton

        btn = ShortcutButton(actions.binding_for(action.id))
        btn.setMinimumWidth(150)
        btn.captured.connect(lambda binding, aid=action.id: self._on_key_captured(aid, binding))
        self._shortcut_buttons[action.id] = btn
        return btn

    def _make_s4_button(self, action):
        """A combo of the S4's controls — the device has a fixed, small set.

        A capture button would be worse here: it needs the hardware present and
        connected, and there are only six controls to choose from.
        """
        combo = QComboBox()
        combo.addItem("—", "")
        for control, label in S4MK2Watcher.CONTROLS:
            combo.addItem(label, control)
        current = actions.binding_for(action.id, s4=True)
        idx = combo.findData(current.code if current else "")
        combo.setCurrentIndex(max(0, idx))
        combo.setMinimumWidth(170)
        combo.currentIndexChanged.connect(
            lambda _i, aid=action.id, c=combo: self._on_s4_selected(aid, c)
        )
        self._s4_buttons[action.id] = combo
        return combo

    def _on_key_captured(self, action_id: str, binding) -> None:
        actions.set_binding(action_id, binding)
        self._refresh_conflicts()

    def _on_s4_selected(self, action_id: str, combo) -> None:
        control = combo.currentData()
        actions.set_binding(
            action_id,
            actions.Binding("s4", control, 0, combo.currentText()) if control else None,
            s4=True,
        )
        self._refresh_conflicts()

    def _on_reset_shortcuts(self) -> None:
        actions.reset_all()
        for action_id, btn in self._shortcut_buttons.items():
            btn.set_binding(actions.binding_for(action_id))
        for action_id, combo in self._s4_buttons.items():
            b = actions.binding_for(action_id, s4=True)
            combo.blockSignals(True)
            combo.setCurrentIndex(max(0, combo.findData(b.code if b else "")))
            combo.blockSignals(False)
        self._refresh_conflicts()

    def _refresh_conflicts(self) -> None:
        """Two actions on one control always means one will never fire."""
        clashes = {**actions.conflicts(), **actions.conflicts(s4=True)}
        if not clashes:
            self.shortcut_conflicts.setVisible(False)
            return
        lines = [
            f"⚠  {key} is bound to "
            + ", ".join(actions.BY_ID[a].label for a in ids)
            + " — only the first will fire."
            for key, ids in clashes.items()
        ]
        self.shortcut_conflicts.setText("\n".join(lines))
        self.shortcut_conflicts.setVisible(True)

    def set_s4_watcher(self, watcher) -> None:
        """Remember the live watcher so learn mode can be armed."""
        self._s4_watcher = watcher

    def _on_s4_identify_toggled(self, armed: bool) -> None:
        watcher = getattr(self, "_s4_watcher", None)
        if watcher is None:
            self.s4_identify_btn.setChecked(False)
            self.s4_learn_lbl.setText("S4 not connected.")
            return
        watcher.set_learning(armed)
        self.s4_learn_lbl.setText("Press a control on the S4…" if armed else "")

    def on_s4_control_learned(self, control: str) -> None:
        """A control was pressed while identify mode was armed."""
        label = dict(S4MK2Watcher.CONTROLS).get(control, control)
        bound = actions.match_s4(control)
        what = actions.BY_ID[bound].label if bound else "not bound"
        self.s4_learn_lbl.setText(f"{label} — {what}")
        self.s4_identify_btn.setChecked(False)  # one-shot; re-arm to repeat
