"""macOS app-unhide observer.

Qt is blind to Cmd+H: hiding/unhiding the application produces no
spontaneous Hide/Show events on the window and no ``ApplicationHidden``
application state (verified by probing — only a generic deactivate fires,
and nothing at all on unhide). AppKit's
``NSApplicationDidUnhideNotification`` is the only reliable signal, so
observe it directly.

This module is imported only on macOS (guarded by the caller), like the
platform backends in ``core/drag*.py``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

import objc
from AppKit import NSApplication
from Foundation import NSNotificationCenter, NSObject

_log = logging.getLogger(__name__)


class _UnhideObserver(NSObject):
    """NSNotificationCenter target holding the Python callback."""

    def initWithCallback_(self, callback):
        self = objc.super(_UnhideObserver, self).init()
        if self is None:
            return None
        self._callback = callback
        return self

    def appDidUnhide_(self, _note) -> None:
        try:
            self._callback()
        except Exception:
            _log.exception("unhide callback failed")


def install_unhide_observer(callback: Callable[[], None]) -> object | None:
    """Invoke *callback* whenever this app is unhidden (Cmd+H → Dock click).

    Returns the observer object — the caller MUST keep a reference to it
    for the lifetime of the app, or it is collected and the notification
    silently stops arriving. Returns None if installation failed.
    """
    try:
        obs = _UnhideObserver.alloc().initWithCallback_(callback)
        NSNotificationCenter.defaultCenter().addObserver_selector_name_object_(
            obs,
            objc.selector(obs.appDidUnhide_, signature=b"v@:@"),
            "NSApplicationDidUnhideNotification",
            NSApplication.sharedApplication(),
        )
        return obs
    except Exception:
        _log.debug("could not install unhide observer", exc_info=True)
        return None
