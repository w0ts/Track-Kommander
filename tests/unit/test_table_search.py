"""The Spotify tab's search box: rows narrow to those with every word typed."""

from __future__ import annotations

import unicodedata

import pytest
from PySide6.QtWidgets import QApplication, QTableWidget

from ui.table_fill import SEARCH_TEXT, cell, filter_rows, fold


@pytest.fixture
def qapp():
    yield QApplication.instance() or QApplication([])


def _table(rows: list[tuple[str, str, str]]) -> QTableWidget:
    table = QTableWidget(len(rows), 2)
    for row, (artist, title, extra) in enumerate(rows):
        first = cell(artist)
        first.setData(SEARCH_TEXT, extra)
        table.setItem(row, 0, first)
        table.setItem(row, 1, cell(title))
    return table


def test_case_and_accents_do_not_matter_however_unicode_spells_them():
    decomposed = unicodedata.normalize("NFD", "GRÜV")
    assert fold(decomposed) == fold("grüv") == "gruv"


def test_every_word_must_be_found_in_any_column_or_the_hidden_text(qapp):
    table = _table(
        [
            ("Joshwa", "Get Stupid", "Get Stupid EP"),
            ("Âme", "Rej", "Rej album"),
            ("Joshwa", "Hold Me", ""),
        ]
    )
    assert filter_rows(table, "joshwa stupid") == 1
    assert [table.isRowHidden(r) for r in range(3)] == [False, True, True]
    assert filter_rows(table, "ame album") == 1  # the album is in the hidden text
    assert filter_rows(table, "") == 3
    assert not any(table.isRowHidden(r) for r in range(3))


def test_columns_fit_their_content_and_the_spare_goes_to_one():
    from ui.table_fill import shares

    assert shares([50, 100, 80], 300, give=1) == [50, 170, 80]


def test_too_narrow_the_widest_columns_give_way_and_the_narrow_stay_whole():
    from ui.table_fill import MIN_COLUMN, shares

    widths = shares([40, 300, 500, 60], 600, give=1)
    assert widths[0] == 40 and widths[3] == 60  # whole
    assert widths[1] == widths[2] == 250  # the two widest share what is left
    assert min(shares([400, 400], 50, give=0)) == MIN_COLUMN
