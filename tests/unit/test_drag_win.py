"""The Windows drag: what it sends, and that it never leaves the button held.

``core.drag_win`` binds user32 and kernel32 when it is imported and only makes
sense on Windows. Here it is loaded against a fake of both, so the calls it
makes can be read back on any machine: the module is executed from its file
under a private name, with ``ctypes.WinDLL`` replaced, and never enters
``sys.modules`` — the fakes stay with the test that made them.

The fake is stricter than the real DLL in the way ``test_keyforward_win``'s
is: a function cannot be called before its signature is declared, nor with a
number that does not fit the type declared for it.

Nothing here can say whether Traktor accepts the drop. What it pins is the
sequence sent — press, nudge, glide, settle, release, put the cursor back — and
the promise made in ``do_drag``: whatever fails in between, the button this
module pressed is released.
"""

from __future__ import annotations

import ctypes
import importlib.util
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import core

_SOURCE = Path(core.__file__).with_name("drag_win.py")

_SIGNED, _UNSIGNED = "bhilq", "BHILQP"

#: A virtual desktop 65536 wide and tall, so that the module's normalisation
#: to 0..65535 is the identity and coordinates read back exactly.
_DESKTOP = {76: 0, 77: 0, 78: 65536, 79: 65536}
_SM_REMOTESESSION = 0x1000


def _fits(kind, value) -> bool:
    code = getattr(kind, "_type_", "")
    if not isinstance(value, int) or not isinstance(code, str) or not code:
        return True
    bits = ctypes.sizeof(kind) * 8
    if code in _SIGNED:
        return -(1 << (bits - 1)) <= value < (1 << (bits - 1))
    if code in _UNSIGNED:
        return 0 <= value < (1 << bits)
    return True


class _Fn:
    """A DLL function as ctypes hands one out, and as strict as
    ``test_keyforward_win._Fn``."""

    def __init__(self, name: str, call) -> None:
        self._name = name
        self._call = call
        self.argtypes = None
        self.restype = None

    def __call__(self, *args):
        if self.argtypes is None or self.restype is None:
            raise AssertionError(f"{self._name} called with no signature")
        if len(args) != len(self.argtypes):
            raise ctypes.ArgumentError(f"{self._name} takes {len(self.argtypes)} arguments")
        for position, (kind, value) in enumerate(zip(self.argtypes, args, strict=True), 1):
            if not _fits(kind, value):
                raise ctypes.ArgumentError(
                    f"{self._name}, argument {position}: {value:#x} does not fit a {kind.__name__}"
                )
        return self._call(*args)


class _FakeUser32:
    """Records every mouse event sent; the cursor sits at :attr:`cursor`.

    Any function the module declares a signature for but the fake has no
    behaviour for fails loudly when called, instead of returning something
    plausible.
    """

    def __init__(self) -> None:
        self.cursor = (500, 400)
        self.remote = False
        #: ``(kind, x, y, extra)`` for every SendInput call, in order.
        self.events: list[tuple[str, int, int, int]] = []
        #: called with the number of events sent so far, before each one is
        #: recorded — a test raises from it to fail the drag at that point.
        self.on_send = lambda _sent: None
        self.hooks_set: list[int] = []
        self.hooks_removed: list[int] = []
        self.hook_result = 0x1_0000_0001
        self._fns: dict[str, _Fn] = {}

    def __getattr__(self, name: str) -> _Fn:
        if name.startswith("_"):
            raise AttributeError(name)
        behaviour = getattr(type(self), f"_do_{name}", None)
        if behaviour is None:

            def behaviour(_self, *_args, _name=name):
                raise AssertionError(f"user32.{_name} was not expected")

        fn = _Fn(name, lambda *a: behaviour(self, *a))
        self._fns[name] = fn
        object.__setattr__(self, name, fn)
        return fn

    # Only what the drag touches.
    def _do_GetCursorPos(self, byref_pt) -> int:
        byref_pt._obj.x, byref_pt._obj.y = self.cursor
        return 1

    def _do_GetSystemMetrics(self, index) -> int:
        if index == _SM_REMOTESESSION:
            return int(self.remote)
        return _DESKTOP[index]

    def _do_SendInput(self, count, byref_input, size) -> int:
        assert count == 1
        inp = byref_input._obj
        assert size == ctypes.sizeof(type(inp))
        assert inp.type == 0, "the drag sends mouse input and nothing else"
        mi = inp.u.mi
        self.on_send(len(self.events))
        flags = mi.dwFlags
        if flags & 0x0002:
            kind = "down"
        elif flags & 0x0004:
            kind = "up"
        else:
            assert flags & 0x0001, f"a mouse event with flags {flags:#x}"
            kind = "move"
        self.events.append((kind, mi.dx, mi.dy, mi.dwExtraInfo))
        if kind == "move":
            self.cursor = (mi.dx, mi.dy)
        return 1

    def _do_WindowFromPoint(self, _pt) -> None:
        return None  # no window there: the drop log names no program

    def _do_SetWindowsHookExW(self, hook_id, _proc, _module, _thread) -> int:
        self.hooks_set.append(hook_id)
        return self.hook_result

    def _do_UnhookWindowsHookEx(self, handle) -> int:
        self.hooks_removed.append(handle)
        return 1


@pytest.fixture
def dw(monkeypatch):
    """``core.drag_win`` loaded against the fake user32, with sleeping
    switched off. ``dw.fake`` is the user32."""
    fake = _FakeUser32()
    libraries = {"user32": fake, "kernel32": _KernelStub()}

    def load(name, **_kw):
        return libraries[name]

    monkeypatch.setattr("ctypes.WinDLL", load, raising=False)
    monkeypatch.setattr("ctypes.get_last_error", lambda: 5, raising=False)
    if not hasattr(ctypes, "WINFUNCTYPE"):
        # Off Windows there is one calling convention; the callback types
        # are built the same way.
        monkeypatch.setattr("ctypes.WINFUNCTYPE", ctypes.CFUNCTYPE, raising=False)
    spec = importlib.util.spec_from_file_location("drag_win_under_test", _SOURCE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    monkeypatch.setattr(
        module, "time", SimpleNamespace(sleep=lambda _s: None, monotonic=time.monotonic)
    )
    module.fake = fake
    return module


class _KernelStub:
    """kernel32: the module only declares signatures on it at import."""

    def __getattr__(self, name: str) -> _Fn:
        if name.startswith("_"):
            raise AttributeError(name)
        fn = _Fn(
            name,
            lambda *_a, _n=name: (_ for _ in ()).throw(
                AssertionError(f"kernel32.{_n} was not expected")
            ),
        )
        object.__setattr__(self, name, fn)
        return fn


SRC = (300, 200)
DST = (1200, 700)
ORIGIN = (500, 400)


def _fire_on_nudge(dw_mod):
    """Act as the GUI thread: report ``startDrag`` as the nudge goes out
    (the first move after the press)."""

    def on_send(_sent):
        kinds = [e[0] for e in dw_mod.fake.events]
        if kinds == ["move", "down"]:
            dw_mod.notify_drag_started()

    dw_mod.fake.on_send = on_send


def _kinds(dw_mod) -> list[str]:
    return [e[0] for e in dw_mod.fake.events]


# ── A drag that works ───────────────────────────────────────────────────────


def test_a_successful_drag_sends_the_expected_sequence(dw) -> None:
    _fire_on_nudge(dw)

    dw.do_drag(SRC, DST)

    # move to src, press, nudge, 10 glide steps, 2 settle ticks, release,
    # cursor back.
    assert _kinds(dw) == (["move", "down", "move"] + ["move"] * 10 + ["move"] * 2 + ["up", "move"])


def test_it_presses_at_the_source_and_releases_at_the_destination(dw) -> None:
    _fire_on_nudge(dw)

    dw.do_drag(SRC, DST)

    events = dw.fake.events
    assert events[0][:3] == ("move", *SRC)
    assert events[1][:3] == ("down", *SRC)
    up = next(e for e in events if e[0] == "up")
    assert up[1:3] == DST


def test_the_nudge_crosses_the_drag_threshold_towards_the_target(dw) -> None:
    _fire_on_nudge(dw)

    dw.do_drag(SRC, DST)

    _, x, y, _ = dw.fake.events[2]
    assert ((x - SRC[0]) ** 2 + (y - SRC[1]) ** 2) ** 0.5 == pytest.approx(24, abs=1.5)
    assert x > SRC[0] and y > SRC[1], "towards the target, not away from it"


def test_the_glide_is_continuous_and_ends_on_the_target(dw) -> None:
    _fire_on_nudge(dw)

    dw.do_drag(SRC, DST)

    glide = [e for e in dw.fake.events[3:13]]
    assert [e[0] for e in glide] == ["move"] * 10
    xs = [e[1] for e in glide]
    assert xs == sorted(xs) and len(set(xs)) == 10, "a step at a time"
    assert glide[-1][1:3] == DST


def test_the_settle_stays_on_the_target(dw) -> None:
    _fire_on_nudge(dw)

    dw.do_drag(SRC, DST)

    assert [e[1:3] for e in dw.fake.events[13:15]] == [DST, DST]


def test_the_cursor_goes_back_to_where_it_was(dw) -> None:
    _fire_on_nudge(dw)

    dw.do_drag(SRC, DST)

    assert dw.fake.events[-1][:3] == ("move", *ORIGIN)
    assert dw.fake.cursor == ORIGIN


def test_every_event_carries_this_modules_tag(dw) -> None:
    """The low-level hook lets an event through only because of this tag."""
    _fire_on_nudge(dw)

    dw.do_drag(SRC, DST)

    assert {e[3] for e in dw.fake.events} == {dw._INJECT_TAG}


def test_a_remote_session_is_paced_more_gently(dw) -> None:
    """Three times the settle ticks over RDP; the glide keeps its steps."""
    dw.fake.remote = True
    _fire_on_nudge(dw)

    dw.do_drag(SRC, DST)

    assert _kinds(dw) == (["move", "down", "move"] + ["move"] * 10 + ["move"] * 6 + ["up", "move"])


def test_the_button_is_released_exactly_once_on_success(dw) -> None:
    _fire_on_nudge(dw)

    dw.do_drag(SRC, DST)

    assert _kinds(dw).count("down") == 1
    assert _kinds(dw).count("up") == 1


# ── A drag that fails ───────────────────────────────────────────────────────


def _fail_at(dw_mod, index: int) -> None:
    """Raise from the *index*th SendInput (0-based), once; then behave."""
    inner = dw_mod.fake.on_send
    state = {"raised": False}

    def on_send(sent):
        inner(sent)
        if sent == index and not state["raised"]:
            state["raised"] = True
            raise OSError("SendInput failed")

    dw_mod.fake.on_send = on_send


@pytest.mark.parametrize("index", [2, 3, 8, 12, 13, 14])
def test_a_failure_after_the_press_releases_the_button(dw, index) -> None:
    """B11: a failed drag on Windows could leave the mouse button held.

    Event 2 is the nudge, 3..12 the glide, 13..14 the settle.
    """
    _fire_on_nudge(dw)
    _fail_at(dw, index)

    with pytest.raises(OSError, match="SendInput failed"):
        dw.do_drag(SRC, DST)

    kinds = _kinds(dw)
    assert kinds.count("down") == 1
    assert kinds.count("up") == 1, f"button left held, sent {kinds}"
    assert kinds.index("up") > kinds.index("down")
    assert kinds[-2:] == ["up", "move"], "released, then the cursor put back"
    assert dw.fake.cursor == ORIGIN


def test_a_failure_releases_where_the_cursor_is(dw) -> None:
    """The release goes to wherever the cursor has got to, not to a spot the
    drag never reached."""
    _fire_on_nudge(dw)
    _fail_at(dw, 8)

    with pytest.raises(OSError):
        dw.do_drag(SRC, DST)

    up = next(e for e in dw.fake.events if e[0] == "up")
    assert up[1:3] not in (SRC, DST)


def test_a_failure_before_the_press_sends_no_release(dw) -> None:
    """Nothing pressed, so nothing to let go of — a stray button-up would
    click on whatever is under the cursor."""
    _fail_at(dw, 0)

    with pytest.raises(OSError):
        dw.do_drag(SRC, DST)

    assert dw.fake.events == []


def test_a_failed_press_sends_no_release(dw) -> None:
    _fail_at(dw, 1)

    with pytest.raises(OSError):
        dw.do_drag(SRC, DST)

    assert _kinds(dw) == ["move"]


def test_a_failure_while_releasing_the_drop_still_ends_with_the_button_up(dw) -> None:
    """The normal release itself failing: the button is still marked down, so
    the clean-up sends it again."""
    _fire_on_nudge(dw)
    _fail_at(dw, 15)  # the release at the destination

    with pytest.raises(OSError):
        dw.do_drag(SRC, DST)

    kinds = _kinds(dw)
    assert kinds[-2:] == ["up", "move"]
    assert kinds.count("up") == 1


def test_a_failure_after_the_drop_does_not_release_twice(dw) -> None:
    _fire_on_nudge(dw)
    _fail_at(dw, 16)  # putting the cursor back, after the release

    with pytest.raises(OSError):
        dw.do_drag(SRC, DST)

    assert _kinds(dw).count("up") == 1


# ── startDrag never fires ───────────────────────────────────────────────────


def test_no_startdrag_aborts_and_releases_at_the_nudge(dw, monkeypatch) -> None:
    """The GUI thread never signals: gliding anyway would drop before the OLE
    loop is live. The press is let go and the cursor restored instead."""
    monkeypatch.setattr(dw, "_T_START_TIMEOUT", 0.01)

    dw.do_drag(SRC, DST)  # returns; nothing to raise

    assert _kinds(dw) == ["move", "down", "move", "up", "move"]
    assert dw.fake.events[3][1:3] == dw.fake.events[2][1:3], (
        "released where the nudge left the cursor"
    )
    assert dw.fake.events[-1][:3] == ("move", *ORIGIN)


def test_a_startdrag_signal_from_an_earlier_drag_is_not_believed(dw) -> None:
    """The event is cleared as a drag begins: a stale set() from the last one
    must not let this one glide before its own startDrag."""
    dw.notify_drag_started()
    dw._T_START_TIMEOUT = 0.01

    dw.do_drag(SRC, DST)

    assert _kinds(dw) == ["move", "down", "move", "up", "move"]


# ── The input-blocking hook ─────────────────────────────────────────────────


def test_blocking_installs_one_low_level_mouse_hook(dw) -> None:
    dw.block_physical_mouse(True)
    dw.block_physical_mouse(True)

    assert dw.fake.hooks_set == [14]  # WH_MOUSE_LL, once


def test_unblocking_removes_the_hook_it_installed(dw) -> None:
    dw.block_physical_mouse(True)
    dw.block_physical_mouse(False)
    dw.block_physical_mouse(False)

    assert dw.fake.hooks_removed == [dw.fake.hook_result]


def test_a_hook_that_cannot_be_installed_leaves_input_alone(dw) -> None:
    dw.fake.hook_result = 0

    dw.block_physical_mouse(True)
    dw.block_physical_mouse(False)

    assert dw.fake.hooks_removed == []


def test_a_synthetic_drag_is_flagged_once(dw) -> None:
    assert dw.consume_synthetic_drag() is False
    dw.begin_synthetic_drag()
    assert dw.consume_synthetic_drag() is True
    assert dw.consume_synthetic_drag() is False
