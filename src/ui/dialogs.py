"""Shared dialog helpers.

Centralises the keyboard-affordance contract for popups:
- Enter / Return triggers the affirmative action (Yes / OK / Save / Proceed).
- Escape triggers the negative action (No / Cancel / Abort).
"""

from __future__ import annotations

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QMessageBox,
    QWidget,
)

#: Button labels for the standard buttons the helpers are called with.
_LABELS = {
    QMessageBox.StandardButton.Yes: "Yes",
    QMessageBox.StandardButton.No: "No",
    QMessageBox.StandardButton.Ok: "OK",
    QMessageBox.StandardButton.Cancel: "Cancel",
    QMessageBox.StandardButton.Save: "Save",
    QMessageBox.StandardButton.Discard: "Discard",
    QMessageBox.StandardButton.Abort: "Abort",
    QMessageBox.StandardButton.Retry: "Retry",
}


def _ask_inline(
    parent, title, text, affirmative, negative, yes_text, no_text, checkbox: QCheckBox | None = None
) -> bool | None:
    """Ask inside the main window; None if there is no visible window to use.

    Pop-ups used to be windows of their own and stayed hidden under the
    docked, always-on-top main window (see ui.inline_dialog).
    """
    from ui.inline_dialog import InlineDialog, host_for

    host = host_for(parent)
    if host is None:
        return None
    buttons = [
        ("no", no_text or _LABELS.get(negative, "Cancel")),
        ("yes", yes_text or _LABELS.get(affirmative, "OK")),
    ]
    dlg = InlineDialog(host, title, text, buttons=buttons, default="yes", escape="no")
    if checkbox is not None:
        dlg.add_widget(checkbox)
    return dlg.run() == "yes"


def _tell_inline(parent, title, text) -> bool:
    """Show a message with OK inside the main window. False if not possible."""
    from ui.inline_dialog import InlineDialog, host_for

    host = host_for(parent)
    if host is None:
        return False
    InlineDialog(host, title, text, buttons=[("ok", "OK")], default="ok", escape="ok").run()
    return True


# QSettings group holding the "don't show this again" flags. Grouped under one
# prefix so a single "show all warnings again" action can clear the lot.
DISMISSED_GROUP = "dismissed_warnings"


def confirm(
    parent: QWidget | None,
    title: str,
    text: str,
    *,
    yes_text: str = "",
    no_text: str = "",
    icon: QMessageBox.Icon = QMessageBox.Icon.Question,
    affirmative: QMessageBox.StandardButton = QMessageBox.StandardButton.Yes,
    negative: QMessageBox.StandardButton = QMessageBox.StandardButton.No,
) -> bool:
    """Show a modal yes/no question. Enter = affirmative, Escape = negative.

    Returns True if the user confirmed, False otherwise.
    """
    inline = _ask_inline(parent, title, text, affirmative, negative, yes_text, no_text)
    if inline is not None:
        return inline
    box = QMessageBox(parent)
    box.setWindowTitle(title)
    box.setText(text)
    box.setIcon(icon)
    box.setStandardButtons(affirmative | negative)
    box.setDefaultButton(affirmative)  # Enter activates this
    box.setEscapeButton(negative)  # Escape activates this

    if yes_text:
        box.button(affirmative).setText(yes_text)
    if no_text:
        box.button(negative).setText(no_text)

    return box.exec() == int(affirmative)


def is_warning_dismissed(key: str) -> bool:
    """True if the user ticked "don't show this again" for *key*."""
    return bool(QSettings().value(f"{DISMISSED_GROUP}/{key}", False, type=bool))


def reset_dismissed_warnings() -> None:
    """Bring every permanently-dismissed warning back."""
    s = QSettings()
    s.beginGroup(DISMISSED_GROUP)
    s.remove("")  # clears the whole group
    s.endGroup()
    s.sync()


def confirm_dismissible(
    parent: QWidget | None,
    title: str,
    text: str,
    *,
    key: str,
    yes_text: str = "",
    no_text: str = "",
    icon: QMessageBox.Icon = QMessageBox.Icon.Warning,
    affirmative: QMessageBox.StandardButton = QMessageBox.StandardButton.Yes,
    negative: QMessageBox.StandardButton = QMessageBox.StandardButton.Cancel,
) -> bool:
    """A ``confirm`` that the user can silence permanently.

    Returns True to proceed. Once dismissed via the checkbox, later calls with
    the same *key* return True immediately without showing anything.

    The dismissal is only remembered when the user actually proceeds — ticking
    the box and then cancelling would otherwise turn into a silent, permanent
    "no", which is not what the checkbox says.

    ``reset_dismissed_warnings()`` undoes every dismissal.
    """
    if is_warning_dismissed(key):
        return True

    never = QCheckBox("Don't show this again")
    inline = _ask_inline(
        parent, title, text, affirmative, negative, yes_text, no_text, checkbox=never
    )
    if inline is not None:
        if inline and never.isChecked():
            s = QSettings()
            s.setValue(f"{DISMISSED_GROUP}/{key}", True)
            s.sync()
        return inline

    box = QMessageBox(parent)
    box.setWindowTitle(title)
    box.setText(text)
    box.setIcon(icon)
    box.setStandardButtons(affirmative | negative)
    box.setDefaultButton(affirmative)
    box.setEscapeButton(negative)
    if yes_text:
        box.button(affirmative).setText(yes_text)
    if no_text:
        box.button(negative).setText(no_text)

    never = QCheckBox("Don't show this again")
    box.setCheckBox(never)

    proceed = box.exec() == int(affirmative)
    if proceed and never.isChecked():
        s = QSettings()
        s.setValue(f"{DISMISSED_GROUP}/{key}", True)
        s.sync()
    return proceed


def info(parent: QWidget | None, title: str, text: str) -> None:
    """Information popup. Both Enter and Escape close it via the OK button."""
    if _tell_inline(parent, title, text):
        return
    box = QMessageBox(parent)
    box.setWindowTitle(title)
    box.setText(text)
    box.setIcon(QMessageBox.Icon.Information)
    box.setStandardButtons(QMessageBox.StandardButton.Ok)
    box.setDefaultButton(QMessageBox.StandardButton.Ok)
    box.setEscapeButton(QMessageBox.StandardButton.Ok)
    box.exec()


def warn(parent: QWidget | None, title: str, text: str) -> None:
    """Warning popup. Both Enter and Escape close it via the OK button."""
    if _tell_inline(parent, title, text):
        return
    box = QMessageBox(parent)
    box.setWindowTitle(title)
    box.setText(text)
    box.setIcon(QMessageBox.Icon.Warning)
    box.setStandardButtons(QMessageBox.StandardButton.Ok)
    box.setDefaultButton(QMessageBox.StandardButton.Ok)
    box.setEscapeButton(QMessageBox.StandardButton.Ok)
    box.exec()


def wire_dialog_keys(
    dialog: QDialog,
    button_box: QDialogButtonBox,
    *,
    affirmative: QDialogButtonBox.StandardButton = QDialogButtonBox.StandardButton.Save,
    negative: QDialogButtonBox.StandardButton = QDialogButtonBox.StandardButton.Cancel,
) -> None:
    """Make Enter trigger *affirmative* and Escape trigger *negative*.

    Qt already routes Escape to ``QDialog.reject()`` when a Cancel-role button
    exists, so we only need to make sure the affirmative button is the default
    and that pressing Enter inside non-button focus does the right thing.
    """
    aff_btn = button_box.button(affirmative)
    neg_btn = button_box.button(negative)
    if aff_btn is not None:
        aff_btn.setDefault(True)
        aff_btn.setAutoDefault(True)
    if neg_btn is not None:
        neg_btn.setAutoDefault(False)


def install_escape_to_close(dialog: QDialog) -> None:
    """Make Escape close the dialog even when it has no Cancel button.

    Used for content-only popups (the Library / Settings panel wrappers).
    """
    original = dialog.keyPressEvent

    def _key_press(ev: QKeyEvent) -> None:
        if ev.key() == Qt.Key.Key_Escape:
            dialog.close()
            return
        original(ev)

    dialog.keyPressEvent = _key_press  # type: ignore[assignment]
