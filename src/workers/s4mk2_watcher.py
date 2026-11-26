"""Traktor Kontrol S4 MK2 HID watcher.

Uses IOHIDManager (macOS IOKit) with push callbacks instead of polling.
This avoids draining the kernel HID event queue, so Traktor retains full
uninterrupted access to the device.

HID protocol sourced from the Mixxx S4 MK2 mapping:
  github.com/mixxxdj/mixxx → res/controllers/Traktor-Kontrol-S4-MK2-hid-scripts.js

Report layout. Offsets index the report **as the device sends it** — byte 0 is
the report ID — which is what hidapi returns and what IOKit's callback buffer
holds, so both backends decode with the same numbers:

  Short report 0x01 — buttons:
    payload[0x0C] (Mixxx offset 0x0C):
      bit 0x08 = Right Shift
    payload[0x0F] (Mixxx offset 0x0F):
      bit 0x40 = CH1 CUE → Deck A
      bit 0x80 = CH3 CUE → Deck C
    payload[0x0A] (Mixxx offset 0x0A):
      bit 0x40 = CH2 CUE → Deck B
      bit 0x80 = CH4 CUE → Deck D
    payload[0x13] (Mixxx offset 0x13):
      bit 0x04 = Browse encoder push (Mixxx binds it to [Library] GoToItem)

  Long report 0x02 — encoders:
    payload[0x02] (Mixxx offset 0x02), lower 4 bits:
      4-bit nibble (0–15, wraps); delta > 0 = CW = scroll down

Controls (all require Right Shift held):
  Right Shift + Browse encoder  → navigate results (CW = down)
  Right Shift + Browse push     → load selected into the first free deck
  Right Shift + CH1 CUE        → load selected to Deck A
  Right Shift + CH2 CUE        → load selected to Deck B
  Right Shift + CH3 CUE        → load selected to Deck C
  Right Shift + CH4 CUE        → load selected to Deck D
"""

from __future__ import annotations

import ctypes
import logging
import sys
import time
from ctypes import (
    CFUNCTYPE,
    POINTER,
    byref,
    c_bool,
    c_byte,
    c_double,
    c_int32,
    c_long,
    c_uint8,
    c_uint32,
    c_void_p,
)

from PySide6.QtCore import QThread, Signal

log = logging.getLogger("s4mk2")

_VID = 0x17CC
_PID = 0x1310

_REPORT_SHORT = 0x01
_REPORT_LONG = 0x02

# Payload byte indices — Mixxx offsets (0-based, no report-ID prefix)
_IDX_SHIFT = 0x0C  # Mixxx 0x0C — right shift button
_IDX_LEFT = 0x0F  # Mixxx 0x0F — CH1 (0x40) / CH3 (0x80)
_IDX_RIGHT = 0x0A  # Mixxx 0x0A — CH2 (0x40) / CH4 (0x80)
_IDX_BROWSE = 0x02  # Mixxx 0x02 — browse encoder nibble
_IDX_PUSH = 0x13  # Mixxx 0x13 — browse encoder push

_BIT_SHIFT = 0x08
_BIT_CUE_A = 0x40  # CH1 → Deck A
_BIT_CUE_C = 0x80  # CH3 → Deck C
_BIT_CUE_B = 0x40  # CH2 → Deck B
_BIT_CUE_D = 0x80  # CH4 → Deck D
_BIT_PUSH = 0x04  # browse encoder push

# ── IOKit / CoreFoundation setup (ctypes) ──────────────────────────────────
# macOS-only: the S4 MK2 watcher drives the device through IOHIDManager, which
# is part of macOS's IOKit framework and has no equivalent here on other
# platforms. We bind the ctypes symbols only on darwin; elsewhere the whole
# block is skipped, `s4mk2_connected()` returns False, and `S4MK2Watcher.run()`
# is a no-op — so the rest of the app runs unaffected (S4 MK2 hardware control
# is simply unavailable). Bringing it to Windows would mean a separate hidapi/
# WinUSB backend; out of scope for the cross-platform port.
_IS_MACOS = sys.platform == "darwin"
_IS_WINDOWS = sys.platform == "win32"

#: Longest report we read. The S4 MK2 declares an input report of 79 bytes
#: (HidP_GetCaps on Windows, report ID included); this is rounded up from that,
#: and caps how much a malformed length can make us copy.
_HID_READ_SIZE = 96

#: How long the Windows backend waits after an open or a read failed before
#: it tries the controller again. Long enough that a controller which is not
#: there costs nothing to keep asking for, short enough that plugging it in
#: feels noticed.
_RECONNECT_S = 2.0

#: The wait is slept in slices of this, so ``stop()`` is noticed within one.
_RECONNECT_SLICE_S = 0.05

#: What :attr:`S4MK2Watcher.link` says, for Settings to show as it is.
_LINK_UP = "Connected"
_LINK_WAITING = "Not connected — waiting for the controller"
_LINK_LOST = "Connection lost — waiting for the controller"
_LINK_NO_HIDAPI = "Hardware control is off — hidapi is not installed"

# Scalar types (pure-Python ctypes aliases — safe to define everywhere; the
# report-callback prototype below references them).
_IOReturn = c_int32
_IOOptionBits = c_uint32
_CFIndex = c_long  # SInt64 on 64-bit macOS
_CFNumberType = c_long

_kIOHIDOptionsTypeNone = 0
_kCFNumberIntType = 9  # kCFNumberIntType → C int

# void callback(context, result, sender, type, reportID, report, length).
# Pure ctypes prototype (no library symbols), so define it unconditionally —
# the run loop on macOS references it when registering the input-report callback.
_IOHIDReportCB = CFUNCTYPE(
    None,
    c_void_p,
    _IOReturn,
    c_void_p,
    c_uint32,
    c_uint32,
    POINTER(c_uint8),
    _CFIndex,
)

if _IS_MACOS:
    _iokit = ctypes.CDLL("/System/Library/Frameworks/IOKit.framework/IOKit")
    _cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")

    # kCFRunLoopDefaultMode is a CFStringRef exported by CoreFoundation
    _kCFRunLoopDefaultMode = c_void_p.in_dll(_cf, "kCFRunLoopDefaultMode").value

    # Addresses of the standard CF dictionary callback structs
    _key_cbs = (c_byte * 1).in_dll(_cf, "kCFTypeDictionaryKeyCallBacks")
    _val_cbs = (c_byte * 1).in_dll(_cf, "kCFTypeDictionaryValueCallBacks")

    # CF helpers
    _cf.CFStringCreateWithCString.restype = c_void_p
    _cf.CFStringCreateWithCString.argtypes = [c_void_p, ctypes.c_char_p, c_uint32]

    _cf.CFNumberCreate.restype = c_void_p
    _cf.CFNumberCreate.argtypes = [c_void_p, _CFNumberType, c_void_p]

    _cf.CFDictionaryCreateMutable.restype = c_void_p
    _cf.CFDictionaryCreateMutable.argtypes = [c_void_p, _CFIndex, c_void_p, c_void_p]

    _cf.CFDictionarySetValue.restype = None
    _cf.CFDictionarySetValue.argtypes = [c_void_p, c_void_p, c_void_p]

    _cf.CFRelease.restype = None
    _cf.CFRelease.argtypes = [c_void_p]

    _cf.CFRunLoopGetCurrent.restype = c_void_p
    _cf.CFRunLoopGetCurrent.argtypes = []

    _cf.CFRunLoopRunInMode.restype = c_int32
    _cf.CFRunLoopRunInMode.argtypes = [c_void_p, c_double, c_bool]

    # IOHIDManager
    _iokit.IOHIDManagerCreate.restype = c_void_p
    _iokit.IOHIDManagerCreate.argtypes = [c_void_p, _IOOptionBits]

    _iokit.IOHIDManagerSetDeviceMatching.restype = None
    _iokit.IOHIDManagerSetDeviceMatching.argtypes = [c_void_p, c_void_p]

    _iokit.IOHIDManagerOpen.restype = _IOReturn
    _iokit.IOHIDManagerOpen.argtypes = [c_void_p, _IOOptionBits]

    _iokit.IOHIDManagerClose.restype = _IOReturn
    _iokit.IOHIDManagerClose.argtypes = [c_void_p, _IOOptionBits]

    _iokit.IOHIDManagerScheduleWithRunLoop.restype = None
    _iokit.IOHIDManagerScheduleWithRunLoop.argtypes = [c_void_p, c_void_p, c_void_p]

    _iokit.IOHIDManagerUnscheduleFromRunLoop.restype = None
    _iokit.IOHIDManagerUnscheduleFromRunLoop.argtypes = [c_void_p, c_void_p, c_void_p]

    _iokit.IOHIDManagerRegisterInputReportCallback.restype = None
    _iokit.IOHIDManagerRegisterInputReportCallback.argtypes = [
        c_void_p,
        _IOHIDReportCB,
        c_void_p,
    ]

    _iokit.IOHIDManagerCopyDevices.restype = c_void_p
    _iokit.IOHIDManagerCopyDevices.argtypes = [c_void_p]

    _cf.CFSetGetCount.restype = _CFIndex
    _cf.CFSetGetCount.argtypes = [c_void_p]


def _make_matching_dict(vid: int, pid: int) -> c_void_p:
    k_vid = _cf.CFStringCreateWithCString(None, b"VendorID", 0)
    k_pid = _cf.CFStringCreateWithCString(None, b"ProductID", 0)
    v_vid = c_int32(vid)
    v_pid = c_int32(pid)
    n_vid = _cf.CFNumberCreate(None, _kCFNumberIntType, byref(v_vid))
    n_pid = _cf.CFNumberCreate(None, _kCFNumberIntType, byref(v_pid))

    d = _cf.CFDictionaryCreateMutable(
        None,
        2,
        ctypes.addressof(_key_cbs),
        ctypes.addressof(_val_cbs),
    )
    _cf.CFDictionarySetValue(d, k_vid, n_vid)
    _cf.CFDictionarySetValue(d, k_pid, n_pid)

    for ref in (k_vid, k_pid, n_vid, n_pid):
        _cf.CFRelease(ref)
    return d


def s4mk2_connected() -> bool:
    """Is an S4 MK2 plugged in?

    macOS answers with pure IOKit (no ``hid`` package needed); Windows
    enumerates through hidapi. Anywhere else, and anywhere the probe itself
    fails, the answer is False — "no hardware control" is always a safe
    thing to be wrong about, where a spurious True would leave the UI
    claiming a device it cannot read.
    """
    if _IS_WINDOWS:
        try:
            import hid

            return any(
                d.get("vendor_id") == _VID and d.get("product_id") == _PID for d in hid.enumerate()
            )
        except Exception:
            return False
    if not _IS_MACOS:
        return False
    try:
        matching = _make_matching_dict(_VID, _PID)
        mgr = _iokit.IOHIDManagerCreate(None, _kIOHIDOptionsTypeNone)
        _iokit.IOHIDManagerSetDeviceMatching(mgr, matching)
        _cf.CFRelease(matching)
        _iokit.IOHIDManagerOpen(mgr, _kIOHIDOptionsTypeNone)
        device_set = _iokit.IOHIDManagerCopyDevices(mgr)
        found = bool(device_set and _cf.CFSetGetCount(device_set) > 0)
        if device_set:
            _cf.CFRelease(device_set)
        _iokit.IOHIDManagerClose(mgr, _kIOHIDOptionsTypeNone)
        _cf.CFRelease(mgr)
        return found
    except Exception:
        return False


class S4MK2Watcher(QThread):
    """Observes S4 MK2 HID reports via IOHIDManager push callbacks."""

    navigate = Signal(int)  # negative = up, positive = down
    load_deck = Signal(str)  # deck letter: A / B / C / D
    control = Signal(str)  # raw control id — "cue1".."cue4", "browse_cw/ccw"
    learned = Signal(str)  # same, but while learn mode is armed
    #: (connected, message for the user) — whether the backend has the
    #: controller open, which is not the same as it being plugged in. Emitted
    #: when that changes and not again until it changes back.
    link = Signal(bool, str)

    #: Every control this watcher can report, with a human label. The Settings
    #: "learn" UI lists these so a control can be bound without owning an S4.
    CONTROLS: tuple[tuple[str, str], ...] = (
        ("cue1", "R.Shift + CH1 CUE"),
        ("cue2", "R.Shift + CH2 CUE"),
        ("cue3", "R.Shift + CH3 CUE"),
        ("cue4", "R.Shift + CH4 CUE"),
        ("browse_ccw", "R.Shift + Browse ↺"),
        ("browse_cw", "R.Shift + Browse ↻"),
        ("browse_push", "R.Shift + Browse push"),
    )

    def __init__(self, parent=None):
        super().__init__(parent)
        self._stop = False
        self._shift = False
        self._prev_cue_a = False
        self._prev_cue_b = False
        self._prev_cue_c = False
        self._prev_cue_d = False
        self._prev_push = False
        self._prev_nibble: int | None = None
        self._cb = None  # keep ctypes callback alive
        self._learning = False
        self._linked: bool | None = None  # what link last said; None: nothing

    def _set_link(self, connected: bool, message: str) -> None:
        """Say whether the controller is open, if that is news."""
        if connected == self._linked:
            return
        self._linked = connected
        self.link.emit(connected, message)

    def _forget_held_controls(self) -> None:
        """Start from nothing held and no encoder position.

        For a controller that went away: the last report it sent is not the
        state it comes back in, and a Shift remembered as down would turn the
        first turn of the encoder into a command.
        """
        self._shift = False
        self._prev_cue_a = self._prev_cue_b = False
        self._prev_cue_c = self._prev_cue_d = False
        self._prev_push = False
        self._prev_nibble = None

    def set_learning(self, learning: bool) -> None:
        """Arm learn mode: the next control is reported, not acted on.

        Without this, binding a control means triggering whatever it is
        currently bound to — pressing CH1 CUE to rebind it would load a track.
        """
        self._learning = bool(learning)

    def _emit_control(self, control: str) -> None:
        if self._learning:
            self.learned.emit(control)
        else:
            self.control.emit(control)

    def stop(self) -> None:
        self._stop = True

    # ── Report handlers ───────────────────────────────────────────────────

    def _short(self, data: list[int]) -> None:
        if len(data) <= max(_IDX_SHIFT, _IDX_LEFT, _IDX_RIGHT):
            return
        self._shift = bool(data[_IDX_SHIFT] & _BIT_SHIFT)
        if not self._shift:
            self._prev_cue_a = self._prev_cue_b = False
            self._prev_cue_c = self._prev_cue_d = False
            self._prev_push = False
            return

        cue_a = bool(data[_IDX_LEFT] & _BIT_CUE_A)
        cue_c = bool(data[_IDX_LEFT] & _BIT_CUE_C)
        cue_b = bool(data[_IDX_RIGHT] & _BIT_CUE_B)
        cue_d = bool(data[_IDX_RIGHT] & _BIT_CUE_D)

        # Emit the control that was pressed, not the deck it used to mean —
        # what a control *does* is the registry's business (core.actions), so
        # the mapping can be changed without touching the HID decode.
        if cue_a and not self._prev_cue_a:
            self._emit_control("cue1")
        if cue_b and not self._prev_cue_b:
            self._emit_control("cue2")
        if cue_c and not self._prev_cue_c:
            self._emit_control("cue3")
        if cue_d and not self._prev_cue_d:
            self._emit_control("cue4")

        self._prev_cue_a = cue_a
        self._prev_cue_b = cue_b
        self._prev_cue_c = cue_c
        self._prev_cue_d = cue_d

        # The push byte sits past the CUE bytes, so it gets its own length
        # check: a report too short for it must still cost us nothing above.
        if len(data) > _IDX_PUSH:
            push = bool(data[_IDX_PUSH] & _BIT_PUSH)
            if push and not self._prev_push:
                self._emit_control("browse_push")
            self._prev_push = push

    def _long(self, data: list[int]) -> None:
        if len(data) <= _IDX_BROWSE:
            return
        curr = data[_IDX_BROWSE] & 0x0F
        prev = self._prev_nibble
        self._prev_nibble = curr

        if prev is None or curr == prev or not self._shift:
            return

        if prev == 15 and curr == 0:
            delta = 1
        elif prev == 0 and curr == 15:
            delta = -1
        else:
            delta = curr - prev
        if delta:
            # One notch per report; the registry decides what a notch does.
            self._emit_control("browse_cw" if delta > 0 else "browse_ccw")

    # ── Thread entry point ────────────────────────────────────────────────

    def _dispatch(self, report_id: int, payload: list[int]) -> None:
        """Route one HID report to its decoder.

        Both backends land here with the report **exactly as the device sent
        it** — report ID at index 0 — which is what the Mixxx offsets at the
        top of this module are written against.
        """
        if report_id == _REPORT_SHORT:
            self._short(payload)
        elif report_id == _REPORT_LONG:
            self._long(payload)

    def run(self) -> None:
        if _IS_MACOS:
            self._run_iokit()
        elif _IS_WINDOWS:
            self._run_hidapi()
        else:
            log.info("S4 MK2 watcher: no HID backend for %s — idle", sys.platform)

    def _run_hidapi(self) -> None:
        """Windows: read the device through hidapi.

        IOKit pushes reports at us; hidapi has no callback, so this polls with
        a short blocking read. The 20 ms timeout is what makes ``stop()``
        responsive — it is not a poll interval, the read returns as soon as a
        report arrives.

        The open may fail with the device in use: macOS HID is shareable,
        which is what lets this watch the S4 while Traktor owns it, and
        whether Windows and NI's driver allow the same with Traktor running
        is still to be found out. Either way a controller that cannot be
        opened, or that stops answering, is not the end of it: the device is
        closed and tried again every :data:`_RECONNECT_S` until ``stop()``,
        and :attr:`link` says which of the two it currently is.
        """
        try:
            import hid
        except ImportError:
            log.warning("S4 MK2: hidapi is not installed — hardware control off")
            self._set_link(False, _LINK_NO_HIDAPI)
            return

        while True:
            self._read_until_lost(hid)
            self._wait_to_try_again()
            if self._stop:
                return

    def _read_until_lost(self, hid) -> None:
        """Open the controller and read it until it fails or ``stop()``.

        Returns either way, with the device closed. Never raises: what went
        wrong goes to the log and to :attr:`link`.
        """
        dev = None
        try:
            try:
                dev = hid.device()
                dev.open(_VID, _PID)
                dev.set_nonblocking(False)
            except Exception as exc:
                # Whatever it was, it is tried again. Logged once, not every
                # two seconds for as long as the controller is unplugged.
                if self._linked is not False:
                    log.warning("S4 MK2: cannot open the controller (%s) — will keep trying", exc)
                self._set_link(False, _LINK_WAITING)
                return

            log.info("S4 MK2: hidapi active (VID=%04x PID=%04x)", _VID, _PID)
            self._forget_held_controls()
            self._set_link(True, _LINK_UP)
            try:
                self._read(dev)
            except Exception as exc:
                # Unplugged, most likely. One line: on Windows the read says
                # "read error" and nothing else, and a traceback made an
                # expected unplug look like a crash in the log.
                log.warning("S4 MK2: lost the controller (%s) — will keep trying", exc)
                self._set_link(False, _LINK_LOST)
        finally:
            if dev is not None:
                try:
                    dev.close()
                except Exception:
                    log.debug("S4 MK2: close failed", exc_info=True)

    def _read(self, dev) -> None:
        """Hand every report *dev* sends to the decoder, until ``stop()``.
        Raises what the read raises once the controller is gone."""
        while not self._stop:
            data = dev.read(_HID_READ_SIZE, timeout_ms=20)
            if not data:
                continue
            # Pass the report through whole, report ID and all: the Mixxx
            # offsets index the report as the device sends it, which is
            # what both hidapi and IOKit's callback buffer hold. Stripping
            # the ID here shifted every offset by one and silently decoded
            # the wrong buttons — measured on an S4 MK2 on Windows, Right
            # Shift arrived on byte 0x0B and Browse push on 0x12, one short
            # of 0x0C and 0x13, so no control ever fired.
            self._dispatch(data[0], list(data))

    def _wait_to_try_again(self) -> None:
        """Sleep :data:`_RECONNECT_S`, in slices ``stop()`` can cut short."""
        deadline = time.monotonic() + _RECONNECT_S
        while not self._stop:
            left = deadline - time.monotonic()
            if left <= 0:
                return
            time.sleep(min(_RECONNECT_SLICE_S, left))

    def _run_iokit(self) -> None:
        matching = _make_matching_dict(_VID, _PID)
        if not matching:
            log.error("S4 MK2: failed to create matching dict")
            return

        mgr = _iokit.IOHIDManagerCreate(None, _kIOHIDOptionsTypeNone)
        if not mgr:
            log.error("S4 MK2: failed to create IOHIDManager")
            _cf.CFRelease(matching)
            return

        _iokit.IOHIDManagerSetDeviceMatching(mgr, matching)
        _cf.CFRelease(matching)

        def _report_cb(context, result, sender, rtype, report_id, report, length):
            n = min(int(length), _HID_READ_SIZE)
            self._dispatch(int(report_id), [report[i] for i in range(n)])

        self._cb = _IOHIDReportCB(_report_cb)  # must stay alive for the loop
        _iokit.IOHIDManagerRegisterInputReportCallback(mgr, self._cb, None)

        run_loop = _cf.CFRunLoopGetCurrent()
        _iokit.IOHIDManagerScheduleWithRunLoop(mgr, run_loop, _kCFRunLoopDefaultMode)

        ret = _iokit.IOHIDManagerOpen(mgr, _kIOHIDOptionsTypeNone)
        if ret != 0:
            log.error("S4 MK2: IOHIDManagerOpen failed (0x%08x)", ret & 0xFFFFFFFF)
            _iokit.IOHIDManagerUnscheduleFromRunLoop(mgr, run_loop, _kCFRunLoopDefaultMode)
            _cf.CFRelease(mgr)
            return

        log.info("S4 MK2: IOHIDManager active (VID=%04x PID=%04x)", _VID, _PID)
        self._set_link(True, _LINK_UP)

        while not self._stop:
            # Pump the run loop in 20 ms slices so stop() is noticed quickly
            _cf.CFRunLoopRunInMode(_kCFRunLoopDefaultMode, 0.02, False)

        _iokit.IOHIDManagerUnscheduleFromRunLoop(mgr, run_loop, _kCFRunLoopDefaultMode)
        _iokit.IOHIDManagerClose(mgr, _kIOHIDOptionsTypeNone)
        _cf.CFRelease(mgr)
