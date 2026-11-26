"""The matching cards in a small window of their own, opened from Traktor.

The "MATCHING" button libtk draws in its band inside Traktor's browser posts
``/uiAction MATCHING``; this window then shows TK's axis cards under the
mouse, so the matching can be tuned without TK's main window. The cards are
not copied: the Now Playing tab's own axis panel moves in here while the
window is open and goes back to its place in the tab when it closes, so both
always show one and the same state.

It is a normal top-level window kept on top, not a ``Qt.Tool``: on macOS a
tool window hides while TK is not the front app, and Traktor is.
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QCloseEvent, QGuiApplication, QKeyEvent
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ui.style import T

#: Wide enough for four cards a row at the cards' minimum width.
_WIDTH = 640
_MARGIN = 8


class MatchingPopup(QWidget):
    """Hosts *tab*'s ``_axis_panel`` while open; *tab* gets it back on close."""

    def __init__(self, tab: QWidget) -> None:
        super().__init__(
            None,
            Qt.WindowType.Window
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint,
        )
        self._tab = tab
        self._home_index = -1
        self.setStyleSheet(f"MatchingPopup {{ background: {T.BG}; border: 1px solid {T.BLUE}; }}")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(_MARGIN, _MARGIN, _MARGIN, _MARGIN)
        outer.setSpacing(6)
        header = QHBoxLayout()
        title = QLabel("Matching")
        title.setStyleSheet(f"color: {T.TEXT}; font-weight: bold;")
        close_btn = QPushButton("×")
        close_btn.setFixedSize(24, 20)
        close_btn.setToolTip("Close (Esc)")
        close_btn.clicked.connect(self.close)
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(close_btn)
        outer.addLayout(header)
        self._body = QVBoxLayout()
        outer.addLayout(self._body, stretch=1)

    def toggle(self, at: QPoint) -> None:
        """Open under *at* (the mouse, on the button in Traktor), or close."""
        if self.isVisible():
            self.close()
        else:
            self.open_at(at)

    def open_at(self, at: QPoint) -> None:
        panel = self._tab._axis_panel
        home = self._tab._axis_panel_home
        self._home_index = home.indexOf(panel)
        home.removeWidget(panel)
        self._body.addWidget(panel)
        panel.show()
        inner = _WIDTH - 2 * _MARGIN
        height = panel.heightForWidth(inner) + 2 * _MARGIN + 30
        self.resize(_WIDTH, max(height, 120))
        self.move(self._on_screen(at))
        self.show()
        self.raise_()
        self.activateWindow()

    def _on_screen(self, at: QPoint) -> QPoint:
        """Top-left just below *at*, kept inside that screen."""
        screen = QGuiApplication.screenAt(at) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        x = min(max(at.x() - 20, area.left()), area.right() - self.width())
        y = min(max(at.y() + 12, area.top()), area.bottom() - self.height())
        return QPoint(x, y)

    def closeEvent(self, event: QCloseEvent) -> None:
        panel = self._tab._axis_panel
        if panel.parent() is self and self._home_index >= 0:
            self._body.removeWidget(panel)
            self._tab._axis_panel_home.insertWidget(self._home_index, panel, stretch=1)
            panel.show()
            self._home_index = -1
        super().closeEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(event)
