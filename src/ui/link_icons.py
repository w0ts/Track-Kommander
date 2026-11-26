"""A chain-link icon, whole or broken, drawn rather than taken from a font:
no font has a broken link, and the emoji 🔗 keeps its own colours."""

from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QApplication

_SIDE = 32  # drawing units; scaled to the device


def _dpr() -> float:
    app = QApplication.instance()
    screen = app.primaryScreen() if app else None
    return float(screen.devicePixelRatio()) if screen else 1.0


@lru_cache(maxsize=8)
def _pixmap(broken: bool, colour: str, dpr: float) -> QPixmap:
    pixmap = QPixmap(round(_SIDE * dpr), round(_SIDE * dpr))
    pixmap.setDevicePixelRatio(dpr)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(QColor(colour), 3.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    painter.translate(_SIDE / 2, _SIDE / 2)
    painter.rotate(-45)
    apart = 4.0 if broken else 0.0  # each half moves out this far
    for x in (-15.0 - apart, -1.0 + apart):  # two capsules, overlapping when whole
        painter.drawRoundedRect(QRectF(x, -5.5, 16, 11), 5.5, 5.5)
    painter.rotate(45)
    if broken:  # the spark between the halves
        for a, b in (((-2, -9), (-4, -13)), ((2, 9), (4, 13)), ((6, -6), (10, -9))):
            painter.drawLine(QPointF(*a), QPointF(*b))
    painter.end()
    return pixmap


def link_icon(broken: bool = False, colour: str = "#2ecc71") -> QIcon:
    """A chain link in *colour*: whole (linked), or broken (to unlink)."""
    return QIcon(_pixmap(broken, colour, _dpr()))
