"""Pop-ups drawn inside the main window, not as windows of their own.

Docked, the main window is kept on top of Traktor. A pop-up opened as its
own window sits at the normal level, and so it stayed hidden under the main
window — one was only seen once Traktor was quit. Juggling window levels to
fix that was fragile; drawn inside the window, a pop-up has nothing to be
ordered against.

An :class:`InlineDialog` covers its host window with a dimmed backdrop and
shows a card: title, text, optional extra widgets, a row of buttons. Enter
takes the default button, Escape the cancel one. :meth:`run` waits for the
answer with a local event loop, like ``QDialog.exec`` — so callers read the
result on the next line, as they did with message boxes.

Without a visible host window — before the main window is up, or while it is
minimised to its button — the helpers in :mod:`ui.dialogs` fall back to an
ordinary message box.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QEventLoop, QObject, Qt
from PySide6.QtGui import QColor, QKeySequence, QPainter, QShortcut
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ui.style import T

#: Widest the card grows, in logical pixels.
CARD_MAX_WIDTH = 560


def host_for(parent: QWidget | None) -> QWidget | None:
    """The window a pop-up for *parent* is drawn in, or None if not shown."""
    if parent is None:
        return None
    host = parent.window()
    return host if host is not None and host.isVisible() else None


class InlineDialog(QWidget):
    """A card over a dimmed host window; :meth:`run` returns the button key."""

    def __init__(
        self,
        host: QWidget,
        title: str,
        text: str,
        *,
        buttons: list[tuple[str, str]],
        default: str | None = None,
        escape: str | None = None,
    ) -> None:
        super().__init__(host)
        self._host = host
        self._result: str | None = None
        self._loop: QEventLoop | None = None
        self._escape = escape
        self.buttons: dict[str, QPushButton] = {}

        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.setGeometry(host.rect())
        host.installEventFilter(self)  # follow the host's size

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 20, 20, 20)
        outer.addStretch()
        row = QHBoxLayout()
        row.addStretch()
        self.card = QFrame()
        self.card.setObjectName("inlineCard")
        # Children transparent: the app stylesheet gives labels (and other
        # widgets) a background of their own, which showed as darker boxes
        # inside the card.
        self.card.setStyleSheet(
            f"#inlineCard {{ background: {T.PANEL}; border: 1px solid {T.BORDER};"
            " border-radius: 10px; }"
            " #inlineCard QLabel, #inlineCard QCheckBox { background: transparent; }"
        )
        self.card.setMaximumWidth(CARD_MAX_WIDTH)
        row.addWidget(self.card, stretch=1)
        row.addStretch()
        outer.addLayout(row)
        outer.addStretch()

        self._lay = QVBoxLayout(self.card)
        self._lay.setContentsMargins(20, 16, 20, 16)
        self._lay.setSpacing(10)
        self.title_label = QLabel(title)
        self.title_label.setStyleSheet(
            f"color: {T.TEXT}; font-weight: 600; font-size: 14px; background: transparent;"
        )
        self.title_label.setWordWrap(True)
        self._lay.addWidget(self.title_label)
        self.text_label = QLabel(text)
        self.text_label.setStyleSheet(f"color: {T.TEXT}; background: transparent;")
        self.text_label.setWordWrap(True)
        self.text_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._lay.addWidget(self.text_label)
        self._extras = QVBoxLayout()
        self._lay.addLayout(self._extras)

        brow = QHBoxLayout()
        brow.addStretch()
        for key, label in buttons:
            btn = QPushButton(label)
            btn.clicked.connect(lambda _=False, k=key: self.finish(k))
            brow.addWidget(btn)
            self.buttons[key] = btn
        self._lay.addLayout(brow)
        if default in self.buttons:
            self.buttons[default].setDefault(True)
            self.buttons[default].setAutoDefault(True)
            self._default = default
        else:
            self._default = None

        for seq, act in (
            (Qt.Key.Key_Return, self._press_default),
            (Qt.Key.Key_Enter, self._press_default),
            (Qt.Key.Key_Escape, self._press_escape),
        ):
            sc = QShortcut(QKeySequence(seq), self)
            sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            sc.activated.connect(act)

    # ── Building ────────────────────────────────────────────────────────────

    def add_widget(self, widget: QWidget) -> None:
        """Put *widget* between the text and the buttons."""
        self._extras.addWidget(widget)

    # ── Running ─────────────────────────────────────────────────────────────

    def run(self) -> str | None:
        """Show over the host and wait. The key of the button pressed, or of
        whatever called :meth:`finish`; None if the host went away."""
        self.setGeometry(self._host.rect())
        self.show()
        self.raise_()
        focus = self.buttons.get(self._default) if self._default else None
        (focus or self).setFocus(Qt.FocusReason.OtherFocusReason)
        self._loop = QEventLoop()
        if self._result is None:
            self._loop.exec()
        self._loop = None
        self.hide()
        self._host.removeEventFilter(self)
        # Detached, not deleteLater(): a queued delete outliving its host (a
        # window closed meanwhile) would run on a destroyed object. Unparented,
        # it goes with its last Python reference.
        self.setParent(None)
        return self._result

    def finish(self, key: str) -> None:
        """Close with *key* as the answer."""
        if self._result is None:
            self._result = key
        if self._loop is not None:
            self._loop.quit()

    def _press_default(self) -> None:
        if self._default:
            self.finish(self._default)

    def _press_escape(self) -> None:
        if self._escape:
            self.finish(self._escape)

    # ── Look and size ───────────────────────────────────────────────────────

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 150))
        painter.end()

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if obj is self._host and event.type() == QEvent.Type.Resize:
            self.setGeometry(self._host.rect())
        return False

    def mousePressEvent(self, event) -> None:
        # Clicks on the backdrop go nowhere: the window underneath waits.
        event.accept()
