"""The patched Traktor's own process symbols (libtk's ``tk_*`` exports), for ctypes.

Only inside Traktor (:func:`core.runtime.in_traktor`): standalone TK never looks, so a desktop
build never depends on ``ctypes.CDLL(None)`` (which raises ``TypeError`` on Windows).

On macOS libtk.dylib is part of the process image, so ``CDLL(None)`` sees its exports. On
Windows ``tk_launch.exe`` loads ``libtk.dll`` into Traktor; it is opened here by its module
handle, never loaded a second time from a path.
"""

from __future__ import annotations

import ctypes
import logging
import sys

from core import platform_modules
from core.runtime import in_traktor

log = logging.getLogger(__name__)

_WINDOWS_LIB = "libtk.dll"


def _loaded_module_handle(name: str) -> int:
    """Handle of the already loaded module *name* in this process (Windows), 0 when absent."""
    kernel32 = platform_modules.win_ctypes().WinDLL("kernel32", use_last_error=True)
    get = kernel32.GetModuleHandleW
    get.argtypes = [ctypes.c_wchar_p]
    get.restype = ctypes.c_void_p
    return get(name) or 0


def process_lib() -> ctypes.CDLL | None:
    """This process's symbols when TK runs inside Traktor, else None."""
    if not in_traktor():
        return None
    try:
        if sys.platform == "win32":
            handle = _loaded_module_handle(_WINDOWS_LIB)
            if not handle:
                log.warning("Inside Traktor, but %s is not loaded in this process", _WINDOWS_LIB)
                return None
            return ctypes.CDLL(_WINDOWS_LIB, handle=handle)
        return ctypes.CDLL(None)
    except (OSError, TypeError) as exc:
        log.warning("Inside Traktor, but its symbols are unavailable: %s", exc)
        return None


def process_symbol(name: str):
    """libtk's export *name* (a ctypes function), or None outside Traktor / when missing."""
    lib = process_lib()
    return getattr(lib, name, None) if lib is not None else None
