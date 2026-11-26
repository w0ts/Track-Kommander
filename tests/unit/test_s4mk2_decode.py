"""Tests for the S4 MK2 short-report decode.

The watcher's job is narrow: turn HID bytes into control ids and leave what a
control *means* to :mod:`core.actions`. These tests pin the byte/bit layout
(sourced from the Mixxx S4 MK2 mapping) and the edge semantics — a control
fires once per press, never while held, and never without Right Shift.

The IOKit run loop is not exercised here: `_short` takes a plain list of
payload bytes, so the decode is testable without the hardware.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6.QtCore")

from workers.s4mk2_watcher import S4MK2Watcher

_IDX_SHIFT = 0x0C
_BIT_SHIFT = 0x08
_IDX_CUE_A = 0x0F
_BIT_CUE_A = 0x40
_IDX_PUSH = 0x13
_BIT_PUSH = 0x04


def _payload(bits: dict[int, int], length: int = 0x20) -> list[int]:
    """A short-report payload with the given index → bit-mask set."""
    data = [0] * length
    for idx, mask in bits.items():
        data[idx] |= mask
    return data


def _shift(extra: dict[int, int] | None = None, **kw) -> list[int]:
    bits = {_IDX_SHIFT: _BIT_SHIFT}
    bits.update(extra or {})
    return _payload(bits, **kw)


@pytest.fixture
def watcher():
    w = S4MK2Watcher()
    seen: list[str] = []
    w.control.connect(seen.append)
    w._seen = seen  # test-local handle, not part of the API
    return w


def test_browse_push_is_a_known_control() -> None:
    assert "browse_push" in dict(S4MK2Watcher.CONTROLS)


def test_browse_push_fires_once_per_press(watcher) -> None:
    watcher._short(_shift())  # shift down, no press
    watcher._short(_shift({_IDX_PUSH: _BIT_PUSH}))  # press
    watcher._short(_shift({_IDX_PUSH: _BIT_PUSH}))  # still held
    assert watcher._seen == ["browse_push"]


def test_browse_push_fires_again_after_release(watcher) -> None:
    watcher._short(_shift({_IDX_PUSH: _BIT_PUSH}))
    watcher._short(_shift())  # released
    watcher._short(_shift({_IDX_PUSH: _BIT_PUSH}))
    assert watcher._seen == ["browse_push", "browse_push"]


def test_browse_push_without_shift_is_ignored(watcher) -> None:
    """Unshifted, the encoder push is Traktor's — Kommander must not claim it."""
    watcher._short(_payload({_IDX_PUSH: _BIT_PUSH}))
    assert watcher._seen == []


def test_browse_push_held_across_a_shift_release_refires(watcher) -> None:
    """Letting go of Shift clears the latch, so the next press is a fresh edge
    rather than a press the watcher believes it already reported."""
    watcher._short(_shift({_IDX_PUSH: _BIT_PUSH}))
    watcher._short(_payload({_IDX_PUSH: _BIT_PUSH}))  # shift up, still held
    watcher._short(_shift({_IDX_PUSH: _BIT_PUSH}))  # shift back down
    assert watcher._seen == ["browse_push", "browse_push"]


def test_a_report_too_short_for_the_push_byte_still_decodes_the_cues(watcher) -> None:
    """The push byte sits past the CUE bytes; a truncated report must not cost
    us the controls that did arrive."""
    watcher._short(_shift({_IDX_CUE_A: _BIT_CUE_A}, length=0x10))
    assert watcher._seen == ["cue1"]


def test_learn_mode_names_the_push_instead_of_firing_it(watcher) -> None:
    learned: list[str] = []
    watcher.learned.connect(learned.append)
    watcher.set_learning(True)
    watcher._short(_shift({_IDX_PUSH: _BIT_PUSH}))
    assert learned == ["browse_push"]
    assert watcher._seen == []
