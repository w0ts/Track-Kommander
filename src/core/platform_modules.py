"""Platform-only modules, reached through ``Any`` handles.

pyobjc builds its symbols at import time (``Quartz.CGEventPost`` is not in
any source file a checker can read), and ``ctypes`` only has ``windll`` and
``WinDLL`` on Windows. Code that talks to them is written for one platform
and runs only there, so a checker on the other platform cannot tell a real
symbol from a typo. Going through :func:`importlib.import_module` says so
plainly instead of leaving hundreds of unresolvable names in the type check.

Each call imports on demand, so nothing loads on a platform that does not
use it, and a module a test has placed in ``sys.modules`` is the one found.
"""

from __future__ import annotations

import ctypes
import importlib
from typing import Any


def appkit() -> Any:
    """macOS: the ``AppKit`` module."""
    return importlib.import_module("AppKit")


def quartz() -> Any:
    """macOS: the ``Quartz`` module."""
    return importlib.import_module("Quartz")


def core_foundation() -> Any:
    """macOS: the ``CoreFoundation`` module."""
    return importlib.import_module("CoreFoundation")


def application_services() -> Any:
    """macOS: the ``ApplicationServices`` module."""
    return importlib.import_module("ApplicationServices")


def win_ctypes() -> Any:
    """``ctypes`` seen as it is on Windows: with ``windll``, ``WinDLL``,
    ``WINFUNCTYPE`` and ``get_last_error``.

    It is the real module, so attributes a test sets on ``ctypes`` are the
    ones read here.
    """
    return ctypes
