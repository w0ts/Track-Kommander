"""Layout-independent physical key codes.

Transport shortcuts are meaningful by their *physical position* on the
keyboard, not by the character they produce. On an AZERTY layout the key in the
QWERTY "W" position types ``Z``, and the unshifted number row types symbols
(``&é"'(-è_``) — so binding by Qt's layout-translated key code would scatter
the shortcuts onto the wrong physical keys.

The physical code comes from the platform's native key code, which is tied to
the hardware position and identical across QWERTY, AZERTY, QWERTZ and Dvorak.
No per-layout detection or configuration is required.

- macOS: ``nativeVirtualKey()`` returns the ``kVK_ANSI_*`` virtual key, which
  is constant per physical position regardless of the active input source.
- Windows: ``nativeScanCode()`` returns the set-1 hardware scan code, also
  position-based (``nativeVirtualKey`` on Windows is layout-translated, so we
  avoid it).
- Other platforms (e.g. Linux): no physical codes; bindings fall back to Qt's
  logical key, which is layout-dependent but better than nothing.

This module is the platform layer only. Which action a key is bound to lives
in :mod:`core.actions`, which needs these codes to match a key event — so
this lives in ``core``, not ``ui``: it touches only the event's accessors and
no widget. :mod:`ui.physical_keys` re-exports it.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover — typing only
    from PySide6.QtGui import QKeyEvent

# Symbolic name → native code for this platform. Since transport moved to
# Traktor (keys are forwarded, not re-implemented), no default binding uses
# this table any more: it survives as the platform-support probe
# (:func:`supports_physical`) and as the label table for a captured key, so
# "the key in the QWERTY W position" is shown as ``W`` rather than as a
# number, whatever the active layout types.
if sys.platform == "darwin":
    # kVK_ANSI_* virtual key codes (Carbon HIToolbox Events.h).
    DEFAULT_CODES: dict[str, int] = {
        "W": 13,
        "S": 1,
        "1": 18,
        "2": 19,
        "3": 20,
        "4": 21,
        "5": 23,
        "6": 22,
        "7": 26,
        "8": 28,
    }

    def native_code(event: QKeyEvent) -> int:
        """Position-based code for a key event, or 0 if unavailable."""
        return int(event.nativeVirtualKey())

elif sys.platform.startswith("win"):
    # Set-1 keyboard scan codes.
    DEFAULT_CODES = {
        "W": 0x11,
        "S": 0x1F,
        "1": 0x02,
        "2": 0x03,
        "3": 0x04,
        "4": 0x05,
        "5": 0x06,
        "6": 0x07,
        "7": 0x08,
        "8": 0x09,
    }

    def native_code(event: QKeyEvent) -> int:
        return int(event.nativeScanCode())

else:
    DEFAULT_CODES = {}

    def native_code(event: QKeyEvent) -> int:
        return 0


def supports_physical() -> bool:
    """True where position-based binding works."""
    return bool(DEFAULT_CODES)


#: Reverse lookup, for showing a captured physical key back to the user.
CODE_NAMES: dict[int, str] = {code: name for name, code in DEFAULT_CODES.items()}
