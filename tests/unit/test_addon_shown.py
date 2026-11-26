"""An add-on may hide itself (User, 2026-11-26: show people the public version without another
install): its band buttons, tabs and rows follow core.extensions' one switch."""

from __future__ import annotations

import pytest

from core import extensions
from core.extensions import BandButton


@pytest.fixture(autouse=True)
def _clean():
    extensions.clear()
    yield
    extensions.clear()


def _button(title: str) -> BandButton:
    return BandButton(title, f"Library: {title}", lambda *_: None)


def test_shown_until_the_add_on_hides_itself() -> None:
    assert extensions.addon_shown()
    extensions.set_addon_shown(False)
    assert not extensions.addon_shown()


def test_listeners_hear_each_change_once() -> None:
    heard: list[bool] = []
    extensions.on_addon_shown(heard.append)
    extensions.set_addon_shown(False)
    extensions.set_addon_shown(False)  # no change: nothing to tell
    extensions.set_addon_shown(True)
    assert heard == [False, True]


def test_a_failing_listener_does_not_stop_the_others() -> None:
    heard: list[bool] = []

    def broken(_shown: bool) -> None:
        raise RuntimeError("boom")

    extensions.on_addon_shown(broken)
    extensions.on_addon_shown(heard.append)
    extensions.set_addon_shown(False)
    assert heard == [False]


def test_settings_rows_are_kept_in_order_and_cleared() -> None:
    first, second = (lambda _tab: "a"), (lambda _tab: "b")
    extensions.add_settings_row(first)
    extensions.add_settings_row(second)
    assert extensions.settings_rows() == (first, second)
    extensions.set_addon_shown(False)
    extensions.clear()
    assert extensions.settings_rows() == ()
    assert extensions.addon_shown()


def test_a_hidden_add_on_leaves_every_band_slot_hidden() -> None:
    from ui.band_extensions import slot_visibility

    extensions.add_band_button(_button("One"))
    extensions.add_band_button(_button("Two"))
    assert slot_visibility() == {"X1": False, "X2": False, "X3": True, "X4": True}
    extensions.set_addon_shown(False)
    assert slot_visibility() == {"X1": True, "X2": True, "X3": True, "X4": True}
