"""Wrapping card layout for the Now Playing tab and the stats histograms.

Split out of ``now_playing`` because it is a self-contained Qt layout that two
screens share (the matching-axis cards and the stats histogram grid), so it
should not drag the whole tab in with it. ``ui.now_playing`` re-exports
``_FlowLayout`` for the callers that still import it from there.
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtWidgets import QLayout

from ui.np_format import _flow_columns


class _FlowLayout(QLayout):
    """Left-to-right grid that wraps items onto new lines by available width.

    Used for the matching-axis cards so as many dimensions as fit sit on one
    line and the rest flow underneath, adapting to the window width. Items are
    stretched to share the full row width evenly: rows are balanced by item
    count and each card grows to fill its row edge-to-edge. ``max_item_width``
    (if > 0) caps the card width; 0 means no upper bound, so the row is always
    completely filled.
    """

    def __init__(
        self,
        parent=None,
        hspacing: int = 6,
        vspacing: int = 6,
        min_item_width: int = 120,
        max_item_width: int = 0,
        fill_height: bool = False,
    ):
        super().__init__(parent)
        self._items: list = []
        self._hspace = hspacing
        self._vspace = vspacing
        self._min_w = min_item_width
        self._max_w = max_item_width
        # When True, rows grow vertically to fill any height the layout is given
        # beyond its natural height — so the cards consume the slack left by a
        # taller neighbouring column instead of leaving a dead gap below them.
        self._fill_height = fill_height
        self.setContentsMargins(0, 0, 0, 0)

    def _column_layout(self, width: int) -> tuple[int, int]:
        """Return ``(columns, item_width)`` for the given available width."""
        m = self.contentsMargins()
        avail = width - m.left() - m.right()
        return _flow_columns(len(self._items), avail, self._hspace, self._min_w, self._max_w)

    def addItem(self, item):
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, i):
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientation.Vertical if self._fill_height else Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._do_layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect) -> None:
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self) -> QSize:
        return self.minimumSize()

    def minimumSize(self) -> QSize:
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        return size + QSize(m.left() + m.right(), m.top() + m.bottom())

    def _do_layout(self, rect, test_only: bool) -> int:
        m = self.contentsMargins()
        n = len(self._items)
        natural = m.top() + m.bottom()
        if n == 0:
            return natural
        cols, item_w = self._column_layout(rect.width())
        nrows = (n + cols - 1) // cols
        # Natural height of each row = the tallest card's preferred height.
        row_h = [
            max(
                self._items[r * cols + c].sizeHint().height()
                for c in range(cols)
                if r * cols + c < n
            )
            for r in range(nrows)
        ]
        natural += sum(row_h) + self._vspace * (nrows - 1)
        if test_only:
            return natural
        # Spread any extra height evenly across the rows so the cards fill the
        # space a taller neighbouring column would otherwise leave empty.
        extra = max(0, rect.height() - natural) if self._fill_height else 0
        add, rem = divmod(extra, nrows)
        left = rect.x() + m.left()
        y = rect.y() + m.top()
        idx = 0
        for r in range(nrows):
            h = row_h[r] + add + (1 if r < rem else 0)
            x = left
            for _c in range(cols):
                if idx >= n:
                    break
                self._items[idx].setGeometry(QRect(QPoint(x, y), QSize(item_w, h)))
                idx += 1
                x += item_w + self._hspace
            y += h + self._vspace
        return y - self._vspace - rect.y() + m.bottom()
