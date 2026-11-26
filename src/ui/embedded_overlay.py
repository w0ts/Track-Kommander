"""The matching-cards strip for the embedded app, pinned over Traktor's list.

PRIVATE / Mac-only / branch ``feat/traktor-native-re``. Part of the embedded UI
(``traktor-native/notes/embedded-ui-plan.md``): the cards stay in PySide (cheap,
and TK already tracks Traktor's browser geometry), while the result LIST is
Traktor's own, with TK columns injected by the dylib.

The geometry — where the strip sits relative to Traktor's window — is a pure
function so it is unit-tested without a live Traktor; the live snap (capturing
Traktor's bounds + browser line) is a thin wrapper tuned visually on screen.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

log = logging.getLogger(__name__)

Rect = tuple[float, float, float, float]  # (x, y, w, h) in Qt logical pixels

#: Keep at least this many pixels of Traktor's list visible below the strip.
_MIN_LIST_VISIBLE_PX = 80.0


def cards_strip_rect(
    bounds: Rect,
    browser_top_frac: float,
    strip_h: float,
    *,
    min_list_visible: float = _MIN_LIST_VISIBLE_PX,
) -> Rect:
    """Geometry ``(x, y, w, h)`` for a cards strip pinned at the top of Traktor's
    list, spanning its full width.

    *bounds* is Traktor's window ``(x, y, w, h)`` in logical pixels;
    *browser_top_frac* is where the list starts as a fraction of window height
    (from :func:`core.drag.browser_top_fraction`); *strip_h* is the desired
    strip height, clamped so at least *min_list_visible* pixels of the list stay
    visible below it.
    """
    x, y, w, h = bounds
    frac = min(max(browser_top_frac, 0.0), 1.0)
    list_top = y + frac * h
    list_bottom = y + h
    avail = max(0.0, list_bottom - list_top - min_list_visible)
    sh = max(0.0, min(strip_h, avail))
    return (x, list_top, w, sh)


class CardsOverlay(QWidget):
    """A frameless, always-on-top strip that will hold the matching cards.

    First increment: a placeholder band that can pin itself over Traktor's list
    via :meth:`snap`. The real axis cards (reusing ``np_axis_cards``) and the
    live layout-watch re-snap come next.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        self._placeholder = QLabel("matching cards")
        self._placeholder.setStyleSheet("color: #888;")
        layout.addWidget(self._placeholder)

    def snap(self, strip_h: float = 96.0) -> bool:
        """Pin the strip over the top of Traktor's list. Returns True if snapped.

        Reuses TK's proven Traktor-geometry probes; the exact band is tuned
        visually on screen (hence no headless assertion on the final pixels).
        """
        from core.drag import _traktor_logical_bounds, browser_top_fraction

        bounds = _traktor_logical_bounds()
        frac = browser_top_fraction()
        if not bounds or frac is None:
            log.info("cards overlay: Traktor not capturable yet — not snapping")
            return False
        x, y, w, h = cards_strip_rect(bounds, frac, strip_h)
        if w <= 0 or h <= 0:
            return False
        self.setGeometry(int(x), int(y), int(w), int(h))
        self.raise_()
        return True
