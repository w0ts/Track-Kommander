"""NoticeBanner — dismissible warning strip shown above the main view.

Displays the :class:`core.health.Notice` list produced by the periodic health
check, plus one-off transient notices (e.g. "load onto deck B failed: …").
Pure Qt widgets, no platform code — works identically on macOS and Windows.

Dismissal is per notice ``key``: a dismissed notice stays hidden while its
condition persists, and re-arms once the condition clears (so it can warn
again if the problem comes back later).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.health import Notice
from ui.style import T

_log = logging.getLogger(__name__)

_TRANSIENT_MS = 8000  # how long a transient (one-off) notice stays visible

_SEVERITY_BG = {
    "info": "#15283a",  # dark blue — news, not a problem
    "warning": "#3a2f14",  # dark amber
    "error": "#3a1a17",  # dark red
}
_SEVERITY_FG = {
    "info": "#64b5f6",
    "warning": T.ORANGE,
    "error": "#ef5350",
}
_SEVERITY_ICON = {"info": "↑", "warning": "⚠", "error": "⚠"}


class _NoticeRow(QWidget):
    """One banner line: icon, message, optional action button, dismiss ✕."""

    dismissed = Signal(str)  # notice key
    action_clicked = Signal(str)  # action identifier

    def __init__(self, notice: Notice, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._key = notice.key
        bg = _SEVERITY_BG.get(notice.severity, _SEVERITY_BG["warning"])
        fg = _SEVERITY_FG.get(notice.severity, _SEVERITY_FG["warning"])
        self.setStyleSheet(f"background: {bg}; border-radius: 4px;")

        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 6, 6, 6)
        lay.setSpacing(8)

        icon = QLabel(_SEVERITY_ICON.get(notice.severity, "⚠"))
        icon.setStyleSheet(f"color: {fg}; font-size: 14px; background: transparent;")
        lay.addWidget(icon)

        text = QLabel(notice.text)
        text.setWordWrap(True)
        text.setStyleSheet(f"color: {T.TEXT}; background: transparent;")
        lay.addWidget(text, stretch=1)

        if notice.action and notice.action_label:
            btn = QPushButton(notice.action_label)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet(
                f"QPushButton {{ color: {fg}; background: transparent; "
                f"border: 1px solid {fg}; border-radius: 3px; padding: 3px 10px; }}"
                f"QPushButton:hover {{ background: {bg}; }}"
            )
            btn.clicked.connect(lambda _=False, a=notice.action: self.action_clicked.emit(a))
            lay.addWidget(btn)

        close = QPushButton("✕")
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.setFixedSize(22, 22)
        close.setStyleSheet(
            f"QPushButton {{ color: {T.TEXT_DIM}; background: transparent; "
            f"border: none; }} QPushButton:hover {{ color: {T.TEXT}; }}"
        )
        close.clicked.connect(lambda: self.dismissed.emit(self._key))
        lay.addWidget(close)


class NoticeBanner(QWidget):
    """Stack of notice rows. Hidden entirely when there is nothing to show."""

    def __init__(
        self,
        on_action: Callable[[str], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._on_action = on_action
        self._dismissed: set[str] = set()
        self._current: dict[str, Notice] = {}
        self._transient_keys: set[str] = set()
        self._transient_seq = 0

        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(8, 8, 8, 0)
        self._lay.setSpacing(4)
        self.hide()

    # ── public API ────────────────────────────────────────────────────────────

    def set_notices(self, notices: Sequence[Notice]) -> None:
        """Replace the set of persistent notices (transients are kept)."""
        incoming = {n.key: n for n in notices}
        transients = {k: n for k, n in self._current.items() if k in self._transient_keys}
        merged = {**incoming, **transients}
        armed = self._dismissed & set(incoming)  # re-arm cleared conditions
        if merged == self._current and armed == self._dismissed:
            return  # nothing changed — don't churn widgets every timer tick
        self._dismissed = armed
        self._current = merged
        self._rebuild()

    def show_transient(
        self,
        text: str,
        severity: str = "warning",
        msecs: int = _TRANSIENT_MS,
    ) -> None:
        """Show a one-off notice that auto-hides after *msecs*."""
        self._transient_seq += 1
        key = f"transient-{self._transient_seq}"
        self._transient_keys.add(key)
        self._current[key] = Notice(key=key, severity=severity, text=text)
        self._rebuild()

        def _expire_safe() -> None:
            try:
                self._expire(key)
            except RuntimeError:
                pass  # widget already deleted (app shutting down)

        QTimer.singleShot(msecs, _expire_safe)

    # ── internals ─────────────────────────────────────────────────────────────

    def _expire(self, key: str) -> None:
        self._transient_keys.discard(key)
        if key in self._current:
            self._current = {k: n for k, n in self._current.items() if k != key}
            self._rebuild()

    def _on_dismissed(self, key: str) -> None:
        if key in self._transient_keys:
            self._expire(key)
            return
        self._dismissed.add(key)
        self._rebuild()

    def _handle_action(self, action: str) -> None:
        if self._on_action is not None:
            try:
                self._on_action(action)
            except Exception:
                _log.exception("notice action %r failed", action)

    def _rebuild(self) -> None:
        while self._lay.count():
            item = self._lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        visible = [n for k, n in self._current.items() if k not in self._dismissed]
        for notice in visible:
            row = _NoticeRow(notice, self)
            row.dismissed.connect(self._on_dismissed)
            row.action_clicked.connect(self._handle_action)
            self._lay.addWidget(row)

        self.setVisible(bool(visible))
