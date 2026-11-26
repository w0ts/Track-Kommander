"""The drag handle's re-snap after Traktor's deck layout flips.

Real failure: the branch that leaves a moved window alone logged through
``log``, a name the module never defined (its logger is ``_log``), so the
layout flip raised ``NameError`` instead of doing nothing.
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication, QWidget

import core.drag as drag
from ui.now_playing import _DragHandle


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def handle(qapp):
    win = QWidget()
    win.setGeometry(100, 100, 600, 400)
    h = _DragHandle(win)
    yield h
    win.deleteLater()


def test_a_window_that_was_never_snapped_is_left_alone(handle, monkeypatch) -> None:
    called = []
    monkeypatch.setattr(drag, "snap_over_traktor_library", lambda win: called.append(win) or True)
    handle.resnap_if_snapped()
    assert called == []


def test_a_window_moved_since_the_snap_is_left_alone(handle, monkeypatch) -> None:
    called = []
    monkeypatch.setattr(drag, "snap_over_traktor_library", lambda win: called.append(win) or True)
    handle.mark_snapped()
    handle.window().setGeometry(300, 250, 600, 400)

    handle.resnap_if_snapped()

    assert called == [], "the user moved it; the layout flip must not pull it back"


def test_a_window_still_snapped_is_snapped_again(handle, monkeypatch) -> None:
    called = []
    monkeypatch.setattr(drag, "snap_over_traktor_library", lambda win: called.append(win) or True)
    handle.mark_snapped()

    handle.resnap_if_snapped()

    assert called == [handle.window()]
