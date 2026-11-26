"""Settings, Library and Stats as pages inside the main window.

They were separate dialogs, and a docked window is kept on top of Traktor:
two top-level windows at one level are ordered by whichever was raised
last, so the dialogs kept ending up underneath the main window, and had to
follow its level through every app switch. As pages there is one window,
and nothing to order.

The home page is the Now Playing view. A panel page has a header — its
title, a Maximize button, a Back button (Escape also goes back) and the
same red close cross as the Now Playing view — above the panel's widget.
The window is frameless: dragging the header moves it. Maximize fills the screen while a panel is open; Back
puts the window back at the size it had, which is also what
re-docks it over Traktor: docking is read from the window's geometry.
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ui.style import T

#: The Now Playing view's close cross (ui.now_playing), red on hover.
CLOSE_STYLE = (
    "QPushButton { padding: 0; background: transparent; border: none; }"
    "QPushButton:hover { background: #c0392b; border-radius: 6px; }"
    "QPushButton:pressed { background: #962d22; border-radius: 6px; }"
)


class _Header(QWidget):
    """A panel's title bar: dragging it moves the frameless window."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._drag: QPoint | None = None

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            top_left = self.window().frameGeometry().topLeft()
            self._drag = event.globalPosition().toPoint() - top_left
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._drag is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.window().move(event.globalPosition().toPoint() - self._drag)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._drag = None
        super().mouseReleaseEvent(event)


class PanelStack(QStackedWidget):
    """The home view, with panels shown in its place one at a time."""

    #: The key of the panel now shown, or "" for the home view.
    panel_changed = Signal(str)

    def __init__(self, home: QWidget, parent=None) -> None:
        super().__init__(parent)
        self._home = home
        self.addWidget(home)
        self._pages: dict[str, QWidget] = {}
        self._max_buttons: list[QPushButton] = []
        #: The window's geometry before Maximize, or None when not maximized.
        self._pre_max_geom: QRect | None = None

    @property
    def current_panel(self) -> str:
        current = self.currentWidget()
        return next((k for k, page in self._pages.items() if page is current), "")

    def has_panel(self, key: str) -> bool:
        return key in self._pages

    def show_panel(self, key: str, title: str, widget: QWidget) -> None:
        """Show panel *key* (built from *widget* the first time)."""
        page = self._pages.get(key)
        if page is None:
            page = self._make_page(title, widget)
            self._pages[key] = page
            self.addWidget(page)
        self.setCurrentWidget(page)
        self.panel_changed.emit(key)

    def toggle_panel(self, key: str, title: str, widget: QWidget) -> None:
        """Show panel *key*, or go home if it is already the one shown."""
        if self.current_panel == key:
            self.close_panel()
        else:
            self.show_panel(key, title, widget)

    def close_panel(self) -> None:
        """Back to the home view, at the size the window had before Maximize."""
        self.restore_size()
        if self.currentWidget() is not self._home:
            self.setCurrentWidget(self._home)
            self.panel_changed.emit("")

    @property
    def is_maximized(self) -> bool:
        return self._pre_max_geom is not None

    def maximize(self) -> None:
        """Fill the screen the window is on (not the menu bar or Dock).

        The window is frameless, so this sets the geometry rather than
        asking the window manager to maximize it.
        """
        win = self.window()
        screen = win.screen()
        if self.is_maximized or screen is None:
            return
        self._pre_max_geom = QRect(win.geometry())
        win.setGeometry(screen.availableGeometry())
        self._sync_max_buttons()

    def restore_size(self) -> None:
        """Put the window back where it was before Maximize, if it was."""
        if self._pre_max_geom is None:
            return
        geom, self._pre_max_geom = self._pre_max_geom, None
        self.window().setGeometry(geom)
        self._sync_max_buttons()

    def _sync_max_buttons(self) -> None:
        # Maximized, the button goes away: Back is what restores the size.
        for button in self._max_buttons:
            button.setVisible(not self.is_maximized)

    def _make_page(self, title: str, widget: QWidget) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        header = _Header()
        header.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        header.setStyleSheet(f"background: {T.PANEL};")
        row = QHBoxLayout(header)
        row.setContentsMargins(14, 6, 8, 6)
        label = QLabel(title)
        label.setStyleSheet(f"color: {T.TEXT}; font-weight: 600;")
        row.addWidget(label)
        row.addStretch()
        maximize = QPushButton("⤢  Maximize")
        maximize.setToolTip("Fill the screen while this is open")
        maximize.clicked.connect(self.maximize)
        self._max_buttons.append(maximize)
        self._sync_max_buttons()
        row.addWidget(maximize)
        back = QPushButton("‹  Back")
        back.setToolTip("Back to the tracks (Esc)")
        back.clicked.connect(self.close_panel)
        row.addWidget(back)
        row.addWidget(self._close_button())
        lay.addWidget(header)
        lay.addWidget(widget, stretch=1)

        esc = QShortcut(QKeySequence(Qt.Key.Key_Escape), page)
        esc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        esc.activated.connect(self.close_panel)
        return page

    def _close_button(self) -> QPushButton:
        from ui.icons import glyph_icon
        from ui.np_constants import _SIDE_ICON_PX as SIDE

        close = QPushButton()
        close.setFixedSize(36, 28)
        close.setIcon(glyph_icon("×", T.TEXT_DIM, "#ffffff", box=SIDE))
        close.setIconSize(QSize(SIDE, SIDE))
        close.setToolTip("Close")
        close.setStyleSheet(CLOSE_STYLE)
        close.clicked.connect(lambda: self.window().close())
        return close
