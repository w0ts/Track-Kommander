"""Icons drawn from text glyphs, all the same size.

The sidebar mixes a colour emoji (🎧), dingbats (⚙ ♫ ▦) and a plain × —
each from a different font, each drawn at its own idea of 18px. Rendering a
glyph large, cropping to its ink and scaling that into a fixed box gives
every icon the same footprint, whatever font it came from.
"""

from __future__ import annotations

from functools import lru_cache

import numpy as np
from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QImage, QPainter, QPixmap
from PySide6.QtWidgets import QApplication

#: Point size the glyph is drawn at before cropping. Large, so scaling
#: down keeps the edges clean.
_RENDER_PX = 96
_CANVAS = _RENDER_PX * 3


def ink_box(img: QImage) -> tuple[int, int, int, int]:
    """(left, top, right, bottom) of the non-transparent pixels, inclusive.

    The whole image when nothing was drawn, so a caller never gets an empty
    rectangle.
    """
    img = img.convertToFormat(QImage.Format.Format_ARGB32)
    h, w = img.height(), img.width()
    buf = np.frombuffer(img.constBits(), dtype=np.uint8)
    alpha = buf.reshape(h, img.bytesPerLine())[:, 3 : w * 4 : 4]
    rows = np.flatnonzero(alpha.any(axis=1))
    cols = np.flatnonzero(alpha.any(axis=0))
    if not len(rows) or not len(cols):
        return 0, 0, w - 1, h - 1
    return int(cols[0]), int(rows[0]), int(cols[-1]), int(rows[-1])


def _dpr() -> float:
    app = QApplication.instance()
    screen = app.primaryScreen() if app else None
    return float(screen.devicePixelRatio()) if screen else 1.0


@lru_cache(maxsize=64)
def _render(glyph: str, color: str, box: int, family: str, dpr: float) -> QPixmap:
    font = QFont(family) if family else QFont()
    font.setPixelSize(_RENDER_PX)

    big = QImage(_CANVAS, _CANVAS, QImage.Format.Format_ARGB32_Premultiplied)
    big.fill(Qt.GlobalColor.transparent)
    painter = QPainter(big)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    painter.setFont(font)
    painter.setPen(QColor(color))
    painter.drawText(QRect(0, 0, _CANVAS, _CANVAS), Qt.AlignmentFlag.AlignCenter, glyph)
    painter.end()

    left, top, right, bottom = ink_box(big)
    ink = big.copy(QRect(left, top, right - left + 1, bottom - top + 1))

    side = round(box * dpr)
    scaled = ink.scaled(
        side, side, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
    )
    canvas = QImage(side, side, QImage.Format.Format_ARGB32_Premultiplied)
    canvas.fill(Qt.GlobalColor.transparent)
    painter = QPainter(canvas)
    painter.drawImage((side - scaled.width()) // 2, (side - scaled.height()) // 2, scaled)
    painter.end()

    pixmap = QPixmap.fromImage(canvas)
    pixmap.setDevicePixelRatio(dpr)
    return pixmap


@lru_cache(maxsize=64)
def _icon(glyph: str, color: str, hover: str, box: int, family: str, dpr: float) -> QIcon:
    icon = QIcon()
    icon.addPixmap(_render(glyph, color, box, family, dpr), QIcon.Mode.Normal)
    icon.addPixmap(_render(glyph, hover, box, family, dpr), QIcon.Mode.Active)
    return icon


def glyph_icon(
    glyph: str, color: str, hover: str | None = None, *, box: int = 22, family: str = ""
) -> QIcon:
    """*glyph* as an icon filling a *box*-pixel square, in *color*, with the
    Active (hovered) state in *hover*. A colour emoji keeps its own colours.

    *family* pins a font — Windows draws U+2699 from its colour-emoji font
    unless told otherwise. Cached: the same glyph and colours give the same
    QIcon, so a button whose colour flips often does not redraw it.
    """
    return _icon(glyph, color, hover or color, box, family, _dpr())
