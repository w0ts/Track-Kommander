"""Traktor's own settings, read from its memory (libtk ``tk_setting_get``) inside Traktor.

TK never reads ``Traktor Settings.tsi`` there (User, 2026-11-24). Traktor's main thread only.
"""

from __future__ import annotations

import ctypes
import logging

from workers.native_lib import process_symbol

log = logging.getLogger(__name__)

_CAP = 1 << 16  # a setting's text; the music folders list is the longest one TK reads


def setting(name: str) -> str | None:
    """The value of Traktor's setting *name* as Traktor stores it (a string list is one
    ";"-joined string), or None outside Traktor, before Traktor's settings were seen, or for a
    setting that is not text."""
    fn = process_symbol("tk_setting_get")
    if fn is None:
        return None
    fn.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int]
    fn.restype = ctypes.c_int
    buf = ctypes.create_string_buffer(_CAP)
    n = int(fn(name.encode(), buf, _CAP))
    if n < 0:
        log.warning("Traktor's setting %s is not readable from its memory (%d)", name, n)
        return None
    return buf.value.decode("utf-8", errors="replace")
