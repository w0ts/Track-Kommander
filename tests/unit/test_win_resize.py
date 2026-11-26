"""Unit tests for the Windows frameless edge-resize hit testing.

Covers ``hit_code``: the pure geometry that maps a screen point to a Win32
HT* resize code (or None for interior/outside points). The Win32 plumbing in
``hit_test_edges`` is Windows-only and exercised on a Windows PC; the
geometry is platform-neutral, so it's tested directly here.

``enable_sizing_frame`` gets a Windows-only test against a real Win32 window:
the WS_THICKFRAME style bit is what lets DefWindowProc run the sizing loop at
all, and it silently regressing is exactly how edge resize broke twice before.
"""

from __future__ import annotations

import sys

import pytest

from ui.win_resize import (
    HTBOTTOM,
    HTBOTTOMLEFT,
    HTBOTTOMRIGHT,
    HTLEFT,
    HTRIGHT,
    HTTOP,
    HTTOPLEFT,
    HTTOPRIGHT,
    hit_code,
)

# Window at (100, 200) sized 800×600, 6 px hit band.
RECT = (100, 200, 900, 800)
BAND = 6


@pytest.mark.parametrize(
    ("x", "y", "expected"),
    [
        # Corners (band × band squares).
        (100, 200, HTTOPLEFT),
        (105, 205, HTTOPLEFT),
        (899, 200, HTTOPRIGHT),
        (894, 205, HTTOPRIGHT),
        (100, 799, HTBOTTOMLEFT),
        (899, 799, HTBOTTOMRIGHT),
        # Edges (past the corner squares).
        (100, 500, HTLEFT),
        (105, 500, HTLEFT),
        (899, 500, HTRIGHT),
        (894, 500, HTRIGHT),
        (500, 200, HTTOP),
        (500, 205, HTTOP),
        (500, 799, HTBOTTOM),
        (500, 794, HTBOTTOM),
    ],
)
def test_edge_and_corner_points(x: int, y: int, expected: int) -> None:
    assert hit_code(x, y, RECT, BAND) == expected


@pytest.mark.parametrize(
    ("x", "y"),
    [
        (500, 500),  # dead centre
        (106, 500),  # one px inside the left band
        (893, 500),  # one px inside the right band
        (500, 206),  # one px below the top band
        (500, 793),  # one px above the bottom band
    ],
)
def test_interior_points_return_none(x: int, y: int) -> None:
    assert hit_code(x, y, RECT, BAND) is None


@pytest.mark.parametrize(
    ("x", "y"),
    [
        (99, 500),  # left of the window
        (900, 500),  # right edge is exclusive
        (500, 199),  # above
        (500, 800),  # bottom edge is exclusive
    ],
)
def test_points_outside_rect_return_none(x: int, y: int) -> None:
    assert hit_code(x, y, RECT, BAND) is None


def test_negative_coordinates_on_secondary_monitor() -> None:
    """Screen coords can be negative when a monitor sits left of the primary."""
    rect = (-1920, 0, -920, 600)
    assert hit_code(-1920, 300, rect, BAND) == HTLEFT
    assert hit_code(-921, 300, rect, BAND) == HTRIGHT
    assert hit_code(-1420, 0, rect, BAND) == HTTOP
    assert hit_code(-1420, 300, rect, BAND) is None


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 API")
def test_enable_sizing_frame_sets_thickframe() -> None:
    """WS_THICKFRAME must end up in GWL_STYLE — without it DefWindowProc
    ignores SC_SIZE and edge resize silently does nothing (hit test and all).
    Uses a bare popup window so no Qt event loop is needed."""
    import ctypes
    from ctypes import wintypes

    from ui.win_resize import (
        _GWL_STYLE,
        _WS_THICKFRAME,
        enable_sizing_frame,
    )

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    WS_POPUP = 0x80000000
    hwnd = user32.CreateWindowExW(
        0, "STATIC", "test", WS_POPUP, 0, 0, 50, 50, None, None, None, None
    )
    assert hwnd, "CreateWindowExW failed"
    try:
        user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
        user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
        assert not (user32.GetWindowLongPtrW(hwnd, _GWL_STYLE) & _WS_THICKFRAME)
        enable_sizing_frame(hwnd)
        assert user32.GetWindowLongPtrW(hwnd, _GWL_STYLE) & _WS_THICKFRAME
        enable_sizing_frame(hwnd)  # idempotent
        assert user32.GetWindowLongPtrW(hwnd, _GWL_STYLE) & _WS_THICKFRAME
    finally:
        user32.DestroyWindow(hwnd)
