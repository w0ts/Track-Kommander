"""Collection writes wait until Traktor is closed — no "continue anyway".

Traktor rewrites collection.nml when it quits, so a write made while it
ran was always lost.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6.QtWidgets")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from types import SimpleNamespace

from PySide6.QtWidgets import QApplication, QDialog, QPushButton

import core.traktor
import ui.traktor_guard as guard
from ui.library import LibraryTab


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


def test_nothing_to_wait_for_when_traktor_is_closed(qapp, monkeypatch) -> None:
    monkeypatch.setattr(core.traktor, "is_traktor_running", lambda: False)
    assert guard.wait_for_traktor_closed(None, "Writing hotcues") == (True, False)


def test_the_dialog_carries_on_once_traktor_has_closed(qapp) -> None:
    running = [True]
    dlg = guard.TraktorClosedDialog(
        "Writing hotcues", running=lambda: running[0], ask_to_quit=lambda: True
    )
    dlg._check()
    assert dlg.result() != QDialog.DialogCode.Accepted
    running[0] = False  # the user quit Traktor themselves
    dlg._check()
    assert dlg.result() == QDialog.DialogCode.Accepted
    assert not dlg.quit_by_us, "not ours to start again"


def test_quit_for_me_asks_politely_and_marks_a_relaunch(qapp) -> None:
    asked: list[bool] = []
    dlg = guard.TraktorClosedDialog(
        "Writing playlists", running=lambda: True, ask_to_quit=lambda: asked.append(True) or True
    )
    dlg._quit_for_me()
    assert asked == [True]
    assert dlg.quit_by_us
    assert not dlg.quit_btn.isEnabled()


def test_a_refused_quit_leaves_the_button_usable(qapp) -> None:
    dlg = guard.TraktorClosedDialog(
        "Writing playlists", running=lambda: True, ask_to_quit=lambda: False
    )
    dlg._quit_for_me()
    assert not dlg.quit_by_us and dlg.quit_btn.isEnabled()


def test_there_is_no_continue_anyway(qapp) -> None:
    dlg = guard.TraktorClosedDialog(
        "Writing hotcues", running=lambda: True, ask_to_quit=lambda: True
    )
    labels = {b.text() for b in dlg.findChildren(QPushButton)}
    assert labels == {"Quit Traktor for me", "Cancel"}


# ── Starting Traktor again ──────────────────────────────────────────────────


def test_traktor_quit_for_a_write_is_started_again_once(qapp, monkeypatch) -> None:
    launched: list[bool] = []
    monkeypatch.setattr(core.traktor, "launch_traktor", lambda: launched.append(True) or True)
    tab = SimpleNamespace(_relaunch_traktor=True)
    LibraryTab._collection_written(tab)
    LibraryTab._collection_written(tab)
    assert launched == [True]


def test_traktor_closed_by_the_user_is_not_started(qapp, monkeypatch) -> None:
    launched: list[bool] = []
    monkeypatch.setattr(core.traktor, "launch_traktor", lambda: launched.append(True) or True)
    LibraryTab._collection_written(SimpleNamespace())
    assert launched == []
