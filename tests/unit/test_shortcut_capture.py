"""Capturing a shortcut must win over the global key handling.

MainWindow installs an application-wide event filter so Kommander's own
bindings fire and every other key is posted on to Traktor. That filter sees a
KeyPress before the focused widget does — so while the user is *recording* a
shortcut, the key they press is either eaten by the action it currently
triggers or forwarded to Traktor, and the capture button never sees it.

The inverse matters just as much: an arm that outlives its panel would swallow
every key in the app, so capture is read off a live, visible widget rather
than a global flag.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6.QtWidgets")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QSettings, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from ui.main_window import owns_key_input
from ui.shortcut_edit import ShortcutButton, capturing_shortcut


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def store(qapp, tmp_path):
    """QSettings in a temp dir — never the user's real bindings."""
    from core import actions

    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path))
    actions.reset_all()
    yield
    actions.reset_all()


@pytest.fixture
def panel(qapp, store):
    """A shown Settings-like panel holding one capture button.

    Shown, because a capture only counts while its button is on screen.
    """
    dlg = QDialog()
    btn = ShortcutButton(None)
    QVBoxLayout(dlg).addWidget(btn)
    dlg.show()
    qapp.processEvents()
    yield dlg, btn
    dlg.close()
    dlg.deleteLater()


def _event(key: Qt.Key, mods=Qt.KeyboardModifier.NoModifier, native: int = 0):
    return QKeyEvent(
        QEvent.Type.KeyPress,
        int(key),
        mods,
        native,
        native,
        0,
    )


def test_capture_is_not_active_by_default(panel) -> None:
    _, btn = panel
    assert capturing_shortcut(btn) is False


def test_arming_a_button_makes_capture_active(panel) -> None:
    _, btn = panel
    btn.click()
    assert capturing_shortcut(btn) is True


def test_capture_clears_once_the_key_lands(panel) -> None:
    _, btn = panel
    seen = []
    btn.captured.connect(seen.append)
    btn.click()
    btn.keyPressEvent(_event(Qt.Key.Key_Z, native=13))

    assert len(seen) == 1
    assert capturing_shortcut(btn) is False


def test_capture_clears_when_cancelled(panel) -> None:
    _, btn = panel
    btn.click()
    btn.keyPressEvent(_event(Qt.Key.Key_Escape))
    assert capturing_shortcut(btn) is False


def test_global_bindings_stand_aside_while_capturing(panel) -> None:
    """The bug: the app filter claimed the key before the button saw it."""
    _, btn = panel
    assert owns_key_input(btn) is False

    btn.click()
    assert owns_key_input(btn) is True

    btn.keyPressEvent(_event(Qt.Key.Key_Z, native=13))
    assert owns_key_input(btn) is False


def test_the_keyboard_grab_alone_disarms_the_global_bindings(panel) -> None:
    """Capture must survive a platform that doesn't focus a clicked button —
    the keyboard grab is what actually routes the key."""
    _, btn = panel
    btn.click()
    assert owns_key_input(None) is True

    btn.keyPressEvent(_event(Qt.Key.Key_Escape))
    assert owns_key_input(None) is False


def test_an_arm_left_behind_by_a_closed_panel_swallows_nothing(panel, qapp) -> None:
    """A hidden armed button must not disable the whole app's key handling —
    that would take the forward-to-Traktor path down with it."""
    dlg, btn = panel
    btn.click()
    assert owns_key_input(btn) is True

    dlg.close()
    qapp.processEvents()
    assert owns_key_input(btn) is False
    assert owns_key_input(None) is False


def test_text_fields_still_keep_their_keystrokes(qapp, store) -> None:
    field = QLineEdit()
    assert owns_key_input(field) is True
    field.deleteLater()


def test_ordinary_widgets_leave_keys_to_kommander_and_traktor(qapp, store) -> None:
    """Anything else must keep falling through to the bindings and to the
    forward-to-Traktor path — that is what makes the DJ's own Traktor mapping
    work while Kommander holds focus."""
    btn = QPushButton()
    assert owns_key_input(btn) is False
    assert owns_key_input(None) is False
    btn.deleteLater()


def test_a_dialog_owns_the_keyboard(qapp) -> None:
    """Enter in a yes/no box ran the "load to deck" binding instead: the app
    filter saw it before the dialog's default button did."""
    from PySide6.QtWidgets import QMessageBox

    box = QMessageBox()
    box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
    yes = box.button(QMessageBox.StandardButton.Yes)
    assert owns_key_input(yes) is True
    assert owns_key_input(box) is True


def test_a_panel_with_keys_of_its_own_keeps_them(qapp):
    from PySide6.QtWidgets import QListWidget, QVBoxLayout, QWidget

    inside, panel = QListWidget(), QWidget()
    panel.OWNS_KEYS = True  # a panel that uses Space/Enter itself
    QVBoxLayout(panel).addWidget(inside)
    assert owns_key_input(inside) is True
    assert owns_key_input(QListWidget()) is False  # elsewhere: Traktor's
