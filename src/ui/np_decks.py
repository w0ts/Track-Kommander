"""Deck buttons and the deck grid of the Now Playing tab.

The four deck buttons carry a playhead bar and a hover tooltip; the grid
places A|B over C|D so the split lands on the host window's centre line, the
way Traktor's own deck split does. They are plain widgets with no knowledge of
the tab's state, so they live apart from it. ``ui.now_playing`` re-exports
them for the callers that still import them from there.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QPushButton, QSizePolicy, QToolTip, QWidget

from ui.np_tooltip import _tooltip_duration_ms


class DeckButton(QPushButton):
    """QPushButton with a playhead progress bar painted at its bottom edge."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._progress: float = 0.0
        # Callable returning the (rich-text) tooltip for this deck, evaluated
        # lazily on hover so it always reflects the current track + matches.
        self._tip_provider = None

    def event(self, e):
        if e.type() == QEvent.Type.ToolTip and self._tip_provider is not None:
            # Same rich-text tooltip as a result row, so it needs the same
            # enforced lifetime — Qt's own would outlast the set.
            text = self._tip_provider() or ""
            ms = _tooltip_duration_ms()
            if text and ms > 0:
                QToolTip.showText(e.globalPos(), text, self, self.rect(), ms)
            else:
                QToolTip.hideText()
            return True
        if e.type() == QEvent.Type.Leave:
            QToolTip.hideText()
        return super().event(e)

    def set_progress(self, value: float) -> None:
        p = max(0.0, min(1.0, value))
        if p != self._progress:
            self._progress = p
            self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        if self._progress <= 0.0:
            return
        painter = QPainter(self)
        bar_h = 5
        bar_w = int(self.width() * self._progress)
        painter.fillRect(0, self.height() - bar_h, bar_w, bar_h, QColor(255, 255, 255, 120))
        painter.end()


class _DeckGrid(QWidget):
    """A 2×2 deck-button grid that positions A│B and C│D so the split between
    the left and right decks lands exactly on the host window's vertical centre
    line — aligning with Traktor's deck split. The left decks (A/C) and right
    decks (B/D) are sized independently, so they need not be the same width.
    """

    H_GAP = 8
    V_GAP = 8

    def __init__(self, btns: dict, parent=None):
        super().__init__(parent)
        self._btns = btns
        for b in btns.values():
            b.setParent(self)
        self._row_h = max(28, btns["A"].sizeHint().height())
        self._rows = 2
        self.setFixedHeight(self._row_h * 2 + self.V_GAP)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_rows(self, rows: int) -> None:
        """One row (A│B) or two (A│B over C│D)."""
        rows = 2 if rows >= 2 else 1
        if rows == self._rows:
            return
        self._rows = rows
        for letter in ("C", "D"):
            self._btns[letter].setVisible(rows == 2)
        self.setFixedHeight(self._row_h * rows + self.V_GAP * (rows - 1))
        self._relayout()

    def _relayout(self) -> None:
        w, rh, half = self.width(), self._row_h, self.H_GAP / 2.0
        # Window centre line, expressed in this widget's local x.
        win = self.window()
        cx = self.mapFromGlobal(win.mapToGlobal(QPoint(win.width() // 2, 0))).x()
        cx = max(self.H_GAP, min(w - self.H_GAP, cx))
        lw = max(1, int(cx - half))  # left decks: 0 → centre
        rx = int(cx + half)  # right decks start past the gap
        rw = max(1, w - rx)
        y2 = rh + self.V_GAP
        self._btns["A"].setGeometry(0, 0, lw, rh)
        self._btns["B"].setGeometry(rx, 0, rw, rh)
        self._btns["C"].setGeometry(0, y2, lw, rh)
        self._btns["D"].setGeometry(rx, y2, rw, rh)

    def resizeEvent(self, e):
        self._relayout()
        super().resizeEvent(e)

    def showEvent(self, e):
        self._relayout()
        super().showEvent(e)

    def moveEvent(self, e):
        # The window may move/resize without resizing the grid; recompute so the
        # split keeps tracking the window centre.
        self._relayout()
        super().moveEvent(e)


class AutoModeButton(QPushButton):
    """QPushButton that shows its tooltip immediately on hover (no Qt delay)."""

    def enterEvent(self, event) -> None:
        QToolTip.showText(
            self.mapToGlobal(QPoint(0, self.height())),
            self.toolTip(),
            self,
        )
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        QToolTip.hideText()
        super().leaveEvent(event)
