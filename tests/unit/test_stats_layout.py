"""Flowed cards must not overlap their neighbours.

The distribution cards carried a fixed width while the flow layout placed
them at a width of its own choosing. At a wide window they were placed
closer together than they were drawn, so each card painted over the right
third of the one before it — hiding those bars and cutting the axis label
mid-character. Every feature whose values sit at the high end looked empty.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QWidget

from ui.now_playing import _FlowLayout


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


def _host(qapp, n: int, width: int, *, fixed: int | None, min_item: int) -> list:
    host = QWidget()
    flow = _FlowLayout(host, hspacing=10, vspacing=10, min_item_width=min_item)
    for _ in range(n):
        card = QWidget()
        if fixed:
            card.setFixedWidth(fixed)
        else:
            card.setMinimumWidth(min_item)
        flow.addWidget(card)
    host.resize(width, 1200)
    flow.setGeometry(host.rect())
    return [flow.itemAt(i).widget().geometry() for i in range(flow.count())]


def _overlaps(rects) -> list:
    rows: dict = {}
    for r in rects:
        rows.setdefault(r.y(), []).append(r)
    bad = []
    for row in rows.values():
        row.sort(key=lambda r: r.x())
        for a, b in zip(row, row[1:], strict=False):
            if a.right() >= b.left():
                bad.append((a.x(), a.width(), b.x()))
    return bad


@pytest.mark.parametrize("width", [1200, 1800, 2560, 3400])
def test_a_fixed_width_card_overlaps_its_neighbour(qapp, width: int) -> None:
    """The bug, pinned: a card wider than its slot runs into the next one."""
    rects = _host(qapp, 30, width, fixed=286, min_item=120)
    assert _overlaps(rects), (
        "expected the fixed width to collide — if this passes the bug is gone "
        "and this test should go with it"
    )


@pytest.mark.parametrize("width", [900, 1200, 1800, 2560, 3400])
def test_a_card_sized_by_the_layout_never_overlaps(qapp, width: int) -> None:
    rects = _host(qapp, 30, width, fixed=None, min_item=250)
    assert not _overlaps(rects), _overlaps(rects)


def test_the_layout_gives_each_card_the_full_share(qapp) -> None:
    """No gaps either: the cards fill the row edge to edge."""
    rects = _host(qapp, 30, 2560, fixed=None, min_item=250)
    widths = {r.width() for r in rects}
    assert len(widths) == 1, f"cards of differing widths: {sorted(widths)}"
    assert next(iter(widths)) >= 250
