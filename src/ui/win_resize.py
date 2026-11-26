"""Edge-resize support for the frameless main window on Windows.

``Qt.FramelessWindowHint`` maps to a WS_POPUP window without WS_THICKFRAME,
so Windows offers no native resize borders (macOS keeps the resizable style
mask on frameless windows, hence the platform asymmetry). Two pieces are
BOTH required:

* answer WM_NCHITTEST with HTLEFT/HTTOPRIGHT/… when the cursor sits in a
  thin band along the window edge (``hit_test_edges``), and
* put WS_THICKFRAME back into GWL_STYLE (``enable_sizing_frame``) —
  DefWindowProc silently ignores the SC_SIZE command triggered by an edge
  hit code when the style bit is absent (measured: WM_NCLBUTTONDOWN
  arrives but WM_ENTERSIZEMOVE never fires and the size never changes).

With both in place DefWindowProc does the rest — resize cursors via
WM_SETCURSOR, the modal sizing loop on click, and Aero snap — while Qt's
WM_GETMINMAXINFO handling keeps the layout's minimum size respected.
Adding WS_THICKFRAME does NOT bring back a visible frame: Qt answers
WM_NCCALCSIZE for frameless windows so the client area still covers the
whole window rect (verified: client size and corner pixels are identical
with and without the bit).

Coordinate spaces: WM_NCHITTEST's lParam and ``GetWindowRect`` are both
PHYSICAL pixels, so the hit band is scaled by the window's DPI to stay
``RESIZE_BAND_LOGICAL_PX`` logical pixels wide (same convention as
``core/drag_win.py``).

``hit_code`` is pure geometry so it is unit-testable on any platform; only
``hit_test_edges`` touches Win32 and is called on Windows only.
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

WM_NCHITTEST = 0x0084

HTLEFT = 10
HTRIGHT = 11
HTTOP = 12
HTTOPLEFT = 13
HTTOPRIGHT = 14
HTBOTTOM = 15
HTBOTTOMLEFT = 16
HTBOTTOMRIGHT = 17

RESIZE_BAND_LOGICAL_PX = 6
_BASE_DPI = 96.0

_GWL_STYLE = -16
_WS_THICKFRAME = 0x00040000
# SWP_NOSIZE | SWP_NOMOVE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED
_SWP_FRAME_ONLY = 0x0001 | 0x0002 | 0x0004 | 0x0010 | 0x0020

if sys.platform == "win32":
    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    _user32.GetWindowRect.restype = wintypes.BOOL
    _user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
    _user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    _user32.SetWindowLongPtrW.argtypes = [
        wintypes.HWND,
        ctypes.c_int,
        ctypes.c_ssize_t,
    ]
    _user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
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
    try:
        _user32.GetDpiForWindow.argtypes = [wintypes.HWND]
        _user32.GetDpiForWindow.restype = wintypes.UINT
        _get_dpi_for_window = _user32.GetDpiForWindow
    except AttributeError:  # pre-Windows 10 1607
        _get_dpi_for_window = None


def hit_code(x: int, y: int, rect: tuple[int, int, int, int], band: int) -> int | None:
    """HT* code for screen point (x, y) against window ``rect``, or None.

    ``rect`` is (left, top, right, bottom); all values share one coordinate
    space (physical pixels in production, but the math is unit-agnostic).
    Returns None when the point is outside the window or in its interior,
    letting the default hit-testing (client area, drag-to-move) apply.
    """
    left, top, right, bottom = rect
    if not (left <= x < right and top <= y < bottom):
        return None

    on_left = x < left + band
    on_right = x >= right - band
    on_top = y < top + band
    on_bottom = y >= bottom - band

    if on_top and on_left:
        return HTTOPLEFT
    if on_top and on_right:
        return HTTOPRIGHT
    if on_bottom and on_left:
        return HTBOTTOMLEFT
    if on_bottom and on_right:
        return HTBOTTOMRIGHT
    if on_left:
        return HTLEFT
    if on_right:
        return HTRIGHT
    if on_top:
        return HTTOP
    if on_bottom:
        return HTBOTTOM
    return None


def enable_sizing_frame(hwnd: int) -> None:
    """OR WS_THICKFRAME into the window style so DefWindowProc will resize.

    Windows-only. Idempotent — call it from showEvent so the bit survives a
    native-window recreation (Qt rebuilds the HWND on setWindowFlags changes).
    SWP_FRAMECHANGED makes the style take effect without moving, resizing,
    reordering or activating the window.
    """
    style = _user32.GetWindowLongPtrW(hwnd, _GWL_STYLE)
    if style & _WS_THICKFRAME:
        return
    _user32.SetWindowLongPtrW(hwnd, _GWL_STYLE, style | _WS_THICKFRAME)
    _user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, _SWP_FRAME_ONLY)


def hit_test_edges(message_addr: int) -> int | None:
    """Parse a native MSG and return an HT* edge code, or None to pass through.

    Windows-only. ``message_addr`` is ``int(message)`` from
    ``QWidget.nativeEvent`` — the address of a ``MSG`` struct.
    """
    msg = wintypes.MSG.from_address(message_addr)
    if msg.message != WM_NCHITTEST:
        return None

    # lParam packs signed 16-bit screen coords (negative on multi-monitor).
    x = ctypes.c_short(msg.lParam & 0xFFFF).value
    y = ctypes.c_short((msg.lParam >> 16) & 0xFFFF).value

    rect = wintypes.RECT()
    if not _user32.GetWindowRect(msg.hWnd, ctypes.byref(rect)):
        return None

    scale = _get_dpi_for_window(msg.hWnd) / _BASE_DPI if _get_dpi_for_window else 1.0
    band = max(1, round(RESIZE_BAND_LOGICAL_PX * scale))
    return hit_code(x, y, (rect.left, rect.top, rect.right, rect.bottom), band)
