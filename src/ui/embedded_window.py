"""Embedded Track Kommander — the window for the injected-Traktor variant.

PRIVATE / Mac-only / branch ``feat/traktor-native-re``. This is the separate
"embedded" app (see ``traktor-native/notes/embedded-ui-plan.md``): a thin
overlay that lives beside Traktor — which the injected ``libtk`` dylib drives —
reusing TK's ``core/`` and ``workers/``. The shipped external TK is untouched.

This first increment proves the bridge end-to-end: it starts the loopback
webhook the dylib POSTs to, shows the native now-playing it receives, and
raises itself when the dylib's opener asks (``/show``). The matching-cards
overlay and the native-column injection come next.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from core.constants import WEBHOOK_PORT
from ui.embedded_overlay import CardsOverlay
from workers.webhook import WebhookWatcher

if TYPE_CHECKING:
    from PySide6.QtGui import QCloseEvent

#: How often the cards strip re-pins itself over Traktor (ms), matching the
#: cadence of the main app's layout watch.
_RESNAP_MS = 2000

log = logging.getLogger(__name__)

_DECKS = ("A", "B", "C", "D")


class EmbeddedWindow(QWidget):
    """A small always-on-top overlay beside Traktor, fed by ``libtk`` over loopback."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Track Kommander (embedded)")
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setMinimumWidth(320)

        self._heading = QLabel("Waiting for Traktor…")
        self._heading.setStyleSheet("font-weight: 600; padding-bottom: 2px;")
        self._decks_lbl = QLabel("")
        self._decks_lbl.setTextFormat(Qt.TextFormat.RichText)
        self._decks_lbl.setWordWrap(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)
        layout.addWidget(self._heading)
        layout.addWidget(self._decks_lbl)

        # Per-deck native metadata (deck letter → info dict), last value wins.
        self._deck_info: dict[str, dict] = {}
        # Created by start(), not __init__, so the window can be built (and
        # unit-tested) without binding the loopback port, spawning a thread, or
        # capturing Traktor.
        self._watcher: WebhookWatcher | None = None
        self._cards: CardsOverlay | None = None
        self._resnap: QTimer | None = None

    def start(self) -> None:
        """Begin listening for the dylib's loopback POSTs and show the cards
        strip over Traktor. Call once, after show()."""
        if self._watcher is not None:
            return
        self._watcher = WebhookWatcher(port=WEBHOOK_PORT)
        self._watcher.native_track.connect(self._on_native_track)
        self._watcher.show_requested.connect(self._on_show_requested)
        self._watcher.deck_loaded.connect(self._on_deck_loaded)
        self._watcher.start()
        log.info("embedded: webhook started on 127.0.0.1:%d", WEBHOOK_PORT)

        # The matching-cards strip, pinned over the top of Traktor's list and
        # re-pinned periodically (Traktor can be moved/resized, its layout
        # toggled). snap() is a safe no-op when Traktor can't be captured yet.
        self._cards = CardsOverlay()
        self._cards.show()
        self._cards.snap()
        self._resnap = QTimer(self)
        self._resnap.setInterval(_RESNAP_MS)
        self._resnap.timeout.connect(self._cards.snap)
        self._resnap.start()

    # ── bridge events ────────────────────────────────────────────────────────

    def _on_native_track(self, info: dict) -> None:
        """A track libtk read from Traktor's C++ (native load / selection)."""
        deck = str(info.get("deck", "")).upper()
        if deck in _DECKS:
            self._deck_info[deck] = dict(info)
        self._refresh()

    def _on_deck_loaded(self, deck: str, info: dict) -> None:
        """The QML D2 bridge's deckLoaded, if that path is also live."""
        deck = (deck or "").upper()
        if deck in _DECKS:
            self._deck_info[deck] = dict(info)
        self._refresh()

    def _on_show_requested(self) -> None:
        """libtk's opener: the user clicked our entry in Traktor's tree."""
        self.showNormal()
        self.raise_()
        self.activateWindow()
        if self._cards is not None:
            self._cards.snap()

    # ── rendering ────────────────────────────────────────────────────────────

    def _refresh(self) -> None:
        rows: list[str] = []
        for deck in _DECKS:
            info = self._deck_info.get(deck)
            if not info:
                continue
            title = str(info.get("title") or "—")
            artist = str(info.get("artist") or "")
            try:
                bpm = float(info.get("bpm") or 0.0)
            except (TypeError, ValueError):
                bpm = 0.0
            who = f"{artist} — {title}" if artist else title
            rows.append(
                f"<b>{deck}</b>&nbsp;&nbsp;{who}"
                f"&nbsp;&nbsp;<span style='color:#888'>{bpm:.1f}</span>"
            )
        self._decks_lbl.setText("<br>".join(rows))
        self._heading.setText("Track Kommander — embedded" if rows else "Waiting for Traktor…")

    # ── teardown ─────────────────────────────────────────────────────────────

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 (Qt override)
        if self._resnap is not None:
            self._resnap.stop()
        if self._cards is not None:
            self._cards.close()
        if self._watcher is not None:
            try:
                self._watcher.stop()
            except Exception:
                log.exception("embedded: stopping the webhook failed")
        super().closeEvent(event)
