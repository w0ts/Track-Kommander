"""Filling a QTableWidget without stalling the UI, and cells that sort right.

A table whose columns fit their contents re-measures them after every cell
set, so a refill grows with the square of its rows: Library quality froze
for seconds, another tab stalled 0.2-0.7 s (2026-10-04). Here the
table is filled with that off and its columns fitted once, and only when
the number of rows changed (a periodic refresh of the same rows keeps the
widths the user may have dragged).
"""

from __future__ import annotations

import unicodedata

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import QHeaderView, QTableWidget, QTableWidgetItem

#: The narrowest a column is trimmed to when the table is too narrow.
MIN_COLUMN = 48
TEXT_TO_TABLE_PX = 6  # between a line of text and the table under it (User, 2026-11-24)


def shares(natural: list[int], room: int, give: int) -> list[int]:
    """Column widths for *room* pixels: each as wide as its content
    (*natural*) when it all fits, the spare going to column *give*; when
    not, the widest are trimmed to one common width (the narrow ones stay
    whole), never under MIN_COLUMN."""
    if sum(natural) <= room:
        return [w + (room - sum(natural) if col == give else 0) for col, w in enumerate(natural)]
    spent, cap = 0, room / len(natural)
    for n, w in enumerate(sorted(natural)):
        left = len(natural) - n
        if w * left > room - spent:  # these and the wider ones share what is left
            cap = (room - spent) / left
            break
        spent += w
    return [w if w <= cap else max(MIN_COLUMN, int(cap)) for w in natural]


class AutoFit(QObject):
    """A table's columns fitted to their content as best the width allows
    (:func:`shares`), again whenever the table is resized. :meth:`fit`
    measures the content: called after a fill (Refill does), not on every
    refresh — that would undo a column the user dragged."""

    def __init__(self, table: QTableWidget, give: int = -1) -> None:
        super().__init__(table)
        self.table = table
        self.give = give if give >= 0 else table.columnCount() - 1
        self.natural: list[int] = []
        header = table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(False)
        table.viewport().installEventFilter(self)

    def fit(self) -> None:
        self.table.resizeColumnsToContents()
        header = self.table.horizontalHeader()
        self.natural = [header.sectionSize(c) for c in range(self.table.columnCount())]
        self._spread()

    def _spread(self) -> None:
        if not self.natural:
            return
        header = self.table.horizontalHeader()
        widths = shares(self.natural, self.table.viewport().width(), self.give)
        for col, width in enumerate(widths):
            header.resizeSection(col, width)

    def eventFilter(self, watched, event) -> bool:
        if event.type() == QEvent.Type.Resize:
            self._spread()
        return False


def fit(table: QTableWidget) -> None:
    """Fit *table*'s columns now: by its AutoFit when it has one."""
    fitter = table.findChild(AutoFit)
    if fitter is not None:
        fitter.fit()
    else:
        table.resizeColumnsToContents()


def interactive(table: QTableWidget, give: int = -1) -> AutoFit:
    """Columns fitted to their content (AutoFit) that the user may drag;
    *give*: the column the spare room goes to (the last by default)."""
    return AutoFit(table, give)


def sortable(table: QTableWidget) -> None:
    """Sorted by a click on a header; in its own order until then (no
    column picked: enabling sorting would sort by the first one at once)."""
    table.horizontalHeader().setSortIndicator(-1, Qt.SortOrder.AscendingOrder)
    table.setSortingEnabled(True)


class Refill:
    """``with Refill(table, n): set cells`` — n rows, set quietly, then fitted."""

    def __init__(self, table: QTableWidget, rows: int) -> None:
        self.table, self.rows = table, rows
        self.changed = table.rowCount() != rows

    def __enter__(self) -> QTableWidget:
        self.sorting = self.table.isSortingEnabled()
        self.table.setSortingEnabled(False)
        self.table.setUpdatesEnabled(False)
        self.table.setRowCount(self.rows)
        return self.table

    def __exit__(self, *exc) -> None:
        self.table.setUpdatesEnabled(True)
        if self.changed:
            fit(self.table)
        self.table.setSortingEnabled(self.sorting)


#: A cell's sort key when its text does not sort right ("#", "Length").
SORT_KEY = Qt.ItemDataRole.UserRole + 1


class Cell(QTableWidgetItem):
    """A table cell sorting by its SORT_KEY when it has one, else by its
    text ignoring case. (Handing off to QTableWidgetItem's own comparison
    from here crashed PySide 6 on sorting, 2026-10-04.)"""

    def __lt__(self, other) -> bool:
        a, b = self.data(SORT_KEY), other.data(SORT_KEY)
        if a is not None and b is not None:
            return a < b
        return self.text().casefold() < other.text().casefold()


def cell(text: str, sort_key=None) -> Cell:
    item = Cell(text)
    if sort_key is not None:
        item.setData(SORT_KEY, sort_key)
    return item


#: Words a row is also found by, beyond its cells' text (album, file name).
SEARCH_TEXT = Qt.ItemDataRole.UserRole + 2


def fold(text: str) -> str:
    """*text* as a search compares it: no case, no accents ("Ü" is "u",
    however Unicode spells it)."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


def row_text(table: QTableWidget, row: int) -> str:
    """Everything *row* is found by: its cells' text and its SEARCH_TEXT."""
    parts = []
    for col in range(table.columnCount()):
        item = table.item(row, col)
        if item is not None:
            parts.append(item.text())
            extra = item.data(SEARCH_TEXT)
            if extra:
                parts.append(str(extra))
    return fold(" ".join(parts))


def filter_rows(table: QTableWidget, query: str) -> int:
    """Hide the rows lacking any word of *query* (in any order, any cell);
    an empty query shows them all. Returns how many rows show."""
    words = fold(query).split()
    shown = 0
    for row in range(table.rowCount()):
        text = row_text(table, row) if words else ""
        hide = any(w not in text for w in words)
        table.setRowHidden(row, hide)
        shown += not hide
    return shown
