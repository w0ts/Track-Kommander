"""Tests for permanently-dismissible confirmation dialogs.

``confirm_dismissible`` backs warnings the user is allowed to silence for good
— currently the "Traktor is running" warning shown before a library scan, where
the on-disk collection.nml may be older than what Traktor holds in memory.

The contract that matters: ticking the box only counts when the user actually
proceeds. Remembering a dismissal after a *cancel* would turn the checkbox into
a silent permanent "no", which is not what it says.

QSettings is redirected into a temp directory so the tests never touch the real
user preferences.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6.QtWidgets")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QSettings
from PySide6.QtWidgets import QApplication, QMessageBox

import ui.dialogs as dialogs

KEY = "scan_traktor_running"


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def settings(qapp, tmp_path, monkeypatch):
    """Isolated QSettings rooted in tmp_path."""
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path))
    monkeypatch.setattr(
        QSettings, "defaultFormat", lambda: QSettings.Format.IniFormat, raising=False
    )
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    old_org = QCoreApplication.organizationName()
    old_app = QCoreApplication.applicationName()
    QCoreApplication.setOrganizationName("TrackKommanderTests")
    QCoreApplication.setApplicationName("dismissible")
    s = QSettings()
    s.clear()
    s.sync()
    yield s
    s.clear()
    s.sync()
    QCoreApplication.setOrganizationName(old_org)
    QCoreApplication.setApplicationName(old_app)


def _answer(monkeypatch, *, button, tick: bool):
    """Patch QMessageBox.exec to answer *button*, optionally ticking the box."""
    shown: list[QMessageBox] = []

    def _exec(box) -> int:
        shown.append(box)
        if tick and box.checkBox() is not None:
            box.checkBox().setChecked(True)
        return int(button)

    monkeypatch.setattr(QMessageBox, "exec", _exec)
    return shown


def _ask(parent=None) -> bool:
    return dialogs.confirm_dismissible(
        parent,
        "Traktor is running",
        "collection.nml may be stale.",
        key=KEY,
    )


# ── Basic answers ───────────────────────────────────────────────────────────


def test_proceeding_returns_true(settings, monkeypatch) -> None:
    shown = _answer(monkeypatch, button=QMessageBox.StandardButton.Yes, tick=False)
    assert _ask() is True
    assert len(shown) == 1


def test_cancelling_returns_false(settings, monkeypatch) -> None:
    _answer(monkeypatch, button=QMessageBox.StandardButton.Cancel, tick=False)
    assert _ask() is False


def test_warning_reappears_when_not_dismissed(settings, monkeypatch) -> None:
    shown = _answer(monkeypatch, button=QMessageBox.StandardButton.Yes, tick=False)
    _ask()
    _ask()
    assert len(shown) == 2, "without the checkbox the warning must come back"


# ── Dismissal ───────────────────────────────────────────────────────────────


def test_dismissal_is_remembered_and_skips_the_dialog(settings, monkeypatch) -> None:
    shown = _answer(monkeypatch, button=QMessageBox.StandardButton.Yes, tick=True)
    assert _ask() is True
    assert dialogs.is_warning_dismissed(KEY)

    assert _ask() is True
    assert len(shown) == 1, "a dismissed warning must not be shown again"


def test_dismissal_is_not_remembered_when_cancelling(settings, monkeypatch) -> None:
    shown = _answer(monkeypatch, button=QMessageBox.StandardButton.Cancel, tick=True)
    assert _ask() is False
    assert not dialogs.is_warning_dismissed(KEY), (
        "ticking the box then cancelling must not become a silent permanent no"
    )

    assert _ask() is False
    assert len(shown) == 2


def test_keys_are_independent(settings, monkeypatch) -> None:
    _answer(monkeypatch, button=QMessageBox.StandardButton.Yes, tick=True)
    _ask()
    assert dialogs.is_warning_dismissed(KEY)
    assert not dialogs.is_warning_dismissed("some_other_warning")


def test_reset_brings_the_warning_back(settings, monkeypatch) -> None:
    shown = _answer(monkeypatch, button=QMessageBox.StandardButton.Yes, tick=True)
    _ask()
    assert len(shown) == 1

    dialogs.reset_dismissed_warnings()
    assert not dialogs.is_warning_dismissed(KEY)

    _ask()
    assert len(shown) == 2
