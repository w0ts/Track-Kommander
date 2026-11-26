"""Cursor shapes that are safe on every platform.

On macOS Qt draws a few cursor shapes from its own images instead of using a
system cursor, and on macOS 27.2 turning those images into a CGImage aborts
the whole app (EXC_BREAKPOINT in ``QImage::toCGImage``, as the mouse enters
the widget). Measured 2026-11-23 with Qt 6.11: ``SizeAllCursor`` and
``WaitCursor`` crash; ``OpenHandCursor`` and ``BusyCursor`` do not. Use these
names instead of those two shapes (``tests/unit/test_cursors.py`` checks).
"""

from __future__ import annotations

import sys

from PySide6.QtCore import Qt

# Something the user drags around (the cover handle moves the window).
MOVE_CURSOR = Qt.CursorShape.OpenHandCursor

# Work in progress that blocks the UI.
WAIT_CURSOR = Qt.CursorShape.BusyCursor if sys.platform == "darwin" else Qt.CursorShape.WaitCursor
