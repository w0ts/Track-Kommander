"""Band icons no font has, drawn as white shapes on a square canvas (User, 2026-11-24).

:mod:`ui.native_icons` fits each into the band's icon size and colours it like the glyph ones:
only the shape's alpha is used. Export is a USB stick, Hotcues a location pin, Tagging the Discogs
logo, read from ``traktor-native/assets``. An add-on draws its own buttons' icons
(core.extensions.BandButton.icon), the same way.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath

#: Canvas side in pixels: shapes are drawn big, then fitted to the icon.
SIDE = 96


def cut_out(p: QPainter) -> None:
    """Draw what follows as holes in what is already there."""
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)


def usb_stick(p: QPainter) -> None:
    p.drawRoundedRect(QRectF(28, 38, 40, 54), 6, 6)  # body
    p.drawRect(QRectF(34, 6, 28, 34))  # connector
    cut_out(p)
    p.drawRect(QRectF(40, 14, 6, 8))  # its two contacts
    p.drawRect(QRectF(50, 14, 6, 8))


def location_pin(p: QPainter) -> None:
    pin = QPainterPath()
    pin.addEllipse(QPointF(48, 36), 28, 28)
    tip = QPainterPath()
    tip.moveTo(24, 46)
    tip.lineTo(48, 92)
    tip.lineTo(72, 46)
    tip.closeSubpath()
    p.drawPath(pin.united(tip))
    cut_out(p)
    p.drawEllipse(QPointF(48, 36), 11, 11)


def _logo(name: str) -> Callable[[QPainter], None]:
    def draw(p: QPainter) -> None:
        path = Path(os.environ.get("TK_REPO", "")) / "traktor-native" / "assets" / name
        img = QImage(str(path))
        if img.isNull():
            p.drawEllipse(QPointF(48, 48), 30, 30)  # a plain dot rather than nothing
            return
        p.drawImage(QRectF(0, 0, SIDE, SIDE), img)

    return draw


def plus(p: QPainter) -> None:
    p.drawRect(QRectF(40, 12, 16, 72))
    p.drawRect(QRectF(12, 40, 72, 16))


def _link(broken: bool) -> Callable[[QPainter], None]:
    """TK's own link / broken-link icon (ui.link_icons), as the Qt Spotify page showed it."""

    def draw(p: QPainter) -> None:
        from ui.link_icons import link_icon

        p.drawPixmap(0, 0, SIDE, SIDE, link_icon(broken=broken).pixmap(SIDE, SIDE))

    return draw


#: Band button → its drawn shape (these take precedence over a glyph). NL_*: the native page's
#: own buttons (libtk tk_nlist.mm), the icons the Qt page had (User, 2026-11-24).
SHAPES: dict[str, Callable[[QPainter], None]] = {
    "NL_NEW": plus,
    "NL_LINK": _link(broken=False),
    "NL_UNLINK": _link(broken=True),
    "L_EXPORT": usb_stick,
    "L_HOTCUES": location_pin,
    "L_TAGGING": _logo("discogs.png"),  # Tagging = the Discogs (and Spotify) lookups
}


def render(draw: Callable[[QPainter], None]) -> QImage:
    """*draw*'s shape, white on transparent, SIDE x SIDE."""
    img = QImage(SIDE, SIDE, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("white"))
    draw(p)
    p.end()
    return img
