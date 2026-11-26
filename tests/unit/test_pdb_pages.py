"""Row counts in the page headers of export.pdb, as Rekordbox writes them.

A data page records its row count twice more at 0x20/0x22. Rekordbox writes
``1`` and ``rows - 1``; the writer library wrote ``rows`` and ``0``. With one
row the two agree, which is why an XDJ-1000 loaded a one-track stick of ours
and crashed on every larger one.
"""

from __future__ import annotations

import struct

import pytest

from core.pdb_pages import FIXED_TABLES, fix_row_counts, row_count

PAGE = 4096


def _page(table: int, rows: int, u5: int, nrl: int, flags: int = 0x24) -> bytearray:
    p = bytearray(PAGE)
    struct.pack_into("<II", p, 4, 1, table)
    p[0x18] = rows & 0xFF
    p[0x19] = ((rows & 7) << 5) | ((rows >> 8) & 0x1F)
    p[0x1A] = (rows >> 3) & 0xFF
    p[0x1B] = flags
    struct.pack_into("<HH", p, 0x20, u5, nrl)
    return p


def _file(*pages: bytearray) -> bytes:
    return bytes(bytearray(PAGE)) + b"".join(bytes(p) for p in pages)


def test_row_count_reads_the_packed_bytes() -> None:
    """Baken's reference: 284 rows pack as 1c 81 23."""
    assert row_count(bytes(_page(8, 284, 0, 0))) == 284
    p = bytearray(PAGE)
    p[0x18:0x1B] = bytes([28, 129, 35])
    assert row_count(bytes(p)) == 284


def test_a_normal_page_gets_one_and_rows_minus_one() -> None:
    fixed = fix_row_counts(_file(_page(0, 10, 10, 0)))
    assert struct.unpack_from("<HH", fixed, PAGE + 0x20) == (1, 9)


def test_colours_and_columns_keep_rows_and_zero() -> None:
    for table in FIXED_TABLES:
        fixed = fix_row_counts(_file(_page(table, 8, 8, 0)))
        assert struct.unpack_from("<HH", fixed, PAGE + 0x20) == (8, 0)


def test_index_and_empty_pages_are_left_alone() -> None:
    index = _page(0, 0, 0x1FFF, 0x1FFF, flags=0x64)
    empty = bytearray(PAGE)
    fixed = fix_row_counts(_file(index, empty))
    assert fixed[PAGE : 3 * PAGE] == bytes(index) + bytes(empty)


def test_a_one_row_page_is_unchanged() -> None:
    page = _with_groups(0, 1)
    assert fix_row_counts(_file(page))[PAGE:] == bytes(page)


#: (table, rows, u5, num_rows_large) of the pages Rekordbox 7.2.18 wrote in
#: one go for a 13-track export. Its track and history pages were rewritten
#: in place and carry values of their own, so they are not listed.
RB13_PAGES = [
    (1, 2, 1, 1),
    (2, 12, 1, 11),
    (3, 9, 1, 8),
    (4, 3, 1, 2),
    (5, 7, 1, 6),
    (6, 8, 8, 0),
    (13, 7, 1, 6),
    (16, 27, 27, 0),
    (17, 22, 22, 0),
    (18, 17, 17, 0),
]


@pytest.mark.parametrize(("table", "rows", "u5", "nrl"), RB13_PAGES)
def test_rekordboxs_pages_come_out_unchanged(table, rows, u5, nrl) -> None:
    """Starting from what the writer library produces, the fix-up arrives
    at exactly what Rekordbox wrote."""
    ours = _page(table, rows, rows, 0)
    fixed = fix_row_counts(_file(ours))
    assert struct.unpack_from("<HH", fixed, PAGE + 0x20) == (u5, nrl)


# ── Row-group markers ──────────────────────────────────────────────────────
#
# Each group of 16 rows ends with two u16s: which rows are present, then a
# second field. Rekordbox 7.2.18 sets only the bit of the page's last row in
# the second (artists, 12 rows: 0xfff and 0x800); the writer library set every
# row's bit (0xfff and 0xfff). With one row both are 0x1 — the same one-works,
# many-crash pattern as the row counts.


def _groups(page: bytes, rows: int) -> list[tuple[int, int]]:
    out = []
    for g in range((rows + 15) // 16):
        base = PAGE - g * 36
        out.append(struct.unpack_from("<HH", page, base - 4))
    return out


def _with_groups(table: int, rows: int) -> bytearray:
    """A page as the writer library leaves it: both fields = present mask."""
    p = _page(table, rows, rows, 0)
    for g in range((rows + 15) // 16):
        n = min(16, rows - 16 * g)
        mask = (1 << n) - 1
        struct.pack_into("<HH", p, PAGE - g * 36 - 4, mask, mask)
    return p


@pytest.mark.parametrize(
    ("table", "rows", "want"),
    [
        (2, 12, [(0xFFF, 0x800)]),  # artists, as Rekordbox wrote them
        (3, 9, [(0x1FF, 0x100)]),  # albums
        (5, 7, [(0x7F, 0x40)]),  # keys
        (1, 2, [(0x3, 0x2)]),  # genres
        (0, 1, [(0x1, 0x1)]),  # one row: unchanged
        (8, 20, [(0xFFFF, 0x0), (0xF, 0x8)]),  # the last row sits in group 2
    ],
)
def test_only_the_last_rows_bit_is_marked(table, rows, want) -> None:
    fixed = fix_row_counts(_file(_with_groups(table, rows)))
    assert _groups(fixed[PAGE : 2 * PAGE], rows) == want


def test_fixed_tables_keep_the_full_mask() -> None:
    for table in FIXED_TABLES:
        fixed = fix_row_counts(_file(_with_groups(table, 8)))
        assert _groups(fixed[PAGE : 2 * PAGE], 8) == [(0xFF, 0xFF)]
