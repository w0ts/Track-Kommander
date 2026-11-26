"""Layout-independent physical key codes — moved to :mod:`core.physical_keys`.

``core.actions`` matches key events by these codes, and ``core`` may not
import from ``ui``, so the module lives below both. This re-export keeps the
old import path working for the widgets that use it.
"""

from __future__ import annotations

from core.physical_keys import (
    CODE_NAMES,
    DEFAULT_CODES,
    native_code,
    supports_physical,
)

__all__ = ["CODE_NAMES", "DEFAULT_CODES", "native_code", "supports_physical"]
