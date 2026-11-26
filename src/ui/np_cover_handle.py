"""The cover-art drag handle of the Now Playing tab, and its art loader.

The handle is the app icon or the current track's cover: it drags the
frameless window, snaps it over Traktor's library on double-click and draws
the docked frame. It is a self-contained label with its own snap state, so it
lives apart from the tab that hosts it. ``ui.now_playing`` re-exports both
names for the callers that still import them from there.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QLabel, QSizePolicy

from core.audio_meta import read_cover_art
from core.runtime import bundle_data_dir
from ui.cursors import MOVE_CURSOR
from ui.np_format import still_snapped
from ui.style import T

_log = logging.getLogger(__name__)


_ICON_PATH = bundle_data_dir() / "assets" / "icons" / "tk.png"


def _load_album_art(path: str) -> QPixmap | None:
    """Embedded album art from an audio file, or None if absent or undecodable."""
    data = read_cover_art(path)
    if not data:
        return None
    px = QPixmap()
    px.loadFromData(data)
    return px if not px.isNull() else None


class _DragHandle(QLabel):
    """App-icon / album-art label that lets the user drag the frameless window."""

    #: Double-clicked while Traktor is not running: start it, then snap.
    start_traktor_requested = Signal()
    #: The snapped geometry was recorded or cleared.
    snap_changed = Signal()

    #: The frame drawn round the cover while docked: thick, in the blue
    #: Traktor lights its active buttons with, so it reads as part of
    #: Traktor; a thin dark line inside keeps bright art from swallowing it.
    DOCKED_FRAME_PX = 4
    DOCKED_FRAME_COLOR = T.TRAKTOR_BLUE
    DOCKED_INNER_COLOR = T.BG

    def __init__(self, parent=None):
        super().__init__(parent)
        self._drag_pos = None
        self._fallback_px = QPixmap(str(_ICON_PATH)) if _ICON_PATH.exists() else QPixmap()
        self._display_px = self._fallback_px
        self._snapped_geom: tuple[int, int, int, int] | None = None
        self._pre_snap_geom: tuple[int, int, int, int] | None = None
        self._framed = False
        # Square cover that fills the left column width (it sits above the
        # BPM/Key readout and the filter box).
        self.setFixedSize(120, 120)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setToolTip(
            "Drag to move window\n"
            "Double-click to snap over Traktor's library (double-click again to restore)\n"
            "If Traktor is not running, double-click starts it\n"
            "Right-click for Settings, Library and the log"
        )
        self.setCursor(MOVE_CURSOR)
        self._repaint()  # paint the fallback icon at full size before first show

    def set_art(self, art: QPixmap | None) -> None:
        """Show album art if provided and valid, otherwise fall back to the app icon."""
        self._display_px = art if (art and not art.isNull()) else self._fallback_px
        self._repaint()

    def _repaint(self) -> None:
        if not self._display_px.isNull():
            self.setPixmap(
                self._display_px.scaled(
                    self.size(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )

    def resizeEvent(self, event) -> None:
        self._repaint()
        super().resizeEvent(event)

    def set_framed(self, on: bool) -> None:
        """Draw (or stop drawing) the docked frame round the cover."""
        if on != self._framed:
            self._framed = on
            self.update()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if not self._framed:
            return
        from PySide6.QtGui import QColor, QPainter

        w = self.DOCKED_FRAME_PX
        painter = QPainter(self)
        # Filled bands rather than a wide pen: exact pixels, no half-pixel
        # antialiasing at the edges.
        outer = self.rect()
        painter.fillRect(outer, QColor(self.DOCKED_FRAME_COLOR))
        inner = outer.adjusted(w, w, -w, -w)
        painter.fillRect(inner, QColor(self.DOCKED_INNER_COLOR))
        # Put the art back inside the frame.
        pm = self.pixmap()
        if pm is not None and not pm.isNull():
            art = inner.adjusted(1, 1, -1, -1)
            scaled = pm.scaled(
                art.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            x = art.x() + (art.width() - scaled.width()) // 2
            y = art.y() + (art.height() - scaled.height()) // 2
            painter.drawPixmap(x, y, scaled)
        painter.end()

    def _set_snapped(self, geom: tuple[int, int, int, int] | None) -> None:
        self._snapped_geom = geom
        self.snap_changed.emit()

    def mark_snapped(self) -> None:
        """Record the window's current geometry as snapped.

        For snaps made elsewhere (the startup snap), so the window counts as
        docked and a later layout change re-anchors it.
        """
        g = self.window().geometry()
        self._set_snapped((g.x(), g.y(), g.width(), g.height()))

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = (
                event.globalPosition().toPoint() - self.window().frameGeometry().topLeft()
            )
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._drag_pos is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.window().move(event.globalPosition().toPoint() - self._drag_pos)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._drag_pos = None
        super().mouseReleaseEvent(event)

    def resnap_if_snapped(self) -> None:
        """Re-run the library snap if the window is currently snapped.

        Called when Traktor's deck layout flips (2↔4 decks) so the overlay
        re-anchors below the new deck row instead of covering C/D. No-op when
        the user has since moved or restored the window (geometry no longer
        matches the snapped rect).
        """
        if self._snapped_geom is None:
            return
        win = self.window()
        cur = win.geometry()
        if not still_snapped(self._snapped_geom, (cur.x(), cur.y(), cur.width(), cur.height())):
            _log.info(
                "resnap: window moved since the snap (%s vs %s) — leaving it",
                (cur.x(), cur.y(), cur.width(), cur.height()),
                self._snapped_geom,
            )
            return  # user moved/resized since snapping — leave it alone
        from core.drag import snap_over_traktor_library

        if snap_over_traktor_library(win):
            new = win.geometry()
            self._set_snapped((new.x(), new.y(), new.width(), new.height()))

    def mouseDoubleClickEvent(self, event) -> None:
        """Toggle snap to Traktor's library overlay rect.

        With Traktor not running there is nothing to snap to, so the
        double-click asks for it to be started instead; the window snaps
        once Traktor's window appears.
        """
        if event.button() != Qt.MouseButton.LeftButton:
            super().mouseDoubleClickEvent(event)
            return

        from core.traktor import is_traktor_running

        if not is_traktor_running():
            self.start_traktor_requested.emit()
            super().mouseDoubleClickEvent(event)
            return

        from core.drag import snap_over_traktor_library

        win = self.window()
        cur = win.geometry()
        cur_tuple = (cur.x(), cur.y(), cur.width(), cur.height())

        # Toggle: if still at the snapped geometry, restore the previous one.
        if (
            self._snapped_geom is not None
            and self._pre_snap_geom is not None
            and still_snapped(self._snapped_geom, cur_tuple)
        ):
            x, y, w, h = self._pre_snap_geom
            win.setGeometry(x, y, w, h)
            win.raise_()
            win.activateWindow()
            self._pre_snap_geom = None
            self._set_snapped(None)
            return

        self._pre_snap_geom = cur_tuple
        if snap_over_traktor_library(win):
            new = win.geometry()
            self._set_snapped((new.x(), new.y(), new.width(), new.height()))
        else:
            # Snap failed (Traktor not running / not on darwin) — clear stash.
            self._pre_snap_geom = None
        super().mouseDoubleClickEvent(event)
