"""Settings, Library and Stats in a window of their own, opened from Traktor.

The buttons libtk draws in its band inside Traktor's browser open these
pages without TK's main window (which may be minimised while the user works
in Traktor). The page widget is not copied: it moves into this window while
it is open and goes back to where it was (a page of the main window's panel
stack, or nowhere yet) when the window closes, so there is only ever one.

Inside the popup a tabbed page (Settings, Library) shows no tab bar: its
tabs are laid one after the other in a single scrolling page, each under a
small heading, and are put back as tabs when the window closes.

A normal titled window kept on top of Traktor, opened over Traktor's library
area at its size (as TK docks its own window there); the same band button,
Esc or the close button closes it. Not a ``Qt.Tool`` window: on macOS those
hide while TK is not the front app, and Traktor is.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QPoint, QSize, Qt
from PySide6.QtGui import QCloseEvent, QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QBoxLayout,
    QFrame,
    QLabel,
    QLayout,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from core.drag import snap_over_traktor_library
from ui.style import T

_DEFAULT_SIZE = QSize(1000, 680)


def _find_in_layout(layout: QLayout, widget: QWidget) -> tuple[QBoxLayout, int, int] | None:
    """The box layout holding *widget* (searched depth-first), its index and stretch."""
    for i in range(layout.count()):
        item = layout.itemAt(i)
        if item.widget() is widget and isinstance(layout, QBoxLayout):
            return layout, i, layout.stretch(i)
        child = item.layout()
        if child is not None:
            found = _find_in_layout(child, widget)
            if found:
                return found
    return None


class PanelPopup(QWidget):
    """Shows one page widget in its own window, then hands it back."""

    def __init__(
        self,
        title: str,
        page: QWidget,
        size: QSize = _DEFAULT_SIZE,
        *,
        place: Callable[[QWidget], bool] = snap_over_traktor_library,
    ) -> None:
        super().__init__(None, Qt.WindowType.Window | Qt.WindowType.WindowStaysOnTopHint)
        self.setWindowTitle(title)
        self._place = place
        self._page = page
        self._home: tuple[QBoxLayout, int, int] | None = None
        # While open: the page's tabs, taken out to be shown one under another.
        self._tabs: list[tuple[str, QWidget, QWidget]] = []
        self._tab_index = 0
        self._flat: QScrollArea | None = None
        self._body = QVBoxLayout(self)
        self._body.setContentsMargins(0, 0, 0, 0)
        self.resize(size)
        esc = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        esc.activated.connect(self.close)

    def toggle(self, at: QPoint) -> None:
        """Open near *at* (the mouse, on the button in Traktor), or close."""
        if self.isVisible():
            self.close()
        else:
            self.open_at(at)

    def open_at(self, at: QPoint) -> None:
        parent = self._page.parentWidget()
        layout = parent.layout() if parent is not None else None
        self._home = _find_in_layout(layout, self._page) if layout is not None else None
        if self._home is not None:
            self._home[0].removeWidget(self._page)
        self._body.addWidget(self._page)
        self._flatten_tabs()
        self._page.show()
        self.move(self._on_screen(at))
        self.show()
        # Over Traktor's library, at its size; where Traktor can't be found, near the mouse.
        self._place(self)
        self.raise_()
        self.activateWindow()

    def _on_screen(self, at: QPoint) -> QPoint:
        """Just below *at*, kept inside that screen."""
        screen = QGuiApplication.screenAt(at) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        x = min(max(at.x() - self.width() // 2, area.left()), area.right() - self.width())
        y = min(max(at.y() + 16, area.top()), area.bottom() - self.height())
        return QPoint(x, y)

    def _tab_widget(self) -> QTabWidget | None:
        tw = getattr(self._page, "_tab_widget", None)
        return tw if isinstance(tw, QTabWidget) else None

    def _flatten_tabs(self) -> None:
        """Lay the page's tabs out one under another, with no tab bar."""
        tw = self._tab_widget()
        found = _find_in_layout(self._page.layout(), tw) if tw and self._page.layout() else None
        if tw is None or found is None or tw.count() == 0:
            return
        self._tab_index = tw.currentIndex()
        column = QWidget()
        col = QVBoxLayout(column)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(4)
        while tw.count():
            wrapper, name = tw.widget(0), tw.tabText(0)
            tw.removeTab(0)
            # Tabs are scroll_page() wrappers: show what they wrap, in our one scroll.
            content = wrapper.takeWidget() if isinstance(wrapper, QScrollArea) else wrapper
            self._tabs.append((name, wrapper, content))
            heading = QLabel(name)
            heading.setStyleSheet(f"color: {T.TEXT}; font-weight: 600; padding: 10px 16px 0 16px;")
            col.addWidget(heading)
            col.addWidget(content)
        col.addStretch(1)
        flat = QScrollArea()
        flat.setWidgetResizable(True)
        flat.setFrameShape(QFrame.Shape.NoFrame)
        flat.setWidget(column)
        layout, index, stretch = found
        tw.hide()
        layout.insertWidget(index, flat, stretch)
        self._flat = flat

    def _restore_tabs(self) -> None:
        tw = self._tab_widget()
        if tw is None or self._flat is None:
            return
        for name, wrapper, content in self._tabs:
            if isinstance(wrapper, QScrollArea):
                wrapper.setWidget(content)
            tw.addTab(wrapper, name)
        tw.setCurrentIndex(self._tab_index)
        tw.show()
        self._flat.hide()
        self._flat.deleteLater()
        self._flat = None
        self._tabs = []

    def closeEvent(self, event: QCloseEvent) -> None:
        self._restore_tabs()
        if self._page.parentWidget() is self:
            self._body.removeWidget(self._page)
            if self._home is not None:
                layout, index, stretch = self._home
                layout.insertWidget(index, self._page, stretch)
            else:
                self._page.hide()
                self._page.setParent(None)  # never shown in TK yet: the panel stack takes it later
            self._home = None
        super().closeEvent(event)
