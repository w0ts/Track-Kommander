"""React at once when the active app changes, not up to 300 ms later.

Clicking Traktor while an editor is in front used to leave the docked window
covered until the next periodic check. The check stays as the safety net;
the event only makes the same decision immediately.
"""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6.QtWidgets")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from ui.app_switch_watch import AppSwitchWatch
from ui.main_window import MainWindow


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


class _Timer:
    def __init__(self, active: bool) -> None:
        self.active = active

    def isActive(self) -> bool:
        return self.active


def _react(docked: bool, minimised: bool) -> list[str]:
    calls: list[str] = []
    win = SimpleNamespace(
        _docked=docked,
        _mini_visible_timer=_Timer(minimised),
        _update_on_top=lambda: calls.append("on_top"),
        _update_mini_visibility=lambda: calls.append("mini"),
    )
    MainWindow._react_to_app_switch(win)
    return calls


def test_docked_an_app_switch_redecides_on_top(qapp) -> None:
    assert _react(docked=True, minimised=False) == ["on_top"]


def test_minimised_an_app_switch_redecides_the_button(qapp) -> None:
    assert _react(docked=False, minimised=True) == ["mini"]


def test_undocked_and_shown_nothing_to_do(qapp) -> None:
    assert _react(docked=False, minimised=False) == []


def test_a_failing_reaction_is_contained(qapp) -> None:
    """It runs inside an OS callback; an exception must not escape."""

    def _boom():
        raise RuntimeError("x")

    AppSwitchWatch(_boom)._fire()


def test_start_and_stop_are_idempotent(qapp) -> None:
    watch = AppSwitchWatch(lambda: None)
    watch.start()
    watch.start()
    watch.stop()
    watch.stop()
    assert not watch.active
