"""What happens to an error nobody caught.

Python prints it to stderr and carries on. In the built app there is no
stderr to read, so an exception in a Qt slot or on a worker thread used to
leave no trace at all: the thing the user asked for did not happen, the log
said nothing, and the bug report that followed had no traceback in it.

:func:`install_exception_hooks` sends every one to the log, with its
traceback, and tells the user once that something went wrong. It covers the
three places they come from — the main thread, any other thread, and Qt
slots, which PySide6 reports through ``sys.excepthook``.
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from collections.abc import Callable
from types import TracebackType

log = logging.getLogger("unhandled")

#: How long before the user is told again. Everything is logged; a slot on a
#: timer can fail many times a second, and one message says it.
TELL_AGAIN_AFTER_S = 30.0

_ExcInfo = tuple[type[BaseException], BaseException, TracebackType | None]


def _now() -> float:
    return time.monotonic()


def install_exception_hooks(tell: Callable[[str], None]) -> None:
    """Log every uncaught exception and pass a short message to *tell*.

    *tell* may be called from any thread, so it has to be safe to — a Qt
    signal is. It is never allowed to fail the hook: an error while
    reporting an error would hide the first one.
    """
    last_told = {"at": float("-inf")}
    told_lock = threading.Lock()

    def report(exc_info: _ExcInfo, where: str) -> None:
        kind, error, _tb = exc_info
        if issubclass(kind, KeyboardInterrupt):
            return
        log.error("uncaught %s %s: %s", kind.__name__, where, error, exc_info=exc_info)
        with told_lock:
            if _now() - last_told["at"] < TELL_AGAIN_AFTER_S:
                return
            last_told["at"] = _now()
        try:
            tell(
                f"Something went wrong ({kind.__name__}: {error}). "
                "The details are in the log — Settings ▸ Save log bundle."
            )
        except Exception:  # reporting must not hide what is being reported
            log.exception("could not show the error to the user")

    def on_main(kind, error, tb) -> None:
        report((kind, error, tb), "on the main thread")
        sys.__excepthook__(kind, error, tb)

    def on_thread(args: threading.ExceptHookArgs) -> None:
        if args.exc_value is None:
            return
        name = args.thread.name if args.thread is not None else "?"
        report((args.exc_type, args.exc_value, args.exc_traceback), f"on thread {name!r}")

    sys.excepthook = on_main
    threading.excepthook = on_thread


def open_library(init: Callable[[], None], tell: Callable[[str, str], None]) -> bool:
    """Run *init* — opening the library and bringing its schema up to date.

    If it fails, the reason is logged and handed to *tell* as (title, text),
    and this returns False: the application cannot run without its library,
    but it can say why before it goes.
    """
    try:
        init()
    except Exception as error:  # whatever it was: explained, never swallowed
        log.exception("the library could not be opened")
        tell(
            "Track Kommander cannot open its library",
            f"{error}\n\nNothing was changed. If another copy of Track "
            "Kommander is running, close it and start again; otherwise the "
            "log has the details.",
        )
        return False
    return True
