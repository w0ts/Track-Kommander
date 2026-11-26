"""The Windows (hidapi) backend for the S4 MK2.

Both backends hand the decoder the report **as the device sends it**, report ID
at index 0: that is what hidapi returns, what IOKit's callback buffer holds,
and what the Mixxx offsets are indices into. Stripping the ID on one platform
shifts every offset by one and the wrong buttons fire — which is exactly what
happened on Windows until a capture from the hardware showed Right Shift
landing on byte 0x0B and Browse push on byte 0x12, one short of 0x0C/0x13.
"""

from __future__ import annotations

import os
import sys
import threading
import time
import types

import pytest

pytest.importorskip("PySide6.QtWidgets")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

import workers.s4mk2_watcher as s4


# The report bytes a real S4 sends: report ID at index 0, Mixxx's offsets
# absolute from there.
def _short_report(
    shift: bool = True, cue1: bool = False, cue2: bool = False, push: bool = False
) -> list[int]:
    report = [0] * 0x14
    report[0] = s4._REPORT_SHORT
    if shift:
        report[s4._IDX_SHIFT] |= s4._BIT_SHIFT
    if cue1:
        report[s4._IDX_LEFT] |= s4._BIT_CUE_A
    if cue2:
        report[s4._IDX_RIGHT] |= s4._BIT_CUE_B
    if push:
        report[s4._IDX_PUSH] |= s4._BIT_PUSH
    return report


def _long_report(nibble: int) -> list[int]:
    report = [0] * (s4._IDX_BROWSE + 2)
    report[0] = s4._REPORT_LONG
    report[s4._IDX_BROWSE] = nibble & 0x0F
    return report


@pytest.fixture
def watcher(qtbot_free):
    w = s4.S4MK2Watcher()
    seen: list[str] = []
    w.control.connect(seen.append)
    return w, seen


@pytest.fixture(scope="module")
def qtbot_free():
    """A QApplication has to exist before any QObject."""
    return QApplication.instance() or QApplication([])


# ── The shared decode path ──────────────────────────────────────────────────


def test_dispatch_routes_a_short_report_to_the_button_decoder(watcher) -> None:
    w, seen = watcher
    w._dispatch(s4._REPORT_SHORT, _short_report(shift=True))
    w._dispatch(s4._REPORT_SHORT, _short_report(shift=True, cue1=True))
    assert seen == ["cue1"]


def test_dispatch_routes_a_long_report_to_the_encoder_decoder(watcher) -> None:
    w, seen = watcher
    w._dispatch(s4._REPORT_SHORT, _short_report(shift=True))
    w._dispatch(s4._REPORT_LONG, _long_report(3))
    w._dispatch(s4._REPORT_LONG, _long_report(4))
    assert seen == ["browse_cw"]


def test_an_unknown_report_id_is_ignored(watcher) -> None:
    w, seen = watcher
    w._dispatch(0x7F, _short_report(shift=True, cue1=True))
    assert seen == []


# ── The hidapi read loop ────────────────────────────────────────────────────


class _FakeHidDevice:
    """Enough of hidapi's device to drive one pass of the read loop.

    Out of reports, it either asks the watcher to stop, the way the user
    would, or — with *unplugged* — fails the read, the way hidapi does once
    the controller is gone.
    """

    def __init__(
        self,
        reports: list[list[int]],
        fail_open: Exception | None = None,
        watcher=None,
        unplugged: bool = False,
    ):
        self._reports = list(reports)
        self._fail_open = fail_open
        self._watcher = watcher
        self._unplugged = unplugged
        self.opened: tuple[int, int] | None = None
        self.closed = False
        self.nonblocking: bool | None = None

    def open(self, vid: int, pid: int) -> None:
        if self._fail_open:
            # The last one in a test's list: nothing will ever open, so the
            # test ends the way the application does, with stop().
            if self._watcher is not None:
                self._watcher.stop()
            raise self._fail_open
        self.opened = (vid, pid)

    def set_nonblocking(self, flag: bool) -> None:
        self.nonblocking = flag

    def read(self, size: int, timeout_ms: int = 0) -> list[int]:
        if self._reports:
            return self._reports.pop(0)
        if self._unplugged:
            raise OSError("read error")
        # Raising here would be caught by the backend's own error handling
        # and prove nothing about the loop ending cleanly.
        if self._watcher is not None:
            self._watcher.stop()
        return []

    def close(self) -> None:
        self.closed = True


def _install_fake_hid(monkeypatch, device=None, enumerate_result=(), devices=()):
    """A ``hid`` module handing out *device*, or each of *devices* in turn —
    one per attempt to open the controller."""
    queue = list(devices) or [device]
    module = types.ModuleType("hid")
    module.handed_out = []

    def next_device():
        dev = queue.pop(0) if len(queue) > 1 else queue[0]
        module.handed_out.append(dev)
        return dev

    module.device = next_device
    module.enumerate = lambda: list(enumerate_result)
    monkeypatch.setitem(sys.modules, "hid", module)
    return module


@pytest.fixture
def links(watcher, monkeypatch):
    """What the watcher says about its connection, in order. And no waiting
    between attempts: the interval is for hardware, not for tests."""
    w, _seen = watcher
    monkeypatch.setattr(s4, "_IS_WINDOWS", True)
    monkeypatch.setattr(s4, "_RECONNECT_S", 0.0)
    said: list[tuple[bool, str]] = []
    w.link.connect(lambda connected, message: said.append((connected, message)))
    return said


def test_the_report_reaches_the_decoder_unshifted(watcher, monkeypatch) -> None:
    """The whole reason this backend can go subtly wrong.

    The Mixxx offsets index the report *including* its ID byte, so hidapi's
    buffer is passed through whole. Removing the ID shifts every offset by
    one — measured on the hardware, that put Right Shift at 0x0B and Browse
    push at 0x12, and no control ever fired.
    """
    w, seen = watcher
    monkeypatch.setattr(s4, "_IS_WINDOWS", True)
    reports = [
        _short_report(shift=True),
        _short_report(shift=True, cue1=True),
    ]
    dev = _FakeHidDevice(reports, watcher=w)
    _install_fake_hid(monkeypatch, dev)

    w._run_hidapi()

    assert seen == ["cue1"], "the payload was misaligned"
    assert dev.opened == (s4._VID, s4._PID)
    assert dev.closed, "the device must be released"


def test_an_empty_read_is_not_a_report(watcher, monkeypatch) -> None:
    """A timed-out read returns []; decoding it would index out of range."""
    w, seen = watcher
    monkeypatch.setattr(s4, "_IS_WINDOWS", True)
    dev = _FakeHidDevice([[], [], _short_report(shift=True)], watcher=w)
    _install_fake_hid(monkeypatch, dev)

    w._run_hidapi()
    assert seen == []


def test_a_device_in_use_is_logged_not_raised(watcher, links, monkeypatch) -> None:
    """The open may well fail on Windows with Traktor holding the device.
    That must not take the app down with it."""
    w, _seen = watcher
    dev = _FakeHidDevice([], fail_open=OSError("device in use"), watcher=w)
    _install_fake_hid(monkeypatch, dev)

    w._run_hidapi()  # must return quietly
    assert dev.opened is None
    assert dev.closed, "even one that never opened is given back"


def test_a_missing_hidapi_is_not_fatal(watcher, monkeypatch) -> None:
    w, _seen = watcher
    monkeypatch.setattr(s4, "_IS_WINDOWS", True)
    monkeypatch.setitem(sys.modules, "hid", None)  # import hid -> ImportError
    w._run_hidapi()


def test_a_missing_hidapi_is_not_shown_as_connected(watcher, links, monkeypatch) -> None:
    w, _seen = watcher
    monkeypatch.setitem(sys.modules, "hid", None)

    w._run_hidapi()

    [(connected, message)] = links
    assert connected is False
    assert "hidapi" in message


# ── Coming back ─────────────────────────────────────────────────────────────
# The device was opened once. If that failed, or the controller was unplugged
# later, the thread ended: nothing tried again, and nothing told Settings,
# whose dot went on showing green for a backend that was gone.


def test_a_controller_that_will_not_open_is_tried_again(watcher, links, monkeypatch) -> None:
    w, seen = watcher
    busy = [_FakeHidDevice([], fail_open=OSError("open failed")) for _ in range(2)]
    free = _FakeHidDevice(
        [_short_report(shift=True), _short_report(shift=True, cue1=True)], watcher=w
    )
    hid = _install_fake_hid(monkeypatch, devices=[*busy, free])

    w._run_hidapi()

    assert hid.handed_out == [*busy, free]
    assert free.opened == (s4._VID, s4._PID)
    assert seen == ["cue1"]
    assert all(dev.closed for dev in hid.handed_out)


def test_an_unplugged_controller_is_opened_again(watcher, links, monkeypatch) -> None:
    w, seen = watcher
    first = _FakeHidDevice(
        [_short_report(shift=True), _short_report(shift=True, cue1=True)], unplugged=True
    )
    second = _FakeHidDevice(
        [_short_report(shift=True), _short_report(shift=True, cue2=True)], watcher=w
    )
    _install_fake_hid(monkeypatch, devices=[first, second])

    w._run_hidapi()

    assert first.closed, "the dead handle is given back before the next try"
    assert second.opened == (s4._VID, s4._PID)
    assert seen == ["cue1", "cue2"]


def test_a_button_held_when_the_controller_went_is_not_still_held(
    watcher, links, monkeypatch
) -> None:
    """Shift was down in the last report before the cable came out. The
    first report after it is back must not be read as Shift + something."""
    w, seen = watcher
    first = _FakeHidDevice([_short_report(shift=True)], unplugged=True)
    second = _FakeHidDevice([_long_report(3), _long_report(4)], watcher=w)
    _install_fake_hid(monkeypatch, devices=[first, second])

    w._run_hidapi()

    assert seen == []


def test_the_link_is_reported_as_it_comes_and_goes(watcher, links, monkeypatch) -> None:
    w, _seen = watcher
    first = _FakeHidDevice([_short_report()], unplugged=True)
    second = _FakeHidDevice([_short_report()], watcher=w)
    _install_fake_hid(monkeypatch, devices=[first, second])

    w._run_hidapi()

    assert [connected for connected, _message in links] == [True, False, True]
    assert all(message for _connected, message in links), "each one says something a user can read"


def test_the_link_is_not_reported_again_while_nothing_changes(watcher, links, monkeypatch) -> None:
    """Every failed attempt is the same news."""
    w, _seen = watcher
    gone = [_FakeHidDevice([], fail_open=OSError("open failed")) for _ in range(4)]
    gone[-1]._watcher = w
    hid = _install_fake_hid(monkeypatch, devices=gone)

    w._run_hidapi()

    assert len(hid.handed_out) == 4
    assert [connected for connected, _message in links] == [False]


def test_stop_is_noticed_while_waiting_to_try_again(watcher, links, monkeypatch) -> None:
    """The wait is slept in slices; closing the application must not take
    as long as the next attempt is away."""
    w, _seen = watcher
    monkeypatch.setattr(s4, "_RECONNECT_S", 30.0)
    _install_fake_hid(monkeypatch, _FakeHidDevice([], fail_open=OSError("open failed")))
    stopper = threading.Timer(0.2, w.stop)
    stopper.start()
    started = time.monotonic()

    w._run_hidapi()

    stopper.join()
    assert time.monotonic() - started < 5.0


# ── macOS says so too ───────────────────────────────────────────────────────


class _FakeLibrary:
    """IOKit or CoreFoundation with every call answered "fine"; the one
    answer that matters is what ``IOHIDManagerOpen`` returns."""

    def __init__(self, open_result: int = 0, on_run=None) -> None:
        self._open_result = open_result
        self._on_run = on_run

    def __getattr__(self, name: str):
        if name == "IOHIDManagerOpen":
            return lambda *_args: self._open_result
        if name == "CFRunLoopRunInMode":
            return lambda *_args: self._on_run()
        return lambda *_args: 1


def _fake_iokit(monkeypatch, w, open_result: int) -> None:
    monkeypatch.setattr(s4, "_make_matching_dict", lambda _vid, _pid: 1)
    monkeypatch.setattr(s4, "_iokit", _FakeLibrary(open_result), raising=False)
    monkeypatch.setattr(s4, "_cf", _FakeLibrary(on_run=w.stop), raising=False)
    monkeypatch.setattr(s4, "_kCFRunLoopDefaultMode", 1, raising=False)


def test_macos_reports_the_link_once_the_manager_is_open(watcher, monkeypatch) -> None:
    w, _seen = watcher
    said: list[tuple[bool, str]] = []
    w.link.connect(lambda connected, message: said.append((connected, message)))
    _fake_iokit(monkeypatch, w, open_result=0)

    w._run_iokit()

    [(connected, message)] = said
    assert connected is True and message


def test_macos_reports_nothing_for_a_manager_that_did_not_open(watcher, monkeypatch) -> None:
    w, _seen = watcher
    said: list[tuple[bool, str]] = []
    w.link.connect(lambda connected, message: said.append((connected, message)))
    # kIOReturnNotPermitted, 0xE00002E2, as the signed 32 bits it comes in.
    _fake_iokit(monkeypatch, w, open_result=0xE00002E2 - (1 << 32))

    w._run_iokit()

    assert said == []


def test_stop_ends_the_loop(watcher, monkeypatch) -> None:
    w, _seen = watcher
    monkeypatch.setattr(s4, "_IS_WINDOWS", True)
    dev = _FakeHidDevice([_short_report()] * 50, watcher=w)
    _install_fake_hid(monkeypatch, dev)
    w.stop()

    w._run_hidapi()
    assert dev.closed


# ── Presence ────────────────────────────────────────────────────────────────


def test_connected_finds_the_s4_by_vid_and_pid(monkeypatch) -> None:
    monkeypatch.setattr(s4, "_IS_WINDOWS", True)
    _install_fake_hid(
        monkeypatch,
        enumerate_result=[
            {"vendor_id": 0x1234, "product_id": 0x0001},
            {"vendor_id": s4._VID, "product_id": s4._PID},
        ],
    )
    assert s4.s4mk2_connected() is True


def test_connected_is_false_with_other_devices_present(monkeypatch) -> None:
    monkeypatch.setattr(s4, "_IS_WINDOWS", True)
    _install_fake_hid(
        monkeypatch,
        enumerate_result=[
            {"vendor_id": s4._VID, "product_id": 0x9999},  # another NI device
        ],
    )
    assert s4.s4mk2_connected() is False


def test_connected_is_false_when_hidapi_is_missing(monkeypatch) -> None:
    monkeypatch.setattr(s4, "_IS_WINDOWS", True)
    monkeypatch.setitem(sys.modules, "hid", None)
    assert s4.s4mk2_connected() is False


# ── Platform routing ────────────────────────────────────────────────────────


def test_run_picks_the_hidapi_backend_on_windows(watcher, monkeypatch) -> None:
    w, _seen = watcher
    monkeypatch.setattr(s4, "_IS_MACOS", False)
    monkeypatch.setattr(s4, "_IS_WINDOWS", True)
    called = []
    monkeypatch.setattr(w, "_run_hidapi", lambda: called.append("hidapi"))
    w.run()
    assert called == ["hidapi"]


def test_run_picks_iokit_on_macos(watcher, monkeypatch) -> None:
    w, _seen = watcher
    monkeypatch.setattr(s4, "_IS_MACOS", True)
    monkeypatch.setattr(s4, "_IS_WINDOWS", False)
    called = []
    monkeypatch.setattr(w, "_run_iokit", lambda: called.append("iokit"))
    w.run()
    assert called == ["iokit"]


def test_run_idles_on_anything_else(watcher, monkeypatch) -> None:
    """Linux: no backend, but the app must still start."""
    w, _seen = watcher
    monkeypatch.setattr(s4, "_IS_MACOS", False)
    monkeypatch.setattr(s4, "_IS_WINDOWS", False)
    w.run()
