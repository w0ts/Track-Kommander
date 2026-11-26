"""Screenshot-based calibration of Traktor deck drop zones.

The user sees a captured frame of the Traktor window and clicks each deck's
drop target. The clicks are stored as normalised offsets (relative to the
window bounds) so the calibration survives Traktor being moved or resized.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSettings, Qt, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

DECKS: tuple[str, ...] = ("A", "B", "C", "D")


class _DeckImageView(QWidget):
    """Displays the screenshot and lets the user click each deck centre."""

    deckPlaced = Signal(str)  # emitted with the deck letter just placed

    def __init__(self, image: QImage, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._image = image
        self._points: dict[str, QPointF] = {}  # in image (pixel) space
        self._active_deck: str = DECKS[0]
        self.setMouseTracking(False)
        # Keep the dialog within reasonable bounds
        max_w, max_h = 1280, 800
        scale = min(max_w / max(image.width(), 1), max_h / max(image.height(), 1), 1.0)
        self._display_size = (
            int(image.width() * scale),
            int(image.height() * scale),
        )
        self.setFixedSize(*self._display_size)
        # Scaled once to the size it is shown at (in device pixels, so it
        # stays sharp on a Retina screen) rather than on every repaint — and
        # a markers-only repaint happens on every click.
        dpr = self.devicePixelRatioF()
        self._pixmap = QPixmap.fromImage(
            image.scaled(
                max(1, round(self._display_size[0] * dpr)),
                max(1, round(self._display_size[1] * dpr)),
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
        self._pixmap.setDevicePixelRatio(dpr)

    # ── public API ────────────────────────────────────────────────────────────

    def set_active_deck(self, deck: str) -> None:
        self._active_deck = deck
        self.update()

    def points(self) -> dict[str, QPointF]:
        """Return the click positions in image-pixel space."""
        return dict(self._points)

    def set_points(self, points: dict[str, QPointF]) -> None:
        """Replace all click positions at once (used by auto-detect)."""
        self._points = {d: QPointF(p) for d, p in points.items()}
        # Activate the first un-set deck (or the last deck if all are set).
        for d in DECKS:
            if d not in self._points:
                self._active_deck = d
                break
        else:
            self._active_deck = DECKS[-1]
        self.update()

    def image(self) -> QImage:
        return self._image

    def image_size(self) -> tuple[int, int]:
        return self._image.width(), self._image.height()

    def clear(self) -> None:
        self._points.clear()
        self.update()

    # ── events ────────────────────────────────────────────────────────────────

    def mousePressEvent(self, ev) -> None:
        if ev.button() != Qt.MouseButton.LeftButton:
            return
        # Map widget coords back to image coords
        img_x = ev.position().x() * self._image.width() / self._display_size[0]
        img_y = ev.position().y() * self._image.height() / self._display_size[1]
        placed = self._active_deck
        self._points[placed] = QPointF(img_x, img_y)
        # Auto-advance to next un-set deck
        for d in DECKS:
            if d not in self._points:
                self._active_deck = d
                break
        self.update()
        self.deckPlaced.emit(placed)

    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        p.drawPixmap(0, 0, self._pixmap)

        sx = self._display_size[0] / max(self._image.width(), 1)
        sy = self._display_size[1] / max(self._image.height(), 1)

        for deck, pt in self._points.items():
            self._draw_marker(
                p, QPointF(pt.x() * sx, pt.y() * sy), deck, active=(deck == self._active_deck)
            )

        # Hint label for the active deck if not yet placed
        if self._active_deck not in self._points:
            p.setPen(QPen(QColor("#bdf164")))
            f = p.font()
            f.setPointSize(14)
            f.setBold(True)
            p.setFont(f)
            p.drawText(
                QRectF(0, 0, self.width(), 28),
                Qt.AlignmentFlag.AlignCenter,
                f"Click on Deck {self._active_deck} drop zone",
            )
        p.end()

    @staticmethod
    def _draw_marker(p: QPainter, pos: QPointF, label: str, *, active: bool) -> None:
        radius = 14
        color = QColor("#bdf164") if active else QColor("#f4a642")
        p.setPen(QPen(color, 2))
        p.setBrush(QColor(0, 0, 0, 120))
        p.drawEllipse(pos, radius, radius)
        f = QFont()
        f.setPointSize(11)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QPen(color))
        p.drawText(
            QRectF(pos.x() - radius, pos.y() - radius, radius * 2, radius * 2),
            Qt.AlignmentFlag.AlignCenter,
            label,
        )


class DeckCalibrationDialog(QDialog):
    """Modal dialog that lets the user pin the four deck drop zones on a
    screenshot of Traktor.
    """

    def __init__(
        self,
        image: QImage,
        bounds: tuple[float, float, float, float],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Calibrate Deck Drop Zones")
        self._bounds = bounds  # screen-space (x, y, w, h)
        self._view = _DeckImageView(image, self)

        # Header — short instruction
        header = QLabel(
            "Click each deck's drop zone (the track-name area at the top of "
            "the deck). The active deck is highlighted in green."
        )
        header.setWordWrap(True)

        # Per-deck status row
        self._deck_buttons: dict[str, QPushButton] = {}
        deck_row = QHBoxLayout()
        deck_row.setSpacing(8)
        for d in DECKS:
            btn = QPushButton(f"Deck {d}")
            btn.setCheckable(True)
            btn.setFixedWidth(90)
            btn.clicked.connect(lambda _checked, deck=d: self._select_deck(deck))
            deck_row.addWidget(btn)
            self._deck_buttons[d] = btn
        deck_row.addStretch()

        auto_btn = QPushButton("🪄 Auto-detect")
        auto_btn.setFixedWidth(140)
        auto_btn.setToolTip(
            "Find each deck's blue letter (A / B / C / D) automatically.\n"
            "Detected positions appear as markers; you can adjust any of\n"
            "them by clicking again before saving."
        )
        auto_btn.clicked.connect(self._on_auto_detect)
        deck_row.addWidget(auto_btn)

        clear_btn = QPushButton("Clear all")
        clear_btn.clicked.connect(self._on_clear)
        deck_row.addWidget(clear_btn)

        # Buttons — Enter triggers Save, Escape triggers Cancel.
        from ui.dialogs import wire_dialog_keys

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)
        self._save_btn = buttons.button(QDialogButtonBox.StandardButton.Save)
        self._save_btn.setEnabled(False)
        wire_dialog_keys(self, buttons)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.addWidget(header)
        layout.addLayout(deck_row)
        layout.addWidget(self._view, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(buttons)

        self._select_deck(DECKS[0])
        self._view.deckPlaced.connect(self._on_deck_placed)

    # ── slots ─────────────────────────────────────────────────────────────────

    def _select_deck(self, deck: str) -> None:
        for d, btn in self._deck_buttons.items():
            btn.setChecked(d == deck)
        self._view.set_active_deck(deck)

    def _on_clear(self) -> None:
        self._view.clear()
        self._select_deck(DECKS[0])
        self._refresh_save_state()

    def _on_auto_detect(self) -> None:
        """Run blue-letter detection and pre-fill the markers."""
        from PySide6.QtCore import QPointF

        from core.drag import detect_deck_letters

        found = detect_deck_letters(self._view.image())
        if not found:
            from ui.dialogs import warn

            warn(
                self,
                "Auto-detect found nothing",
                "Could not locate the blue deck letters in the screenshot.\n"
                "Try clicking each deck's drop zone manually instead.",
            )
            return

        # Merge with any user-placed markers — auto-detect overwrites only
        # the decks it actually found, so a previous manual fix isn't lost.
        merged = self._view.points()
        for deck, (x, y) in found.items():
            merged[deck] = QPointF(float(x), float(y))
        self._view.set_points(merged)

        # Sync the deck-button selection with the view's active deck.
        for d, btn in self._deck_buttons.items():
            btn.setChecked(d == self._view._active_deck)
        self._refresh_save_state()

    def _refresh_save_state(self) -> None:
        self._save_btn.setEnabled(len(self._view.points()) == len(DECKS))

    def _on_save(self) -> None:
        pts = self._view.points()
        if len(pts) != len(DECKS):
            return
        s = QSettings()
        # CGImage was captured at nominal resolution, so image-pixel offsets
        # divided by image size give normalised offsets that map cleanly onto
        # the screen-space window bounds at drag time.
        img_w_raw, img_h_raw = self._view.image_size()
        img_w = max(img_w_raw, 1)
        img_h = max(img_h_raw, 1)
        for deck, pt in pts.items():
            s.setValue(f"drag_deck_{deck}_rx", float(pt.x()) / img_w)
            s.setValue(f"drag_deck_{deck}_ry", float(pt.y()) / img_h)
        # Stale absolute Y values from a previous monitor break things if the
        # window has since moved. Now that we have per-deck normalised offsets
        # the legacy keys are dead weight — drop them.
        for legacy in ("drag_deck_y_top", "drag_deck_y_bottom"):
            s.remove(legacy)
        self.accept()

    def _on_deck_placed(self, _deck: str) -> None:
        self._refresh_save_state()
        for d in DECKS:
            if d not in self._view.points():
                self._select_deck(d)
                return
        self._select_deck(DECKS[-1])


def open_deck_calibration(parent: QWidget | None = None) -> bool | None:
    """Capture Traktor and open the calibration dialog.

    Returns ``True`` if the user saved, ``False`` if cancelled, or ``None`` if
    Traktor could not be captured (caller should surface a message).
    """
    from core.drag import capture_traktor_window  # local import: avoids cycles

    captured = capture_traktor_window()
    if captured is None:
        return None
    image, bounds = captured
    dlg = DeckCalibrationDialog(image, bounds, parent)
    try:
        return dlg.exec() == QDialog.DialogCode.Accepted
    finally:
        # A child of *parent*, holding the full-window screenshot: without
        # this every calibration run left one behind for the app's life.
        dlg.deleteLater()
