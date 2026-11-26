"""The automation modes as a small panel, shown by the AUTO button in Traktor's band.

Each click on AUTO moves to the next mode, as standalone TK's button does, and this panel shows
the four modes with the new one ticked and a line on what each does (User, 2026-11-25: "the big
popup was nice... if we can have it AND the button to work"). A click on a mode in it picks that
mode. It goes away by itself 1.5 s after the last click, but not while the mouse is
over it.

It never takes the focus from Traktor (the menu that did read as a tooltip the mouse had to go
to, and closed when Traktor got a click). Like :class:`ui.matching_popup.MatchingPopup` it is a
frameless top-level window kept on top, not a Qt popup or a ``QMenu``: it lives in Traktor's
process beside Traktor's own Qt, so it grabs neither the mouse nor the keyboard.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QEvent, QPoint, Qt, QTimer
from PySide6.QtGui import QEnterEvent, QGuiApplication, QKeyEvent
from PySide6.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget

from ui.np_constants import _AUTO_MODES
from ui.style import T

_WIDTH = 300
_MARGIN = 6
_TICK = "✓"
#: How long the panel stays after the last AUTO click (the mouse elsewhere).
SHOW_MS = 1500  # User, 2026-11-25: "should go away after a sec or two"


def _summary(tooltip: str) -> tuple[str, str]:
    """("AutoMix", "Silence channel fader before load · …") from a mode's tooltip."""
    lines = [ln.strip(" •") for ln in tooltip.split("\n") if ln.strip()]
    name = lines[0].split(":", 1)[-1].strip() if lines else ""
    return name, " · ".join(lines[1:])


class AutoModePopup(QWidget):
    """Calls *on_choose* with the mode key clicked in it ("off", "play_cue", "automix", "auto")."""

    def __init__(self, on_choose: Callable[[str], None]) -> None:
        super().__init__(
            None,
            Qt.WindowType.Window
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)  # Traktor keeps the focus
        self._on_choose = on_choose
        self._buttons: dict[str, QPushButton] = {}
        self._labels: dict[str, str] = {}
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.setInterval(SHOW_MS)
        self._hide_timer.timeout.connect(self.close)
        self.setFixedWidth(_WIDTH)
        self.setStyleSheet(
            f"AutoModePopup {{ background: {T.BG}; border: 1px solid {T.BLUE}; }}"
            f"QPushButton {{ text-align: left; padding: 6px 8px; border: none;"
            f" background: transparent; color: {T.TEXT}; }}"
            f"QPushButton:hover {{ background: {T.BTN_HOVER}; }}"
            f"QPushButton:checked {{ color: {T.TRAKTOR_BLUE}; font-weight: bold; }}"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(_MARGIN, _MARGIN, _MARGIN, _MARGIN)
        layout.setSpacing(0)
        title = QLabel("Automation for every track loaded")
        title.setStyleSheet(f"color: {T.TEXT_DIM}; padding: 2px 8px 4px 8px;")
        layout.addWidget(title)
        for key, icon, _color, tooltip in _AUTO_MODES:
            name, detail = _summary(tooltip)
            self._labels[key] = f"{icon}  {name}"
            button = QPushButton(self._labels[key])
            button.setCheckable(True)
            button.clicked.connect(lambda _c=False, k=key: self.choose(k))
            layout.addWidget(button)
            hint = QLabel(detail)
            hint.setWordWrap(True)
            hint.setStyleSheet(f"color: {T.TEXT_DIM}; font-size: 11px; padding: 0 8px 6px 30px;")
            layout.addWidget(hint)
            self._buttons[key] = button

    def mode_keys(self) -> list[str]:
        return list(self._buttons)

    def checked_key(self) -> str | None:
        return next((k for k, b in self._buttons.items() if b.isChecked()), None)

    def hides_soon(self) -> bool:
        return self._hide_timer.isActive()

    def show_mode(self, at: QPoint, current: str) -> None:
        """Show *current* ticked: opened under *at* (the mouse, on the AUTO button in Traktor),
        or, already open, ticked where it is. Hides SHOW_MS after the last call."""
        self._tick(current)
        if not self.isVisible():
            self.adjustSize()
            self.move(self._on_screen(at))
            self.show()
            self.raise_()
        if not self.underMouse():
            self._hide_timer.start()

    def choose(self, key: str) -> None:
        self.close()
        self._on_choose(key)

    def _tick(self, current: str) -> None:
        for key, button in self._buttons.items():
            button.setChecked(key == current)
            button.setText(f"{_TICK if key == current else '   '}  {self._labels[key]}")

    def _on_screen(self, at: QPoint) -> QPoint:
        """Top-left just below *at*, kept inside that screen."""
        screen = QGuiApplication.screenAt(at) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        x = min(max(at.x() - 20, area.left()), area.right() - self.width())
        y = min(max(at.y() + 12, area.top()), area.bottom() - self.height())
        return QPoint(x, y)

    def enterEvent(self, event: QEnterEvent) -> None:
        self._hide_timer.stop()  # reading it, or about to click a mode
        super().enterEvent(event)

    def leaveEvent(self, event: QEvent) -> None:
        self._hide_timer.start()
        super().leaveEvent(event)

    def hideEvent(self, event) -> None:
        self._hide_timer.stop()
        super().hideEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(event)
