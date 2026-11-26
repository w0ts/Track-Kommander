"""Pop-ups drawn inside the main window.

As windows of their own they stayed hidden under the docked, always-on-top
main window; one was only seen once Traktor was quit.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6.QtWidgets")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QCheckBox, QWidget

import ui.dialogs as dialogs
from ui.inline_dialog import InlineDialog, host_for


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def host(qapp):
    w = QWidget()
    w.resize(600, 400)
    w.show()
    qapp.processEvents()
    yield w
    w.close()


def _press(dlg_finder, key: str) -> None:
    """Press *key* once the pop-up is up (run() is blocking)."""
    QTimer.singleShot(50, lambda: dlg_finder().buttons[key].click())


def _current(host):
    return next(c for c in host.findChildren(InlineDialog) if c.isVisible())


def test_it_is_drawn_inside_the_window_not_as_one(host) -> None:
    dlg = InlineDialog(host, "T", "x", buttons=[("ok", "OK")], default="ok")
    assert not dlg.isWindow() and dlg.parent() is host
    QTimer.singleShot(50, lambda: dlg.finish("ok"))
    assert dlg.run() == "ok"


def test_confirm_answers_yes_and_no_inside_the_window(host) -> None:
    _press(lambda: _current(host), "yes")
    assert dialogs.confirm(host, "Apply?", "Sure?") is True
    _press(lambda: _current(host), "no")
    assert dialogs.confirm(host, "Apply?", "Sure?") is False


def test_custom_button_labels_are_kept(host) -> None:
    seen: dict = {}

    def _look():
        dlg = _current(host)
        seen["labels"] = {k: b.text() for k, b in dlg.buttons.items()}
        dlg.buttons["no"].click()

    QTimer.singleShot(50, _look)
    dialogs.confirm(host, "Q", "t", yes_text="Write stick", no_text="Later")
    assert seen["labels"] == {"yes": "Write stick", "no": "Later"}


def test_info_and_warn_close_with_ok(host) -> None:
    _press(lambda: _current(host), "ok")
    dialogs.info(host, "Export complete", "KEY: 901 tracks")
    _press(lambda: _current(host), "ok")
    dialogs.warn(host, "Oops", "No collection")


def test_escape_is_the_negative_answer(host) -> None:
    dlg = InlineDialog(
        host, "T", "x", buttons=[("no", "No"), ("yes", "Yes")], default="yes", escape="no"
    )
    QTimer.singleShot(50, dlg._press_escape)
    assert dlg.run() == "no"


def test_enter_is_the_default_answer(host) -> None:
    dlg = InlineDialog(
        host, "T", "x", buttons=[("no", "No"), ("yes", "Yes")], default="yes", escape="no"
    )
    QTimer.singleShot(50, dlg._press_default)
    assert dlg.run() == "yes"


def test_the_dismissible_box_is_remembered_only_on_proceed(host, monkeypatch) -> None:
    stored: dict = {}
    monkeypatch.setattr(dialogs, "is_warning_dismissed", lambda key: False)

    class _S:
        def setValue(self, k, v):
            stored[k] = v

        def sync(self):
            pass

    monkeypatch.setattr(dialogs, "QSettings", _S)

    def _tick_and(key):
        dlg = _current(host)
        dlg.findChildren(QCheckBox)[0].setChecked(True)
        dlg.buttons[key].click()

    QTimer.singleShot(50, lambda: _tick_and("no"))
    assert dialogs.confirm_dismissible(host, "W", "t", key="k") is False
    assert stored == {}
    QTimer.singleShot(50, lambda: _tick_and("yes"))
    assert dialogs.confirm_dismissible(host, "W", "t", key="k") is True
    assert stored == {f"{dialogs.DISMISSED_GROUP}/k": True}


def test_no_visible_window_means_no_inline_host(qapp) -> None:
    hidden = QWidget()
    assert host_for(hidden) is None and host_for(None) is None
