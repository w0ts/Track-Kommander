"""Widgets for editing a keyboard or S4 binding.

A capture button rather than a text field: shortcuts are pressed, not typed,
and a physical key has no spelling to type anyway — on AZERTY the play/pause
key produces ``Z`` while remaining the "W" position.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QPushButton

from core.actions import MOD_MASK, Binding
from ui.physical_keys import CODE_NAMES, native_code, supports_physical

# Modifiers pressed on their own are not a shortcut — capture must wait for a
# real key rather than binding "Shift".
_BARE_MODIFIERS = {
    Qt.Key.Key_Shift,
    Qt.Key.Key_Control,
    Qt.Key.Key_Alt,
    Qt.Key.Key_Meta,
    Qt.Key.Key_AltGr,
    Qt.Key.Key_CapsLock,
}

_MOD_NAMES = (
    (Qt.KeyboardModifier.MetaModifier, "Cmd"),
    (Qt.KeyboardModifier.ControlModifier, "Ctrl"),
    (Qt.KeyboardModifier.AltModifier, "Alt"),
    (Qt.KeyboardModifier.ShiftModifier, "Shift"),
)


def describe(event) -> str:
    """Human label for a captured key event."""
    parts = [name for mod, name in _MOD_NAMES if event.modifiers() & mod]
    code = native_code(event)
    name = CODE_NAMES.get(code) if code else None
    if not name:
        seq = QKeySequence(event.key()).toString()
        name = seq or f"key {event.key()}"
    parts.append(name)
    return " + ".join(parts)


def binding_from_event(event) -> Binding | None:
    """Build a :class:`Binding` from a key event, or None for a bare modifier.

    Prefers a position-based binding so the shortcut survives a keyboard-layout
    change; falls back to the logical key where the platform exposes no native
    code (Linux).
    """
    if Qt.Key(event.key()) in _BARE_MODIFIERS:
        return None
    mods = int(event.modifiers().value) & MOD_MASK
    label = describe(event)
    code = native_code(event)
    if code and supports_physical():
        return Binding("phys", code, mods, label)
    return Binding("key", int(event.key()), mods, label)


def capturing_shortcut(widget) -> bool:
    """True when *widget* is a capture button waiting for a key.

    MainWindow's application-wide event filter sees a KeyPress before the
    focused widget does, and it either runs the action the key is bound to or
    forwards the key to Traktor — so an armed capture button would never see
    the key it is supposed to record. The filter asks this of the focus widget
    rather than reading a global "a capture is armed" flag: that flag would
    outlive a button destroyed mid-capture and leave every shortcut dead.

    A hidden button never counts, for the same reason: an arm left behind by a
    closed Settings panel would otherwise silently swallow every key in the
    app, shortcuts and forward-to-Traktor alike.
    """
    return isinstance(widget, ShortcutButton) and widget.is_capturing() and widget.isVisible()


class ShortcutButton(QPushButton):
    """Click to arm, then press a key combination to bind it.

    Escape cancels, Backspace/Delete clears the binding.
    """

    captured = Signal(object)  # Binding | None  (None = cleared)

    def __init__(self, binding: Binding | None, parent=None):
        super().__init__(parent)
        self._binding = binding
        self._capturing = False
        self.setCheckable(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.clicked.connect(self._toggle_capture)
        self._refresh()

    def is_capturing(self) -> bool:
        """True while armed and waiting for a key."""
        return self._capturing

    def set_binding(self, binding: Binding | None) -> None:
        self._binding = binding
        self._refresh()

    def _refresh(self) -> None:
        if self._capturing:
            self.setText("Press a key…   (Esc cancels)")
        else:
            self.setText(self._binding.display() if self._binding else "—")

    def _toggle_capture(self) -> None:
        self._capturing = self.isChecked()
        self._refresh()
        if self._capturing:
            self.grabKeyboard()
        else:
            self._stop()

    def _stop(self) -> None:
        self._capturing = False
        self.setChecked(False)
        self.releaseKeyboard()
        self._refresh()

    def focusOutEvent(self, event) -> None:
        if self._capturing:
            self._stop()
        super().focusOutEvent(event)

    def hideEvent(self, event) -> None:
        # Closing the Settings panel mid-capture is a cancel; without this the
        # arm would outlive the button and keep the global bindings disarmed.
        if self._capturing:
            self._stop()
        super().hideEvent(event)

    def keyPressEvent(self, event) -> None:
        if not self._capturing:
            super().keyPressEvent(event)
            return

        key = Qt.Key(event.key())
        if key == Qt.Key.Key_Escape:
            self._stop()
            return
        if key in (Qt.Key.Key_Backspace, Qt.Key.Key_Delete):
            self._binding = None
            self._stop()
            self.captured.emit(None)
            return

        binding = binding_from_event(event)
        if binding is None:
            return  # bare modifier — keep waiting for a real key
        self._binding = binding
        self._stop()
        self.captured.emit(binding)
