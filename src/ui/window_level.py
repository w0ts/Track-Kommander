"""Keep a window above other apps' windows.

* macOS: Qt's ``WindowStaysOnTopHint``, set on the widget's ``QWindow``.
  Setting it there changes the level in place — no hide, no flicker — which
  ``QWidget.setWindowFlags`` does not. And it must be Qt's own flag: an
  NSWindow level set behind Qt's back was reset by Qt (after the window was
  hidden and shown again, and around the Preferences / Library dialogs,
  which Qt keeps at the level it believes the parent has — they opened
  underneath the floating window). With Qt's flag the dialogs follow.
* Windows: ``HWND_TOPMOST`` vs ``HWND_NOTOPMOST`` through ``SetWindowPos``.

Anything else is a no-op that reports False.
"""

from __future__ import annotations

import logging
import sys

log = logging.getLogger(__name__)


def set_always_on_top(widget, on: bool) -> bool:
    """Put *widget*'s window above (or back among) other windows."""
    try:
        if sys.platform == "darwin":
            return _mac(widget, on)
        if sys.platform == "win32":
            from core import drag_win

            return drag_win.set_topmost(int(widget.winId()), on)
    except Exception:
        log.warning("could not set always-on-top=%s", on, exc_info=True)
    return False


def is_always_on_top(widget) -> bool | None:
    """Whether *widget*'s window really is above others; None if unknown.

    The flag the app keeps is not enough: restoring the window from the
    minimised button left it at the normal level while the flag said
    "on top" (seen live: layer 0), so it went under Traktor.
    """
    try:
        if sys.platform == "darwin":
            import objc
            from AppKit import NSFloatingWindowLevel

            window = objc.objc_object(c_void_p=int(widget.winId())).window()
            return None if window is None else window.level() >= NSFloatingWindowLevel
        if sys.platform == "win32":
            import ctypes

            gwl_exstyle, ws_ex_topmost = -20, 0x00000008
            style = ctypes.windll.user32.GetWindowLongW(int(widget.winId()), gwl_exstyle)
            return bool(style & ws_ex_topmost)
    except Exception:
        log.debug("could not read the window level", exc_info=True)
    return None


def _mac(widget, on: bool) -> bool:
    from PySide6.QtCore import Qt

    handle = widget.windowHandle()
    if handle is None:
        return False
    handle.setFlag(Qt.WindowType.WindowStaysOnTopHint, on)
    return True
