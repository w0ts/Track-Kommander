"""Hear, the moment it happens, that the active app changed.

Docked mode decides "always on top?" from which app is in front. A 300 ms
check does that on its own, but it leaves a visible gap: click Traktor while
an editor is in front and Traktor covers this window until the next check.
These events make the same decision at once. The periodic check stays, as
the safety net for anything with no event (a window minimised, a missed
notice) — so a missed event costs nothing but those 300 ms.

* macOS: NSWorkspace notifications — an app activated, hidden, unhidden,
  launched or quit.
* Windows: a ``SetWinEventHook`` for foreground changes and minimise /
  restore, out-of-context, delivered through the Qt thread's message loop.

Elsewhere :meth:`AppSwitchWatch.start` does nothing.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Callable

log = logging.getLogger(__name__)

#: NSWorkspace notifications that can change what "Traktor in front" means.
_MAC_NOTIFICATIONS = (
    "NSWorkspaceDidActivateApplicationNotification",
    "NSWorkspaceDidHideApplicationNotification",
    "NSWorkspaceDidUnhideApplicationNotification",
    "NSWorkspaceDidLaunchApplicationNotification",
    "NSWorkspaceDidTerminateApplicationNotification",
)


class AppSwitchWatch:
    """Calls *on_change* whenever the active app may have changed."""

    def __init__(self, on_change: Callable[[], None]) -> None:
        self._on_change = on_change
        self._handle: object | None = None

    @property
    def active(self) -> bool:
        return self._handle is not None

    def start(self) -> None:
        if self._handle is not None:
            return
        try:
            if sys.platform == "darwin":
                self._handle = _MacWatch(self._fire)
            elif sys.platform == "win32":
                self._handle = _WinWatch(self._fire)
        except Exception:
            log.warning(
                "app-switch events unavailable; the periodic check carries on alone", exc_info=True
            )
            self._handle = None

    def stop(self) -> None:
        if self._handle is None:
            return
        try:
            self._handle.close()
        except Exception:
            log.debug("stopping the app-switch watch failed", exc_info=True)
        self._handle = None

    def _fire(self) -> None:
        # From an OS callback: an exception must not escape into it.
        try:
            self._on_change()
        except Exception:
            log.exception("reacting to an app switch failed")


# ── macOS ───────────────────────────────────────────────────────────────────

_OBSERVER_CLASS = None


def _mac_observer_class():
    """Defined on first use: an Objective-C class can be registered once."""
    global _OBSERVER_CLASS
    if _OBSERVER_CLASS is not None:
        return _OBSERVER_CLASS
    import objc
    from Foundation import NSObject

    class _AppSwitchObserver(NSObject):
        def initWithCallback_(self, callback):
            self = objc.super(_AppSwitchObserver, self).init()
            if self is None:
                return None
            self._callback = callback
            return self

        def appChanged_(self, _note) -> None:
            self._callback()

    _OBSERVER_CLASS = _AppSwitchObserver
    return _OBSERVER_CLASS


class _MacWatch:
    def __init__(self, fire: Callable[[], None]) -> None:
        import objc
        from AppKit import NSWorkspace

        self._center = NSWorkspace.sharedWorkspace().notificationCenter()
        self._observer = _mac_observer_class().alloc().initWithCallback_(fire)
        selector = objc.selector(self._observer.appChanged_, signature=b"v@:@")
        for name in _MAC_NOTIFICATIONS:
            self._center.addObserver_selector_name_object_(self._observer, selector, name, None)

    def close(self) -> None:
        self._center.removeObserver_(self._observer)


# ── Windows ─────────────────────────────────────────────────────────────────

_EVENT_SYSTEM_FOREGROUND = 0x0003
_EVENT_SYSTEM_MINIMIZESTART = 0x0016
_EVENT_SYSTEM_MINIMIZEEND = 0x0017
_WINEVENT_OUTOFCONTEXT = 0x0000


class _WinWatch:
    def __init__(self, fire: Callable[[], None]) -> None:
        import ctypes
        from ctypes import wintypes

        proc_type = ctypes.WINFUNCTYPE(
            None,
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.HWND,
            wintypes.LONG,
            wintypes.LONG,
            wintypes.DWORD,
            wintypes.DWORD,
        )

        def _on_event(_hook, _event, _hwnd, _obj, _child, _thread, _time):
            fire()

        # Held on self: a collected callback crashes the process on the next
        # event.
        self._proc = proc_type(_on_event)
        self._user32 = ctypes.windll.user32
        self._user32.SetWinEventHook.restype = wintypes.HANDLE
        self._hooks = [
            self._user32.SetWinEventHook(low, high, None, self._proc, 0, 0, _WINEVENT_OUTOFCONTEXT)
            for low, high in (
                (_EVENT_SYSTEM_FOREGROUND, _EVENT_SYSTEM_FOREGROUND),
                (_EVENT_SYSTEM_MINIMIZESTART, _EVENT_SYSTEM_MINIMIZEEND),
            )
        ]
        if not all(self._hooks):
            self.close()
            raise OSError("SetWinEventHook failed")

    def close(self) -> None:
        for hook in self._hooks:
            if hook:
                self._user32.UnhookWinEvent(hook)
