"""Forwarding keystrokes to Traktor.

Measured against a live Traktor Pro 4 (posted to its pid while it sat in the
background on another display, window captured before/after):

* plain keycodes do arrive — "S" and "W" landed in its browser search field,
  but "A" did not, because ``kVK_ANSI_A`` is 0 and the guard treated it as
  "no key";
* its Controller Manager mapping ignores a key that goes down and up in the
  same instant — 0 ms and 2 ms holds moved a paused deck not at all, 5 ms and
  10 ms fired every press. Hence real down/up pairs rather than a synthesised
  press: the key is held for exactly as long as the user holds it.
"""

from __future__ import annotations

import sys
import types

import pytest

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent

from core import keyforward


class _FakeQuartz(types.ModuleType):
    """Records what would have been posted to the target process."""

    kCGEventSourceStateHIDSystemState = 1

    def __init__(self):
        super().__init__("Quartz")
        self.posted: list[tuple[int, int, bool]] = []
        self.flags: dict[int, int] = {}

    def CGEventSourceCreate(self, state):
        return object()

    def CGEventCreateKeyboardEvent(self, src, keycode, down):
        return [keycode, down, 0]

    def CGEventSetFlags(self, ev, flags):
        ev[2] = flags

    def CGEventPostToPid(self, pid, ev):
        self.posted.append((pid, ev[0], ev[1]))
        self.flags[ev[0]] = ev[2]


@pytest.fixture
def quartz(monkeypatch):
    fake = _FakeQuartz()
    monkeypatch.setitem(sys.modules, "Quartz", fake)
    monkeypatch.setattr(keyforward, "_IS_MAC", True)
    # Both, or on Windows the key still goes down the PostMessage path and
    # the fake is never asked.
    monkeypatch.setattr(keyforward, "_IS_WIN", False)
    keyforward.release_all(pid=1)  # start from a clean held-key state
    fake.posted.clear()
    yield fake
    keyforward.release_all(pid=1)


def test_a_press_posts_one_key_down_only(quartz) -> None:
    assert keyforward.post_key(13, down=True, pid=4242) is True
    assert quartz.posted == [(4242, 13, True)]


def test_the_release_is_posted_when_the_user_releases(quartz) -> None:
    keyforward.post_key(13, down=True, pid=4242)
    assert keyforward.post_key(13, down=False, pid=4242) is True
    assert quartz.posted == [(4242, 13, True), (4242, 13, False)]


def test_keycode_zero_is_a_real_key(quartz) -> None:
    """kVK_ANSI_A is 0 — dropping falsy codes silently ate the "A" key."""
    assert keyforward.post_key(0, down=True, pid=4242) is True
    assert quartz.posted == [(4242, 0, True)]


def test_a_forwarded_key_is_tracked_as_held(quartz) -> None:
    keyforward.post_key(13, down=True, pid=4242)
    assert keyforward.is_held(13) is True

    keyforward.post_key(13, down=False, pid=4242)
    assert keyforward.is_held(13) is False


def test_a_release_for_a_key_we_never_pressed_is_not_posted(quartz) -> None:
    """Otherwise a key Kommander consumed would still poke Traktor."""
    assert keyforward.post_key(13, down=False, pid=4242) is False
    assert quartz.posted == []


def test_release_all_frees_every_held_key(quartz) -> None:
    """Losing focus mid-press must not leave Traktor with the key down."""
    for code in (13, 1, 15):
        keyforward.post_key(code, down=True, pid=4242)
    quartz.posted.clear()

    keyforward.release_all(pid=4242)

    assert sorted(quartz.posted) == [(4242, 1, False), (4242, 13, False), (4242, 15, False)]
    assert keyforward.held_keys() == frozenset()


def test_release_all_is_a_no_op_when_nothing_is_held(quartz) -> None:
    keyforward.release_all(pid=4242)
    assert quartz.posted == []


def test_modifiers_are_translated_to_cg_flags(quartz) -> None:
    keyforward.post_key(13, int(Qt.KeyboardModifier.ShiftModifier.value), down=True, pid=4242)
    assert quartz.flags[13] == 0x00020000


def test_nothing_is_posted_when_traktor_is_not_running(quartz, monkeypatch) -> None:
    monkeypatch.setattr(keyforward, "traktor_pid", lambda: None)
    assert keyforward.post_key(13, down=True) is False
    assert quartz.posted == []
    assert keyforward.is_held(13) is False


def _event(key: Qt.Key, mods=Qt.KeyboardModifier.NoModifier):
    return QKeyEvent(QEvent.Type.KeyPress, int(key), mods)


def test_cmd_shortcuts_belong_to_the_menu_not_to_traktor() -> None:
    assert (
        keyforward.should_forward(_event(Qt.Key.Key_F, Qt.KeyboardModifier.MetaModifier)) is False
    )


def test_a_bare_modifier_is_never_forwarded() -> None:
    assert keyforward.should_forward(_event(Qt.Key.Key_Shift)) is False


def test_an_ordinary_key_is_forwarded() -> None:
    assert keyforward.should_forward(_event(Qt.Key.Key_A)) is True
