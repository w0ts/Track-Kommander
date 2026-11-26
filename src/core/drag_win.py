"""Win32 backend for programmatic drag-and-drop into Traktor decks.

The Windows counterpart of the Quartz code in ``core/drag.py``. Same idea:
``ResultsTable.startDrag`` starts a real Qt ``QDrag.exec`` carrying a file-URL
(the OLE equivalent of dragging a file from Explorer); we synthesize a mouse
drag with ``SendInput`` so that drag session follows the cursor onto Traktor's
deck drop zone, where Traktor accepts the file drop and loads the track.

Coordinate spaces (this matters on HiDPI / scaled displays):
  * Win32 ``GetWindowRect`` and ``SendInput`` absolute coords are PHYSICAL pixels.
  * Qt screen coords (e.g. ``row_screen_center``) are LOGICAL pixels.
  * Conversion factor is the screen's devicePixelRatio (2.0 on a 200% display).
``core/drag.py`` converts the drag *source* (Qt logical) to physical via
:func:`device_pixel_ratio` before calling :func:`do_drag`; the *destination*
comes from ``GetWindowRect`` (already physical) scaled by the normalised,
DPI-independent per-deck calibration.

This module is imported only on Windows (guarded by the caller).
"""

from __future__ import annotations

import ctypes
import logging
import math
import threading
import time
from ctypes import wintypes

from core import platform_modules
from core.traktor_process import is_traktor_exe

_win = platform_modules.win_ctypes()

_log = logging.getLogger(__name__)

_user32 = _win.WinDLL("user32", use_last_error=True)
_kernel32 = _win.WinDLL("kernel32", use_last_error=True)

# ── ctypes prototypes ────────────────────────────────────────────────────────
_WNDENUMPROC = _win.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

_user32.EnumWindows.argtypes = [_WNDENUMPROC, wintypes.LPARAM]
_user32.EnumWindows.restype = wintypes.BOOL
_user32.IsWindowVisible.argtypes = [wintypes.HWND]
_user32.IsWindowVisible.restype = wintypes.BOOL
_user32.IsIconic.argtypes = [wintypes.HWND]
_user32.IsIconic.restype = wintypes.BOOL
_user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
_user32.GetWindowRect.restype = wintypes.BOOL
_user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
_user32.GetWindowThreadProcessId.restype = wintypes.DWORD
_user32.WindowFromPoint.argtypes = [wintypes.POINT]
_user32.WindowFromPoint.restype = wintypes.HWND
_user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
_user32.GetAncestor.restype = wintypes.HWND
_user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
_user32.GetCursorPos.restype = wintypes.BOOL
_user32.GetSystemMetrics.argtypes = [ctypes.c_int]
_user32.GetSystemMetrics.restype = ctypes.c_int

_SM_REMOTESESSION = 0x1000


def is_remote_session() -> bool:
    """True when running inside an RDP / remote desktop session."""
    return bool(_user32.GetSystemMetrics(_SM_REMOTESESSION))


_kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_kernel32.OpenProcess.restype = wintypes.HANDLE
_kernel32.QueryFullProcessImageNameW.argtypes = [
    wintypes.HANDLE,
    wintypes.DWORD,
    wintypes.LPWSTR,
    ctypes.POINTER(wintypes.DWORD),
]
_kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
_kernel32.CloseHandle.restype = wintypes.BOOL

_GA_ROOT = 2
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

# SendInput
_INPUT_MOUSE = 0
_MOUSEEVENTF_MOVE = 0x0001
_MOUSEEVENTF_LEFTDOWN = 0x0002
_MOUSEEVENTF_LEFTUP = 0x0004
_MOUSEEVENTF_ABSOLUTE = 0x8000
_MOUSEEVENTF_VIRTUALDESK = 0x4000
_MOVE_ABS = _MOUSEEVENTF_MOVE | _MOUSEEVENTF_ABSOLUTE | _MOUSEEVENTF_VIRTUALDESK

_SM_XVIRTUALSCREEN = 76
_SM_YVIRTUALSCREEN = 77
_SM_CXVIRTUALSCREEN = 78
_SM_CYVIRTUALSCREEN = 79


# ULONG_PTR (pointer-sized) — the real type of MOUSEINPUT.dwExtraInfo. We carry
# a private signature here on every synthesized event so the WH_MOUSE_LL hook can
# tell our injected drag from the operator's (or RDP's) real mouse with certainty
# — far more reliable than the LLMHF_INJECTED flag, which RDP sets on the client's
# own mouse too (so the flag can't distinguish them, the reason the hook used to
# be disabled over RDP and the drop then missed).
ULONG_PTR = ctypes.c_size_t
_INJECT_TAG = 0x544B4452  # 'TKDR' — marks events this module synthesized


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", _MOUSEINPUT)]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


_user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(_INPUT), ctypes.c_int]
_user32.SendInput.restype = wintypes.UINT


# ── physical-input suppression during a drag ─────────────────────────────────
# A synthesized drag tracks the cursor across Traktor's drop zone over ~1s. If
# the operator's real mouse / trackpad / S4 jog moves the cursor during that
# window it fights the synthesized motion and the drop misses (the random
# "sometimes loads, sometimes not"). A WH_MOUSE_LL hook lets us swallow *real*
# mouse events while passing our own injected ones (BlockInput can't — it stops
# our SendInput too). The hook runs on whichever thread installs it; it must be
# a thread that pumps messages — the GUI thread does (and QDrag.exec pumps it).
_WH_MOUSE_LL = 14
_LLMHF_INJECTED = 0x00000001
_HC_ACTION = 0

_HOOKPROC = _win.WINFUNCTYPE(ctypes.c_long, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)


class _MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt", wintypes.POINT),
        ("mouseData", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


_user32.SetWindowsHookExW.argtypes = [ctypes.c_int, _HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
_user32.SetWindowsHookExW.restype = wintypes.HHOOK
_user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
_user32.UnhookWindowsHookEx.restype = wintypes.BOOL
_user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
_user32.CallNextHookEx.restype = ctypes.c_long

_mouse_hook = None


def _ll_mouse_proc(ncode, wparam, lparam):
    if ncode == _HC_ACTION:
        st = ctypes.cast(lparam, ctypes.POINTER(_MSLLHOOKSTRUCT)).contents
        # Pass ONLY the events this module synthesized (tagged in dwExtraInfo);
        # swallow everything else — the operator's real mouse AND RDP's cursor
        # sync — so nothing fights the synthesized drag. Keying on our own tag
        # (not LLMHF_INJECTED) is what makes this correct inside an RDP session.
        if st.dwExtraInfo != _INJECT_TAG:
            return 1
    return _user32.CallNextHookEx(None, ncode, wparam, lparam)


# Keep a permanent reference so the callback isn't garbage-collected while set.
_ll_mouse_proc_ptr = _HOOKPROC(_ll_mouse_proc)


def block_physical_mouse(block: bool) -> None:
    """Install/remove a low-level hook that swallows every mouse event except
    the ones this module synthesized (tagged via ``_INJECT_TAG`` in
    dwExtraInfo). Call on the GUI thread.

    Runs in RDP sessions too. The hook keys on our private tag rather than
    LLMHF_INJECTED, so it reliably tells our injected drag apart from RDP's
    cursor-sync stream and the operator's real mouse, and swallows both — our
    synthesized glide is then the only thing moving the cursor, so the OLE drop
    lands on the deck instead of being knocked off target (the RDP failure mode
    that returned IgnoreAction). The cursor stays visible throughout because our
    own MOVE events drive it; the hook is always removed in ``finally`` so input
    can never stay blocked.
    """
    global _mouse_hook
    if block:
        if _mouse_hook is None:
            _mouse_hook = _user32.SetWindowsHookExW(_WH_MOUSE_LL, _ll_mouse_proc_ptr, None, 0)
            if not _mouse_hook:
                _log.warning(
                    "block_physical_mouse: SetWindowsHookEx failed (err %d)", _win.get_last_error()
                )
                _mouse_hook = None
    else:
        if _mouse_hook:
            _user32.UnhookWindowsHookEx(_mouse_hook)
            _mouse_hook = None


# ── process / window helpers ─────────────────────────────────────────────────


def _exe_for_pid(pid: int) -> str | None:
    """Full executable path for *pid*, or None."""
    if not pid:
        return None
    h = _kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return None
    try:
        size = wintypes.DWORD(32768)
        buf = ctypes.create_unicode_buffer(size.value)
        if _kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return buf.value
        return None
    finally:
        _kernel32.CloseHandle(h)


def is_traktor_hwnd(hwnd) -> bool:
    """True when *hwnd* belongs to Traktor (the rule in
    :func:`core.traktor_process.is_traktor_exe`)."""
    pid = wintypes.DWORD()
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return is_traktor_exe(_exe_for_pid(pid.value))


def traktor_window() -> tuple[int, tuple[float, float, float, float]] | None:
    """Return ``(hwnd, (x, y, w, h))`` for Traktor's largest visible window.

    Bounds are PHYSICAL pixels (Win32). Matches by executable name via the
    window's owning process, so no fragile window-title matching is needed.
    Returns None if no Traktor window is on screen.
    """
    best: list = [None, 0.0]  # [(hwnd, bounds), area]

    @_WNDENUMPROC
    def _cb(hwnd, _lparam):
        if not _user32.IsWindowVisible(hwnd):
            return True
        # A minimized window reports the magic (-32000, -32000) rect — useless
        # for snapping, capture and drop targeting alike. Treat it as absent.
        if _user32.IsIconic(hwnd):
            return True
        if not is_traktor_hwnd(hwnd):
            return True
        r = wintypes.RECT()
        if not _user32.GetWindowRect(hwnd, ctypes.byref(r)):
            return True
        w, h = r.right - r.left, r.bottom - r.top
        area = float(w) * float(h)
        if area > best[1]:
            best[0] = (int(hwnd), (float(r.left), float(r.top), float(w), float(h)))
            best[1] = area
        return True

    _user32.EnumWindows(_cb, 0)
    return best[0]


def exe_under_point(x: float, y: float) -> str | None:
    """Executable path of the top-level window under screen point (x, y).

    Used as the drop-target safety check — the window we're about to drop onto
    must belong to Traktor. Coords are PHYSICAL pixels.
    """
    pt = wintypes.POINT(int(x), int(y))
    hwnd = _user32.WindowFromPoint(pt)
    if not hwnd:
        return None
    root = _user32.GetAncestor(hwnd, _GA_ROOT) or hwnd
    pid = wintypes.DWORD()
    _user32.GetWindowThreadProcessId(root, ctypes.byref(pid))
    return _exe_for_pid(pid.value)


def point_is_traktor(x: float, y: float) -> bool:
    """True when the window under (x, y) belongs to Traktor. PHYSICAL px."""
    return is_traktor_exe(exe_under_point(x, y))


# ── screenshot (for deck calibration) ────────────────────────────────────────


def capture():
    """Return ``(QImage, bounds)`` of the Traktor window, or None.

    ``bounds`` is the PHYSICAL ``(x, y, w, h)`` from :func:`traktor_window`.
    Uses ``QScreen.grabWindow`` so no extra capture dependency is needed.
    """
    found = traktor_window()
    if not found:
        return None
    hwnd, bounds = found
    from PySide6.QtGui import QGuiApplication

    screen = QGuiApplication.primaryScreen()
    if screen is None:
        return None
    pm = screen.grabWindow(hwnd)
    if pm.isNull():
        return None
    return _crop_to_content(pm.toImage()), bounds


def _crop_to_content(img):
    """Trim black padding around the captured window content.

    On a HiDPI display ``grabWindow`` returns a physical-size bitmap, but the
    window paints at logical size into the top-left corner, leaving the rest
    black (the deck-letter detector then looks in the wrong place and finds
    nothing). Cropping to the non-black bounding box recovers the real UI so
    detection sees correct *relative* positions. A normal full-frame capture
    (no padding) is returned unchanged.
    """
    import numpy as np
    from PySide6.QtGui import QImage

    if img.format() != QImage.Format.Format_RGBA8888:
        img = img.convertToFormat(QImage.Format.Format_RGBA8888)
    ptr = img.constBits()
    if hasattr(ptr, "setsize"):
        ptr.setsize(img.sizeInBytes())
    stride = img.bytesPerLine() // 4
    arr = np.frombuffer(ptr, dtype=np.uint8).reshape(img.height(), stride, 4)
    lum = arr[:, : img.width(), :3].mean(axis=2)
    rows = np.where(lum.mean(axis=1) > 2)[0]
    cols = np.where(lum.mean(axis=0) > 2)[0]
    if len(rows) == 0 or len(cols) == 0:
        return img  # all black — nothing to crop to
    y0, y1, x0, x1 = int(rows[0]), int(rows[-1]), int(cols[0]), int(cols[-1])
    if (x1 - x0 + 1) >= img.width() - 4 and (y1 - y0 + 1) >= img.height() - 4:
        return img  # already full-frame — no padding to trim
    return img.copy(x0, y0, x1 - x0 + 1, y1 - y0 + 1)


_SM_CXSCREEN = 0

# SetWindowPos — used to raise our window above Traktor WITHOUT activating it.
_user32.SetWindowPos.argtypes = [
    wintypes.HWND,
    wintypes.HWND,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.UINT,
]
_user32.SetWindowPos.restype = wintypes.BOOL

_HWND_TOPMOST = wintypes.HWND(-1)
_HWND_NOTOPMOST = wintypes.HWND(-2)
_SWP_NOSIZE = 0x0001
_SWP_NOMOVE = 0x0002
_SWP_NOACTIVATE = 0x0010


def raise_above_without_focus(hwnd: int) -> bool:
    """Put *hwnd* at the top of the normal z-order without activating it.

    ``raise_()``/``activateWindow()`` from a non-foreground process are
    refused by the foreground lock — the OS just flashes the taskbar button.
    Toggling WS_EX_TOPMOST on and off is permitted from the background and
    leaves the window above everything (incl. Traktor) while keyboard focus
    stays where it is — exactly what the keep-on-top watchdog needs.
    """
    flags = _SWP_NOSIZE | _SWP_NOMOVE | _SWP_NOACTIVATE
    h = wintypes.HWND(int(hwnd))
    ok = bool(_user32.SetWindowPos(h, _HWND_TOPMOST, 0, 0, 0, 0, flags))
    ok = bool(_user32.SetWindowPos(h, _HWND_NOTOPMOST, 0, 0, 0, 0, flags)) and ok
    if not ok:
        _log.warning(
            "raise_above_without_focus: SetWindowPos failed (err %d)", _win.get_last_error()
        )
    return ok


def set_topmost(hwnd: int, on: bool) -> bool:
    """Keep *hwnd* above every non-topmost window, or stop doing so."""
    flags = _SWP_NOSIZE | _SWP_NOMOVE | _SWP_NOACTIVATE
    where = _HWND_TOPMOST if on else _HWND_NOTOPMOST
    ok = bool(_user32.SetWindowPos(wintypes.HWND(int(hwnd)), where, 0, 0, 0, 0, flags))
    if not ok:
        _log.warning("set_topmost(%s): SetWindowPos failed (err %d)", on, _win.get_last_error())
    return ok


_user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
_user32.ShowWindow.restype = wintypes.BOOL

_HWND_TOP = wintypes.HWND(0)
_SW_RESTORE = 9


def _any_traktor_hwnd() -> int | None:
    """Largest visible Traktor hwnd — minimized ones included.

    Unlike :func:`traktor_window` (which needs usable bounds and therefore
    skips iconic windows), this only needs a handle to restore/raise. A
    non-minimized window still wins over an iconic one, whose (-32000)
    rect would make its area meaningless.
    """
    best: list = [None, -1.0]  # [hwnd, area]

    @_WNDENUMPROC
    def _cb(hwnd, _lparam):
        if not _user32.IsWindowVisible(hwnd) or not is_traktor_hwnd(hwnd):
            return True
        if _user32.IsIconic(hwnd):
            if best[0] is None:
                best[0] = int(hwnd)
            return True
        r = wintypes.RECT()
        if not _user32.GetWindowRect(hwnd, ctypes.byref(r)):
            return True
        area = float(r.right - r.left) * float(r.bottom - r.top)
        if area > best[1]:
            best[0], best[1] = int(hwnd), area
        return True

    _user32.EnumWindows(_cb, 0)
    return best[0]


def restore_traktor_window() -> bool:
    """Un-minimize Traktor and put it at the top of the normal z-order.

    Companion to Kommander's own restore-from-taskbar: bring Traktor back
    up too, so both windows reappear together. ``SW_RESTORE`` activates
    the restored window — the caller re-raises Kommander afterwards.
    Returns True when a Traktor window was found.
    """
    hwnd = _any_traktor_hwnd()
    if hwnd is None:
        return False
    h = wintypes.HWND(hwnd)
    if _user32.IsIconic(h):
        _user32.ShowWindow(h, _SW_RESTORE)
    else:
        # Already restored, just buried — raise without activating.
        _user32.SetWindowPos(h, _HWND_TOP, 0, 0, 0, 0, _SWP_NOSIZE | _SWP_NOMOVE | _SWP_NOACTIVATE)
    return True


def window_rect(hwnd) -> tuple[float, float, float, float] | None:
    """Physical-pixel ``(left, top, w, h)`` of *hwnd* via GetWindowRect, or None."""
    r = wintypes.RECT()
    if not _user32.GetWindowRect(int(hwnd), ctypes.byref(r)):
        return None
    return (float(r.left), float(r.top), float(r.right - r.left), float(r.bottom - r.top))


def device_pixel_ratio() -> float:
    """Logical→physical scale factor for the primary screen.

    Qt's ``QScreen.devicePixelRatio()`` is the obvious source, but on Windows
    it intermittently reports 1.0 even when the process is per-monitor-DPI-aware
    and the window geometry is still in *logical* pixels (verified: GetWindowRect
    returns physical pixels while devicePixelRatio() returns 1.0). Trusting that
    stale 1.0 leaves the logical→physical mouse conversion un-scaled, so the
    synthesized drag press lands at half position and misses the row.

    To be robust we derive the ratio from Win32's PHYSICAL primary-screen width
    (``SM_CXSCREEN`` — physical while DPI-aware) divided by Qt's LOGICAL primary
    screen width. This stays correct whether Qt scales (geometry 1728, dpr 2),
    doesn't (geometry 3456, dpr 1), or misreports the dpr (geometry 1728, dpr 1).
    Falls back to Qt's value if the numbers look implausible.
    """
    from PySide6.QtGui import QGuiApplication

    screen = QGuiApplication.primaryScreen()
    if screen is None:
        return 1.0
    qt_dpr = float(screen.devicePixelRatio())
    try:
        qt_logical_w = float(screen.geometry().width())
        phys_w = float(_user32.GetSystemMetrics(_SM_CXSCREEN))
        if qt_logical_w > 0 and phys_w > 0:
            ratio = phys_w / qt_logical_w
            if 0.9 <= ratio <= 8.0:
                return ratio
    except Exception:  # pragma: no cover — defensive
        _log.exception("device_pixel_ratio: Win32 ratio failed; using Qt dpr")
    return qt_dpr if qt_dpr > 0 else 1.0


# ── mouse synthesis ──────────────────────────────────────────────────────────


def _vmetrics() -> tuple[int, int, int, int]:
    return (
        _user32.GetSystemMetrics(_SM_XVIRTUALSCREEN),
        _user32.GetSystemMetrics(_SM_YVIRTUALSCREEN),
        _user32.GetSystemMetrics(_SM_CXVIRTUALSCREEN),
        _user32.GetSystemMetrics(_SM_CYVIRTUALSCREEN),
    )


def _send(flags: int, x: float = 0.0, y: float = 0.0) -> None:
    if flags & _MOUSEEVENTF_ABSOLUTE:
        xv, yv, cx, cy = _vmetrics()
        nx = int((x - xv) * 65535 / max(cx - 1, 1))
        ny = int((y - yv) * 65535 / max(cy - 1, 1))
    else:
        nx, ny = int(x), int(y)
    mi = _MOUSEINPUT(nx, ny, 0, flags, 0, _INJECT_TAG)
    inp = _INPUT(_INPUT_MOUSE, _INPUTUNION(mi=mi))
    _user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT))


# ── drag timing (seconds) ────────────────────────────────────────────────────
# The pre-glide phase is load-bearing: Qt fires startDrag (→ the modal
# DoDragDrop loop) on the MAIN thread when it processes the synthesized press +
# nudge. If the glide runs before that loop is live, the button-up wins the race
# and the drop is cancelled. Rather than gambling on a fixed spin-up sleep
# (which loses whenever the GUI thread is slow — RDP session, detection pass,
# artwork decode), the worker now WAITS for ``startDrag`` to signal via
# :func:`notify_drag_started` before gliding (see ``_drag_started``); the
# remaining constants only pace the glide/settle. The glide must stay
# CONTINUOUS for the target to track; the settle gives DragOver time on the
# deck before release.
# Tuned via an under-load success-rate sweep (interference hook active): every
# config from ~1.6s down to ~0.16s held 14/14, so these values keep a safety
# margin rather than sitting on the edge.
_T_PRE_PRESS = 0.02  # after moving to src, before pressing
_T_PRESS_REG = 0.06  # after press, before the threshold nudge (Qt sees press)
_T_SPINUP = 0.08  # after startDrag signals, before glide (DoDragDrop spins up)
_T_START_TIMEOUT = 3.0  # max wait for startDrag to fire before aborting the drag
_GLIDE_STEPS = 10  # interpolated steps src→dst (must stay continuous)
_T_GLIDE_STEP = 0.010  # per glide step
_SETTLE_REPS = 2  # DragOver dwell ticks on the deck before the drop
_T_SETTLE = 0.04  # per settle tick
_T_DROP = 0.02  # after button-up, before restoring the cursor
# RDP sessions add input/message latency at every hop (injected event → queue →
# Qt → OLE DragOver round-trips), so pace the glide/settle more gently there.
_REMOTE_TIME_SCALE = 3.0

# Handshake between the GUI thread and the drag worker. ``ResultsTable.
# startDrag`` calls :func:`notify_drag_started` right before ``QDrag.exec``;
# ``do_drag`` waits on this instead of hoping a fixed sleep was long enough.
_drag_started = threading.Event()


def notify_drag_started() -> None:
    """Signal (from the GUI thread) that ``startDrag`` fired and ``QDrag.exec``
    is about to enter the OLE loop — releases :func:`do_drag`'s wait."""
    _drag_started.set()


# Whether the NEXT ``startDrag`` was initiated by :func:`do_drag` (synthetic
# mouse) rather than the operator's real mouse. The input-blocking hook must
# ONLY be installed for synthetic drags: during a MANUAL drag the operator's
# own mouse is the drag, and swallowing it leaves ``DoDragDrop`` modal with no
# way to move or release — the whole session's mouse is dead until the app
# dies (observed over RDP on 2026-07-16: exec stayed alive 129 s).
_synthetic_pending = False


def begin_synthetic_drag() -> None:
    """Mark that a synthetic drag is about to press the mouse — the upcoming
    ``startDrag`` may install the input-blocking hook. Set on the caller's
    thread right before the ``do_drag`` worker starts."""
    global _synthetic_pending
    _synthetic_pending = True


def consume_synthetic_drag() -> bool:
    """True (once) if the current ``startDrag`` came from a synthetic drag."""
    global _synthetic_pending
    was = _synthetic_pending
    _synthetic_pending = False
    return was


def do_drag(src: tuple[float, float], dst: tuple[float, float]) -> None:
    """Blocking drag worker — run in a daemon thread. Coords are PHYSICAL px.

    Windows OLE drag-and-drop (``DoDragDrop``, entered by Qt's ``QDrag.exec``)
    tracks the cursor through a modal message loop and needs CONTINUOUS movement
    for the target's ``IDropTarget`` to receive DragEnter/DragOver before the
    drop. So we press at *src*, nudge past the drag-distance threshold to fire
    ``startDrag``, glide to *dst* (kept continuous, just brisk), settle briefly
    on the deck, release, and restore the cursor to where it started. Timings
    are the module ``_T_*`` constants above.

    The cursor is kept visually silent by the transparent drag image +
    per-action cursors set in ``ResultsTable.startDrag``; the operator's real
    mouse is held off for the duration by the WH_MOUSE_LL hook.
    """
    origin = wintypes.POINT()
    _user32.GetCursorPos(ctypes.byref(origin))
    up = _MOUSEEVENTF_LEFTUP | _MOUSEEVENTF_ABSOLUTE | _MOUSEEVENTF_VIRTUALDESK
    button = {"down": False}
    try:
        _drag(src, dst, origin, button)
    finally:
        # Whatever went wrong in between, the button this pressed must not
        # stay pressed: it would hold the operator's mouse down for them.
        if button["down"]:
            _log.warning(
                "do_drag: ended with the button still down — releasing it and restoring the cursor"
            )
            here = wintypes.POINT()
            _user32.GetCursorPos(ctypes.byref(here))
            _send(up, float(here.x), float(here.y))
            _send(_MOVE_ABS, float(origin.x), float(origin.y))


def _drag(
    src: tuple[float, float], dst: tuple[float, float], origin, button: dict[str, bool]
) -> None:
    """The drag itself. *button* is kept up to date with whether the
    injected button is down, for :func:`do_drag` to clean up after."""
    sx, sy = src
    dx, dy = dst
    scale = _REMOTE_TIME_SCALE if is_remote_session() else 1.0

    down = _MOUSEEVENTF_LEFTDOWN | _MOUSEEVENTF_ABSOLUTE | _MOUSEEVENTF_VIRTUALDESK
    up = _MOUSEEVENTF_LEFTUP | _MOUSEEVENTF_ABSOLUTE | _MOUSEEVENTF_VIRTUALDESK

    _drag_started.clear()
    _send(_MOVE_ABS, sx, sy)
    time.sleep(_T_PRE_PRESS)
    _send(down, sx, sy)
    button["down"] = True
    time.sleep(_T_PRESS_REG)

    # Threshold nudge — cross startDragDistance so Qt's view fires startDrag.
    vx, vy = dx - sx, dy - sy
    dist = math.hypot(vx, vy) or 1.0
    ux, uy = vx / dist, vy / dist
    _send(_MOVE_ABS, sx + ux * 24, sy + uy * 24)

    # Wait for the GUI thread to actually fire startDrag (it may be busy —
    # RDP session, detection pass). Gliding before the OLE loop is live lets
    # the button-up win the race and the drop returns IgnoreAction.
    t0 = time.monotonic()
    started = _drag_started.wait(_T_START_TIMEOUT)
    waited_ms = (time.monotonic() - t0) * 1000
    if not started:
        _log.warning(
            "do_drag: startDrag did not fire within %.1fs — aborting "
            "(button released at src, cursor restored).",
            _T_START_TIMEOUT,
        )
        _send(up, sx + ux * 24, sy + uy * 24)
        button["down"] = False
        _send(_MOVE_ABS, float(origin.x), float(origin.y))
        return
    _log.debug("do_drag: startDrag handshake after %.0f ms", waited_ms)
    time.sleep(_T_SPINUP * scale)

    # Continuous glide src→dst so the OLE session tracks every step and
    # Traktor's drop target latches.
    for i in range(1, _GLIDE_STEPS + 1):
        t = i / _GLIDE_STEPS
        _send(_MOVE_ABS, sx + vx * t, sy + vy * t)
        time.sleep(_T_GLIDE_STEP * scale)

    # Settle on the deck so DragOver is processed, then drop. Remote sessions
    # get extra dwell ticks too — Traktor's drop target must have answered
    # DragOver before the release for the drop to count, and its UI thread is
    # noticeably slower under RDP rendering.
    for _ in range(int(_SETTLE_REPS * scale)):
        _send(_MOVE_ABS, dx, dy)
        time.sleep(_T_SETTLE * scale)
    pt = wintypes.POINT()
    _user32.GetCursorPos(ctypes.byref(pt))
    _log.debug(
        "do_drag: dropping at (%d, %d) over %r (target dst=(%.0f, %.0f))",
        pt.x,
        pt.y,
        exe_under_point(float(pt.x), float(pt.y)),
        dx,
        dy,
    )
    _send(up, dx, dy)
    button["down"] = False
    time.sleep(_T_DROP)
    _send(_MOVE_ABS, float(origin.x), float(origin.y))
