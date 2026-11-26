"""TK's own icons for the band in Traktor's browser, in Traktor's two button colours.

Traktor draws a button's picture exactly as stored: it cannot tint one, and it
has no black list, gear or grid icons. So TK draws its icons itself, with Qt,
as silhouettes (the glyph's shape in a single colour, colour emoji included):
grey when the button is off and black when it is lit, like the text of the
band's A-D buttons. libtk (``tk_band_icon``) turns the pixels into Traktor
pictures and shows them in place of its own.

Integrated mode only (TK running inside Traktor, on its main thread).
"""

from __future__ import annotations

import ctypes
import logging

import numpy as np
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter

from workers.native_lib import process_symbol

log = logging.getLogger(__name__)

#: Off: the grey of Traktor's button text. Lit: black (on Traktor blue).
OFF_ARGB = 0xFFA0A0A0
LIT_ARGB = 0xFF000000
#: Unlit but busy (the button's job runs): Traktor's blue, dimmed.
STATE_ARGB = 0xFF4A7FBF

#: Picture size in Traktor's units (the band's buttons are 40 x 28).
ICON_W, ICON_H = 24, 20

#: Band button → glyph: TK's own (np_constants / now_playing side buttons).
BUTTON_GLYPHS = {
    "MATCHING": "≈",
    "LIST": "☰",
    # Settings (one button, its page has its own tabs), About, then Library's tabs (ui.band_pages).
    "S_SETTINGS": "⚙",
    "S_ABOUT": "ⓘ",
    "L_SCAN": "⟳",
    "L_HOTCUES": "◆",
    "L_TAGGING": "✎",  # an emoji tag silhouettes into a blob
    "L_PLAYLISTS": "♫",
    "L_EXPORT": "⇪",
    "STATS": "▦",
}
#: Buttons drawn from one of Traktor's own pictures instead (id, frame), recoloured the same way:
#: Settings is Traktor's gear, as in its top bar (User, 2026-11-24).
TRAKTOR_PICTURES = {"S_SETTINGS": (514, 0)}

#: The AUTO button shows the mode, as TK's own automation button.
AUTO_GLYPHS = {"off": "⊘", "play_cue": "🎧", "automix": "▶", "auto": "🪄"}


#: Glyphs are drawn this big, then cropped to their ink and fitted to the icon: every font
#: draws its symbols at its own size, and some came out a third of the others.
_RENDER_PX = 96


def silhouette(glyph: str, argb: int, w: int = ICON_W, h: int = ICON_H) -> np.ndarray:
    """*glyph* fitted into a w x h picture, every pixel *argb*'s colour with the glyph's alpha.

    Returned as uint32 0xAARRGGBB, row by row: the layout Traktor's pictures use.
    """
    big = QImage(_RENDER_PX, _RENDER_PX, QImage.Format.Format_ARGB32)
    big.fill(Qt.GlobalColor.transparent)
    painter = QPainter(big)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    font = QFont()
    font.setPixelSize(int(_RENDER_PX * 0.8))
    painter.setFont(font)
    painter.setPen(QColor("white"))
    painter.drawText(QRectF(0, 0, _RENDER_PX, _RENDER_PX), Qt.AlignmentFlag.AlignCenter, glyph)
    painter.end()
    return _fit(big, argb, w, h)


def icon_pixels(key: str, argb: int, w: int = ICON_W, h: int = ICON_H) -> np.ndarray:
    """Band button *key*'s icon in *argb*: its drawn shape if it has one, else its glyph."""
    from ui.band_extensions import button_for
    from ui.native_icon_shapes import SHAPES, render

    extension = button_for(key)  # an add-on's button on one of libtk's spare slots
    draw = extension.icon if extension is not None else SHAPES.get(key)
    if draw is not None:
        return _fit(render(draw), argb, w, h)
    return silhouette(BUTTON_GLYPHS.get(key, key), argb, w, h)


def _fit(big: QImage, argb: int, w: int, h: int) -> np.ndarray:
    """*big*'s ink, cropped and fitted into w x h: every pixel *argb* with the ink's alpha."""
    side_w, side_h = big.width(), big.height()
    alpha = (_pixels(big) >> 24).reshape(side_h, side_w)
    ys, xs = np.nonzero(alpha)
    img = QImage(w, h, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    if len(xs):
        ink = big.copy(int(xs.min()), int(ys.min()), int(np.ptp(xs)) + 1, int(np.ptp(ys)) + 1)
        fitted = ink.scaled(
            w - 2,
            h - 2,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        painter = QPainter(img)
        painter.drawImage((w - fitted.width()) // 2, (h - fitted.height()) // 2, fitted)
        painter.end()
    a = _pixels(img) >> 24
    return (a << 24) | (argb & 0x00FFFFFF)


def _pixels(img: QImage) -> np.ndarray:
    img = img.convertToFormat(QImage.Format.Format_ARGB32)
    count = img.width() * img.height()
    return np.frombuffer(img.constBits(), dtype=np.uint32, count=count).copy()


def _lib_call():
    fn = process_symbol("tk_band_icon")
    if fn is None:
        return None
    fn.argtypes = [
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.POINTER(ctypes.c_uint32),
    ]
    fn.restype = ctypes.c_int
    return fn


def _picture_call():
    fn = process_symbol("tk_band_icon_from_picture")
    if fn is None:
        return None
    fn.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.c_uint, ctypes.c_uint, ctypes.c_uint32]
    fn.restype = ctypes.c_int
    return fn


def push_band_icons() -> int:
    """Send every band icon, off and lit, to libtk. Returns how many were taken."""
    fn = _lib_call()
    if fn is None:
        log.warning("libtk has no tk_band_icon: the band keeps Traktor's pictures")
        return 0
    from ui.band_extensions import assigned, push_tooltips
    from ui.native_icon_shapes import SHAPES

    keys = dict(BUTTON_GLYPHS)
    keys.update({f"AUTO:{mode}": glyph for mode, glyph in AUTO_GLYPHS.items()})
    keys.update({slot: "" for slot in assigned()})  # an add-on's buttons: their own shapes
    # The native page's own buttons (+, Link…, Unlink): drawn shapes only.
    keys.update({key: "" for key in SHAPES if key.startswith("NL_")})
    taken = 0
    for key, glyph in keys.items():
        for lit, argb in ((0, OFF_ARGB), (1, LIT_ARGB)):
            drawn = key in BUTTON_GLYPHS or key in SHAPES or key in assigned()
            pixels = icon_pixels(key, argb) if drawn else silhouette(glyph, argb)
            buf = pixels.ctypes.data_as(ctypes.POINTER(ctypes.c_uint32))
            if fn(key.encode(), lit, ICON_W, ICON_H, buf) == 0:
                taken += 1
    from_picture = _picture_call()
    for key, (pic, frame) in TRAKTOR_PICTURES.items() if from_picture else ():
        for lit, argb in ((0, OFF_ARGB), (1, LIT_ARGB)):
            if from_picture(key.encode(), lit, pic, frame, argb) != 0:
                log.warning("band icon %s: Traktor picture %d unusable; TK's glyph stays", key, pic)
    log.info("band icons: %d of %d taken by Traktor", taken, 2 * len(keys))
    if assigned():
        log.info("band: %d add-on tooltip(s) taken", push_tooltips())
    return taken


def push_state_icon(key: str, active: bool) -> bool:
    """Band button *key*'s unlit icon in dimmed blue while *active* (its job runs), grey
    otherwise (User, 2026-11-24). Push only on a change:
    libtk keeps every picture it was given alive."""
    fn = _lib_call()
    if fn is None:
        return False
    pixels = icon_pixels(key, STATE_ARGB if active else OFF_ARGB)
    return (
        fn(key.encode(), 0, ICON_W, ICON_H, pixels.ctypes.data_as(ctypes.POINTER(ctypes.c_uint32)))
        == 0
    )
