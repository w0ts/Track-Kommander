"""Tests for result-table tooltip lifetime and dismiss-on-move.

The row tooltip is a large rich-text block (metric bars + cover art). Qt sizes
a tooltip's auto-hide timer from its text length, so one this big effectively
never goes away — it sits over the results whenever the pointer stops, which
is constant when the app is driven from the S4 rather than the mouse.

``ResultsTable`` therefore shows tooltips itself with an explicit lifetime and
hides them as soon as the pointer actually moves. ``QToolTip`` is patched out
so the assertions don't depend on a real tooltip window (there is none under
the offscreen platform plugin).
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6.QtWidgets")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QHelpEvent, QMouseEvent
from PySide6.QtWidgets import QApplication, QStyle, QTableWidgetItem

import ui.widgets as widgets

TIP = "<b>Some track</b><br>a very long rich-text tooltip …"


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


class _FakeToolTip:
    """Records showText / hideText instead of drawing a tooltip window."""

    def __init__(self) -> None:
        self.shown: list[tuple[str, int]] = []
        self.hides = 0

    def showText(self, _pos, text, _widget=None, _rect=None, msecs=-1) -> None:
        self.shown.append((text, msecs))

    def hideText(self) -> None:
        self.hides += 1


@pytest.fixture
def tip(monkeypatch):
    fake = _FakeToolTip()
    monkeypatch.setattr(widgets, "QToolTip", fake)
    return fake


@pytest.fixture
def table(qapp):
    t = widgets.ResultsTable(1, 1)
    item = QTableWidgetItem("Track")
    item.setToolTip(TIP)
    t.setItem(0, 0, item)
    t.resize(400, 200)
    yield t
    t.deleteLater()


def _hover(table, x: int = 20, y: int = 10, *, rest: bool = True) -> None:
    """Qt's ToolTip event at (x, y); with *rest*, the show delay runs out too."""
    pos = QPoint(x, y)
    ev = QHelpEvent(QEvent.Type.ToolTip, pos, table.viewport().mapToGlobal(pos))
    table.viewportEvent(ev)
    if rest:
        _rest(table)


def _rest(table) -> None:
    """The pointer stayed put for the rest of the show delay."""
    if table._pending_timer.isActive():
        table._pending_timer.stop()
        table._show_pending_tip()


def _move(table, x: int, y: int) -> None:
    pos = QPointF(x, y)
    ev = QMouseEvent(
        QEvent.Type.MouseMove,
        pos,
        table.viewport().mapToGlobal(pos.toPoint()),
        Qt.MouseButton.NoButton,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )
    table.viewportEvent(ev)


# ── Lifetime ────────────────────────────────────────────────────────────────


def test_tooltip_shown_with_explicit_lifetime(table, tip) -> None:
    table.set_tooltip_duration(5000)
    _hover(table)

    assert len(tip.shown) == 1
    text, msecs = tip.shown[0]
    assert text == TIP
    assert msecs == 5000, "lifetime must be passed explicitly, not left to Qt"


def test_default_lifetime_is_bounded(table, tip) -> None:
    _hover(table)
    assert tip.shown[0][1] == widgets.TOOLTIP_MS_DEFAULT
    assert widgets.TOOLTIP_MS_DEFAULT <= widgets.TOOLTIP_MS_MAX


def test_duration_is_capped(table) -> None:
    table.set_tooltip_duration(10 * widgets.TOOLTIP_MS_MAX)
    assert table._tooltip_ms == widgets.TOOLTIP_MS_MAX


def test_zero_duration_disables_tooltips(table, tip) -> None:
    table.set_tooltip_duration(0)
    _hover(table)
    assert tip.shown == []


# ── Dismiss on movement ─────────────────────────────────────────────────────


def test_pointer_movement_hides_the_tooltip(table, tip) -> None:
    _hover(table, 20, 10)
    assert tip.hides == 0

    _move(table, 120, 60)
    assert tip.hides == 1, "tooltip must go away as soon as the pointer moves"


def test_jitter_does_not_hide_the_tooltip(table, tip) -> None:
    _hover(table, 20, 10)
    _move(table, 21, 11)  # sub-threshold tremor
    assert tip.hides == 0


def test_leave_hides_the_tooltip(table, tip) -> None:
    _hover(table)
    table.viewportEvent(QEvent(QEvent.Type.Leave))
    assert tip.hides == 1


def test_move_without_a_visible_tooltip_is_a_noop(table, tip) -> None:
    _move(table, 300, 90)
    assert tip.hides == 0


def test_hover_outside_any_row_hides_the_tooltip(table, tip) -> None:
    _hover(table, 20, 10)
    _hover(table, 20, 180)  # below the single populated row
    assert tip.hides == 1


# ── Show delay ──────────────────────────────────────────────────────────────


def test_nothing_shows_before_the_delay(table, tip) -> None:
    """A pause on the way across the list must not pop the tooltip up."""
    _hover(table, rest=False)
    assert tip.shown == []
    assert table._pending_timer.isActive()


def test_the_delay_is_the_total_rest_time(table, tip) -> None:
    wake = QApplication.style().styleHint(QStyle.StyleHint.SH_ToolTip_WakeUpDelay)
    _hover(table, rest=False)
    assert table._pending_timer.interval() == max(0, widgets.TOOLTIP_SHOW_DELAY_MS - wake)
    assert widgets.TOOLTIP_SHOW_DELAY_MS >= 1500


def test_moving_during_the_delay_cancels_it(table, tip) -> None:
    _hover(table, 20, 10, rest=False)
    _move(table, 120, 60)
    assert not table._pending_timer.isActive()
    _rest(table)
    assert tip.shown == []


def test_jitter_during_the_delay_keeps_it(table, tip) -> None:
    _hover(table, 20, 10, rest=False)
    _move(table, 21, 11)
    _rest(table)
    assert len(tip.shown) == 1


def test_the_text_is_built_only_when_the_tip_shows(table, tip) -> None:
    """A row tooltip can extract cover art; a pointer passing by must not pay."""
    asked: list[int] = []
    table.row_tooltip_provider = lambda row: asked.append(row) or TIP
    _hover(table, 20, 10, rest=False)
    assert asked == []
    _move(table, 120, 60)
    _rest(table)
    assert asked == [], "the pointer moved on: nothing to build"

    _hover(table, 20, 10)
    assert asked == [0]
    assert tip.shown == [(TIP, widgets.TOOLTIP_MS_DEFAULT)]


def test_leaving_during_the_delay_cancels_it(table, tip) -> None:
    _hover(table, rest=False)
    table.viewportEvent(QEvent(QEvent.Type.Leave))
    _rest(table)
    assert tip.shown == []
