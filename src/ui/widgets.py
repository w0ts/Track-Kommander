"""Shared custom widgets."""

from __future__ import annotations

import logging
import sys
import time
from collections.abc import Callable

from PySide6.QtCore import QEvent, QMimeData, QRect, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QBrush, QColor, QDrag, QLinearGradient, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QListWidget,
    QStyle,
    QStyledItemDelegate,
    QTableWidget,
    QToolTip,
)

from ui.style import T

_log = logging.getLogger(__name__)

# The result-row tooltip is a large rich-text block (bars + cover art). Qt
# derives a tooltip's auto-hide delay from its text length, so an HTML tooltip
# this size effectively never disappears — it parks itself over the results
# while the pointer sits still, which is exactly what happens when the app is
# driven from the S4 rather than the mouse. Both of these are therefore
# enforced by hand: a hard lifetime, and dismissal on any real pointer motion.
TOOLTIP_MS_DEFAULT = 8_000  # hard lifetime for a result tooltip
TOOLTIP_MS_MAX = 30_000  # ceiling for a user-configured lifetime
_TIP_JITTER_PX = 3  # movement under this doesn't count as "moved"
#: How long the pointer must rest on a row before its tooltip shows. Qt's own
#: wake-up (~0.7 s) had the large score tooltip popping up whenever the
#: pointer paused on its way across the list.
TOOLTIP_SHOW_DELAY_MS = 1_500

# Custom item-data roles carrying the composite-score fill for a lead cell.
FILL_COLOR_ROLE = int(Qt.ItemDataRole.UserRole) + 50  # QColor (darkened base)
FILL_FRAC_ROLE = int(Qt.ItemDataRole.UserRole) + 51  # 0–1: how much of the bar
#   spanning all lead cells is filled. Pixels are worked out at paint time from
#   the current column widths, so resizing a column never leaves a stale bar.


def span_segment(widths: list[int], pos: int, frac: float) -> tuple[float, float]:
    """(offset_px, fill_px) of the cell at *pos* within a bar spanning *widths*.

    offset_px is the distance from the bar's start to this cell's left edge;
    fill_px the total filled length of the bar across all its cells.
    """
    total = sum(max(1, w) for w in widths)
    offset = sum(max(1, w) for w in widths[:pos])
    return float(offset), max(0.0, min(1.0, frac)) * total


class ScoreBarDelegate(QStyledItemDelegate):
    """Paints a horizontal composite-score fill behind a cell's text.

    The app stylesheet defines a ``QTableWidget::item`` rule, which makes Qt's
    stylesheet style own item painting and silently drop per-item background
    brushes (``QTableWidgetItem.setBackground``). This delegate restores the
    fill by painting it — and the row/selection background and text — itself,
    so the composite bar is visible again across the ARTIST·ALBUM·TITLE cells.

    The fill is a single gradient that spans all three cells continuously
    (using each cell's pixel offset within the bar): transparent at the bar's
    start, building to a dark tint at the score position, so the leading edge
    marks the score and it reads as a soft background wash, not a solid block.
    """

    _ALT_BG = QColor("#141618")  # matches stylesheet alternate-background-color
    _BASE_BG = QColor("#111214")  # matches T.TABLE_BG
    _SELECTION = QColor(T.SELECTION)
    _TEXT = QColor("#ced2d8")  # matches T.TEXT
    _PAD = 8  # matches stylesheet ::item horizontal padding
    _FILL_ALPHA = 150  # peak alpha at the score edge (fades to 0 at the start)

    def __init__(self, view, span_columns: tuple[int, ...] = ()):
        super().__init__(view)
        self._view = view
        self._span: tuple[int, ...] = tuple(span_columns)

    def set_span_columns(self, columns) -> None:
        """The columns (in order) the one continuous score bar runs across."""
        self._span = tuple(columns)

    def _segment(self, column: int, frac: float) -> tuple[float, float] | None:
        if column not in self._span:
            return None
        widths = [self._view.columnWidth(c) for c in self._span]
        return span_segment(widths, self._span.index(column), frac)

    def paint(self, painter, option, index):  # type: ignore[override]
        painter.save()
        selected = bool(option.state & QStyle.StateFlag.State_Selected)

        # Row / selection background (the stylesheet would otherwise draw this).
        if selected:
            bg = self._SELECTION
        elif index.row() % 2:
            bg = self._ALT_BG
        else:
            bg = self._BASE_BG
        painter.fillRect(option.rect, bg)

        # Composite-score fill: a gradient continuous across the lead cells,
        # clipped to the portion of this cell that lies within the bar length.
        if not selected:
            color = index.data(FILL_COLOR_ROLE)
            frac = index.data(FILL_FRAC_ROLE)
            geo = (
                self._segment(index.column(), float(frac))
                if color is not None and frac is not None
                else None
            )
            if geo is not None:
                offset_px, fill_px = float(geo[0]), float(geo[1])
                r = option.rect
                local_w = min(float(r.width()), fill_px - offset_px)
                if local_w > 0 and fill_px > 0:
                    x_start = r.x() - offset_px  # virtual bar start (fades to transparent)
                    x_end = x_start + fill_px  # score position (peak tint)
                    grad = QLinearGradient(x_start, 0.0, x_end, 0.0)
                    c0 = QColor(color)
                    c0.setAlpha(0)
                    c1 = QColor(color)
                    c1.setAlpha(self._FILL_ALPHA)
                    grad.setColorAt(0.0, c0)
                    grad.setColorAt(1.0, c1)
                    sub = QRect(r.x(), r.y(), int(round(local_w)), r.height())
                    painter.fillRect(sub, QBrush(grad))

        # Text on top.
        text = index.data(Qt.ItemDataRole.DisplayRole)
        if text:
            if selected:
                painter.setPen(QColor("#ffffff"))
            else:
                fg = index.data(Qt.ItemDataRole.ForegroundRole)
                if isinstance(fg, QBrush):
                    painter.setPen(fg.color())
                elif isinstance(fg, QColor):
                    painter.setPen(fg)
                else:
                    painter.setPen(self._TEXT)
            painter.setFont(option.font)
            align = index.data(Qt.ItemDataRole.TextAlignmentRole)
            flags = (
                int(align)
                if align
                else int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            )
            tr = option.rect.adjusted(self._PAD, 0, -self._PAD, 0)
            elided = painter.fontMetrics().elidedText(
                str(text), Qt.TextElideMode.ElideRight, tr.width()
            )
            painter.drawText(tr, flags, elided)
        painter.restore()


# Custom item-data roles for metric cells painted by MetricCellDelegate.
BAR_ROLE = int(Qt.ItemDataRole.UserRole) + 52  # (fraction 0–1, QColor)
PARTS_ROLE = int(Qt.ItemDataRole.UserRole) + 53  # [(text, QColor), …] joined by " · "


class MetricCellDelegate(QStyledItemDelegate):
    """Paints metric fill bars and multi-colour genre labels from item data.

    These used to be one widget (plus a layout and child frames) per cell;
    building ~500 of them per results page was the main UI lag the watchdog
    found (250–500 ms on every refresh). Items + painting cost next to nothing,
    and plain items keep the table's own tooltip and hover handling.

    Cells without :data:`BAR_ROLE` / :data:`PARTS_ROLE` paint as usual.
    """

    _BAR_H = 10
    _BAR_MARGIN = 6
    _BAR_RADIUS = 3.0
    _PAD = 4
    _SEP = " · "
    _SEP_COLOR = QColor(120, 130, 160, 178)

    def paint(self, painter, option, index):  # type: ignore[override]
        super().paint(painter, option, index)  # background / selection
        bar = index.data(BAR_ROLE)
        if bar:
            self._paint_bar(painter, option.rect, float(bar[0]), bar[1])
            return
        parts = index.data(PARTS_ROLE)
        if parts:
            self._paint_parts(painter, option, parts)

    def _paint_bar(self, painter, rect: QRect, frac: float, color) -> None:
        avail = rect.width() - 2 * self._BAR_MARGIN
        w = round(max(0.0, min(1.0, frac)) * avail)
        if w <= 0:
            return
        y = rect.y() + (rect.height() - self._BAR_H) // 2
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(color))
        painter.drawRoundedRect(
            QRect(rect.x() + self._BAR_MARGIN, y, w, self._BAR_H),
            self._BAR_RADIUS,
            self._BAR_RADIUS,
        )
        painter.restore()

    def _paint_parts(self, painter, option, parts) -> None:
        painter.save()
        painter.setFont(option.font)
        fm = painter.fontMetrics()
        r = option.rect.adjusted(self._PAD + 4, 0, -self._PAD, 0)
        x, right = r.x(), r.right()
        flags = int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        for i, (text, color) in enumerate(parts):
            pieces = ([(self._SEP, self._SEP_COLOR)] if i else []) + [(text, color)]
            for piece, col in pieces:
                if x >= right:
                    painter.restore()
                    return
                shown = fm.elidedText(piece, Qt.TextElideMode.ElideRight, right - x)
                painter.setPen(QColor(col))
                painter.drawText(QRect(x, r.y(), right - x, r.height()), flags, shown)
                x += fm.horizontalAdvance(shown)
        painter.restore()


class ResultsTable(QTableWidget):
    """QTableWidget that lets users drag rows as file URLs into Traktor's decks."""

    # The table changed size. Rows load lazily in pages; a taller table can
    # show more than has been loaded, and with no scrollbar to drag there is
    # no other cue to load the rest.
    resized = Signal()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._row_paths: dict[int, str] = {}  # row index → absolute file path
        # Builds a row's tooltip on hover (row index → text). Building every
        # row's rich tooltip up front, cover art included, slowed each refresh.
        self.row_tooltip_provider = None
        self._tooltip_ms: int = TOOLTIP_MS_DEFAULT
        self._tip_origin = None  # QPoint (viewport coords) where the tip was shown
        # A tooltip Qt has asked for but that is still waiting out the delay:
        # (source, global_pos, build, rect). *build* makes the text — only
        # once the delay is over, because a row's tooltip may extract cover
        # art, and a pointer that moves on in the meantime should not pay.
        self._pending_tip: tuple | None = None
        self._pending_timer = QTimer(self)
        self._pending_timer.setSingleShot(True)
        self._pending_timer.timeout.connect(self._show_pending_tip)
        self.viewport().setMouseTracking(True)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.resized.emit()

    # ── Tooltips ────────────────────────────────────────────────────────────

    def set_tooltip_duration(self, ms: int) -> None:
        """Set the tooltip lifetime in ms. 0 (or less) disables tooltips."""
        self._tooltip_ms = min(int(ms), TOOLTIP_MS_MAX)

    def _hide_tip(self) -> None:
        self._cancel_pending_tip()
        if self._tip_origin is not None:
            self._tip_origin = None
            QToolTip.hideText()

    def _schedule_tip(
        self, source, global_pos, build: Callable[[], str], rect: QRect | None = None
    ) -> None:
        """Show the tooltip once the pointer has rested long enough.

        Qt sends the ToolTip event after its own wake-up delay; this waits out
        the rest of :data:`TOOLTIP_SHOW_DELAY_MS` on top of it. *build* is
        called for the text only then.
        """
        wake = QApplication.style().styleHint(QStyle.StyleHint.SH_ToolTip_WakeUpDelay)
        extra = max(0, TOOLTIP_SHOW_DELAY_MS - int(wake))
        self._pending_tip = (source, global_pos, build, rect)
        self._pending_timer.start(extra)

    def _cancel_pending_tip(self) -> None:
        self._pending_timer.stop()
        self._pending_tip = None

    def _show_pending_tip(self) -> None:
        pending, self._pending_tip = self._pending_tip, None
        if pending is None:
            return
        source, global_pos, build, rect = pending
        text = build()
        self._show_tip(source, global_pos, str(text) if text else "", rect)

    def _row_tip_builder(self, row: int, column: int) -> Callable[[], str]:
        """What builds the tooltip for the cell at (*row*, *column*)."""
        provider = self.row_tooltip_provider
        if provider is not None:
            return lambda: provider(row)
        return lambda: self.model().index(row, column).data(Qt.ItemDataRole.ToolTipRole)

    def _moved_from_pending(self, global_pos) -> bool:
        if self._pending_tip is None:
            return False
        origin = self._pending_tip[1]
        return (
            abs(global_pos.x() - origin.x()) > _TIP_JITTER_PX
            or abs(global_pos.y() - origin.y()) > _TIP_JITTER_PX
        )

    @staticmethod
    def _global_pos(event):
        """Global QPoint for either a QHelpEvent or a QMouseEvent (Qt 6)."""
        # QMouseEvent keeps a deprecated globalPos() alongside globalPosition(),
        # so probe for the Qt 6 accessor first and fall back for QHelpEvent.
        if hasattr(event, "globalPosition"):
            return event.globalPosition().toPoint()
        return event.globalPos()

    def _show_tip(self, source, global_pos, text: str, rect: QRect | None = None) -> None:
        """Show *text* with an enforced lifetime, anchored at *global_pos*."""
        if self._tooltip_ms <= 0 or not text:
            self._hide_tip()
            return
        self._tip_origin = global_pos
        QToolTip.showText(
            global_pos, text, source, rect if rect is not None else QRect(), self._tooltip_ms
        )

    def _moved_away(self, global_pos) -> bool:
        """True once the pointer has travelled far enough to count as a move."""
        if self._tip_origin is None:
            return False
        return (
            abs(global_pos.x() - self._tip_origin.x()) > _TIP_JITTER_PX
            or abs(global_pos.y() - self._tip_origin.y()) > _TIP_JITTER_PX
        )

    def _handle_hover(self, event) -> bool:
        """Common motion/exit handling. True if the event was consumed."""
        t = event.type()
        if t == QEvent.Type.MouseMove:
            pos = self._global_pos(event)
            if self._moved_away(pos) or self._moved_from_pending(pos):
                self._hide_tip()
        elif t in (QEvent.Type.Leave, QEvent.Type.Wheel):
            self._hide_tip()
        return False

    def viewportEvent(self, event) -> bool:  # type: ignore[override]
        if event.type() == QEvent.Type.ToolTip:
            idx = self.indexAt(event.pos())
            if not idx.isValid():
                self._hide_tip()
                return True
            # The anchor rect keeps Qt from re-issuing ToolTip events while the
            # pointer stays inside the cell the tip belongs to.
            self._schedule_tip(
                self.viewport(),
                self._global_pos(event),
                self._row_tip_builder(idx.row(), idx.column()),
                self.visualRect(idx),
            )
            return True
        self._handle_hover(event)
        return super().viewportEvent(event)

    def startDrag(self, supported_actions) -> None:  # type: ignore[override]
        row = self.currentRow()
        path = self._row_paths.get(row)
        _log.debug("ResultsTable.startDrag: fired row=%s path=%r", row, path)
        if not path:
            _log.warning(
                "ResultsTable.startDrag: no path for current row %s — drag aborted "
                "(nothing will be dropped).",
                row,
            )
            return
        mime = QMimeData()
        url = QUrl.fromLocalFile(path)
        mime.setUrls([url])
        _log.debug(
            "ResultsTable.startDrag: mime urls=%s",
            [u.toString() for u in mime.urls()],
        )
        drag = QDrag(self)
        drag.setMimeData(mime)
        # A fully-transparent 1x1 pixmap suppresses the drag image that would
        # otherwise follow the cursor during the programmatic drag.
        blank = QPixmap(1, 1)
        blank.fill(Qt.GlobalColor.transparent)
        drag.setPixmap(blank)
        # Replace the per-action drag cursors too. Without this, Windows OLE
        # shows its copy ("+") cursor over a valid target and the "forbidden"
        # (no-entry) cursor everywhere else as the synthesized drag sweeps
        # across — a transparent cursor for every action keeps it silent.
        for action in (
            Qt.DropAction.CopyAction,
            Qt.DropAction.MoveAction,
            Qt.DropAction.LinkAction,
            Qt.DropAction.IgnoreAction,
        ):
            drag.setDragCursor(blank, action)

        # For SYNTHETIC drags only: block the operator's real mouse for the
        # duration so a stray hand / trackpad / S4 jog can't fight the
        # synthesized cursor and make the drop miss (our injected SendInput
        # events still pass through). NEVER for a manual drag — there the
        # operator's mouse IS the drag, and swallowing it wedges DoDragDrop
        # with the whole session's mouse dead (observed: 129 s until the app
        # was killed). A watchdog QTimer force-unhooks after 10 s regardless —
        # DoDragDrop pumps this thread's messages, so it fires even mid-drag.
        blocking = False
        synthetic = False
        if sys.platform == "win32":
            try:
                from PySide6.QtCore import QTimer

                from core import drag_win

                synthetic = drag_win.consume_synthetic_drag()
                if synthetic:
                    drag_win.block_physical_mouse(True)
                    blocking = True
                    QTimer.singleShot(10_000, lambda: drag_win.block_physical_mouse(False))
            except Exception:  # pragma: no cover
                _log.debug("startDrag: could not block physical mouse", exc_info=True)
        t0 = time.monotonic()
        try:
            if synthetic:
                # Release the do_drag worker's wait — the OLE loop is about to
                # be live, so the glide/drop can proceed without racing it.
                drag_win.notify_drag_started()
            # Offer every transfer action. Some targets answer a file-URL drop
            # with LINK or MOVE rather than COPY; offering only COPY makes OLE
            # resolve to no-effect and the drop is silently ignored. Nothing
            # move-deletes on our side, so MOVE is safe to offer.
            result = drag.exec(
                Qt.DropAction.CopyAction | Qt.DropAction.MoveAction | Qt.DropAction.LinkAction,
                Qt.DropAction.CopyAction,
            )
        finally:
            if blocking:
                try:
                    drag_win.block_physical_mouse(False)
                except Exception:  # pragma: no cover
                    _log.debug("startDrag: could not unblock physical mouse", exc_info=True)
        _log.debug(
            "ResultsTable.startDrag: QDrag.exec returned %r after %.0f ms (synthetic=%s)",
            result,
            (time.monotonic() - t0) * 1000,
            synthetic,
        )


class CheckList(QListWidget):
    """A checkable list where clicking anywhere on a row toggles it.

    Qt only toggles when the small indicator itself is hit, so clicking the
    label just selects the row and nothing appears to happen. Handling the
    press here makes the whole row the target — and because it replaces Qt's
    own handling rather than adding to it, a click on the indicator toggles
    once, not twice.
    """

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.position().toPoint()
            item = self.itemAt(pos)
            if item is not None and item.flags() & Qt.ItemFlag.ItemIsUserCheckable:
                item.setCheckState(
                    Qt.CheckState.Unchecked
                    if item.checkState() == Qt.CheckState.Checked
                    else Qt.CheckState.Checked
                )
                self.setCurrentItem(item)
                event.accept()
                return
        super().mousePressEvent(event)

    def set_all_checked(self, checked: bool) -> None:
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for i in range(self.count()):
            self.item(i).setCheckState(state)

    def checked_data(self) -> list:
        """UserRole data of every ticked row, in display order."""
        return [
            self.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self.count())
            if self.item(i).checkState() == Qt.CheckState.Checked
        ]
