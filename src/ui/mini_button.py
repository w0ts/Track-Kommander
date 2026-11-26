"""The window minimised to one small floating button.

Minimising uncovers Traktor's library entirely: the main window hides and
this button stays behind, above Traktor, showing the app icon. A click
brings the window back; a drag moves the button out of the way, and where
it was put is remembered for next time.
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, QSettings, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPen
from PySide6.QtWidgets import QWidget

from ui.style import T

#: Side of the button, in logical pixels.
SIZE = 72


def app_icon() -> QIcon:
    """The TK logo, read from the bundled file.

    Not ``QApplication.windowIcon()``: the compiled app takes its Dock icon
    from the bundle's .icns and leaves that empty, which drew a black square.
    """
    from core.runtime import bundle_data_dir

    path = bundle_data_dir() / "assets" / "icons" / "tk.png"
    return QIcon(str(path)) if path.exists() else QIcon()


#: Movement under this is a click, not a drag.
_DRAG_THRESHOLD_PX = 4

_POS_KEY = "mini_button_pos"


class MiniButton(QWidget):
    """A small always-on-top button that restores the main window."""

    restore_requested = Signal()

    def __init__(self, icon: QIcon | None = None, parent=None) -> None:
        super().__init__(
            parent,
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint,
        )
        # A Qt tool window on macOS is an NSPanel that hides whenever the app
        # is not active — i.e. the moment the user clicks into Traktor.
        self.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedSize(SIZE, SIZE)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Track Kommander — click to bring it back, drag to move")
        self._icon = icon if icon is not None and not icon.isNull() else app_icon()
        self._press: QPoint | None = None
        self._press_pos: QPoint | None = None
        self._dragged = False

    # ── Placement ───────────────────────────────────────────────────────────

    def show_at(self, default: QPoint) -> None:
        """Show at the remembered spot, or at *default* the first time."""
        saved = QSettings().value(_POS_KEY)
        pos = saved if isinstance(saved, QPoint) else default
        self.move(pos)
        self.show()
        self.raise_()

    # ── Painting ────────────────────────────────────────────────────────────

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(Qt.GlobalColor.black)
        r = self.rect().adjusted(1, 1, -1, -1)
        radius = SIZE * 0.2
        painter.drawRoundedRect(r, radius, radius)
        # QIcon.paint centres and scales for the screen's pixel ratio. Placing
        # a pixmap by hand used its size in device pixels — twice the logical
        # size on a Retina display — and pushed the logo to the top left.
        self._icon.paint(painter, self.rect().adjusted(4, 4, -4, -4), Qt.AlignmentFlag.AlignCenter)
        # The docked frame's blue, so it reads as the same thing, shrunk.
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(T.TRAKTOR_BLUE), 3))
        painter.drawRoundedRect(r, radius, radius)
        painter.end()

    # ── Click to restore, drag to move ──────────────────────────────────────

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._press = event.globalPosition().toPoint()
            self._press_pos = self.pos()
            self._dragged = False

    def mouseMoveEvent(self, event) -> None:
        if self._press is None:
            return
        delta = event.globalPosition().toPoint() - self._press
        if not self._dragged and delta.manhattanLength() < _DRAG_THRESHOLD_PX:
            return
        self._dragged = True
        self.move(self._press_pos + delta)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton or self._press is None:
            return
        self._press = None
        if self._dragged:
            QSettings().setValue(_POS_KEY, self.pos())
        else:
            self.restore_requested.emit()
