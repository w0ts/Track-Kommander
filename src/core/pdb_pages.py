"""Page-header fix-ups for ``export.pdb``, so it reads like Rekordbox's.

A DeviceSQL data page stores its row count packed into bytes 0x18–0x1A, and
two more u16s at 0x20 and 0x22. Rekordbox writes ``1`` and ``rows - 1``
there; only the fixed tables (colours, columns and two more) carry
``rows`` and ``0``. The
writer library (``rekordbox_pdb``) writes ``rows`` and ``0`` on every page.

With one row the two conventions agree. That is why an XDJ-1000 loaded a
one-track stick of ours with waveform and hot cues, and crashed on every
stick with more tracks. Rekordbox 7.2.18's own export of the same 13 tracks
confirms the convention (artists: 12 rows, ``1`` and ``11``), and the
MIT-licensed baken project reproduces Rekordbox's pages byte for byte with
it (``crates/baken-export/src/pdb/page.rs``).
"""

from __future__ import annotations

import struct

PAGE_SIZE = 4096

#: The tables that keep ``rows`` and ``0``: COLORS (6), COLUMNS (16) and the
#: two unnamed fixed tables after it (17, 18) — all four prefilled in the
#: seed, and written that way by Rekordbox 7.2.18.
FIXED_TABLES = frozenset({6, 16, 17, 18})

#: Page flag marking an index page, which holds no rows.
_INDEX_PAGE = 0x40


def row_count(page: bytes | bytearray) -> int:
    """Rows on a data page: the low byte at 0x18, bits 8–12 at 0x19."""
    return page[0x18] | ((page[0x19] & 0x1F) << 8)


#: A row group: 16 u16 row offsets, then the present-rows mask and the
#: marker, growing down from the end of the page.
_GROUP_BYTES = 36


def _mark_last_row(buf: bytearray, start: int, rows: int) -> None:
    """Set each row group's second u16 the way Rekordbox does.

    The first u16 says which of the group's rows exist. Rekordbox 7.2.18
    sets only the bit of the page's last row in the second (artists, 12 rows:
    0xfff and 0x800; keys, 7 rows: 0x7f and 0x40), and 0 in every other
    group. The writer library copied the present mask into both, which is
    the same thing for a single row — the stick an XDJ-1000 did load.
    """
    last_group, last_slot = divmod(rows - 1, 16)
    for g in range((rows + 15) // 16):
        marker = 1 << last_slot if g == last_group else 0
        struct.pack_into("<H", buf, start + PAGE_SIZE - g * _GROUP_BYTES - 2, marker)


def fix_row_counts(pdb: bytes) -> bytes:
    """*pdb* with every data page's header and row-group markers set the way
    Rekordbox sets them: 0x20/0x22 to ``1`` and ``rows - 1``, and only the
    last row's bit in the marker (see :func:`_mark_last_row`).

    Index pages, never-used (all-zero) pages and the fixed tables are left
    exactly as they are.
    """
    buf = bytearray(pdb)
    for start in range(PAGE_SIZE, len(buf) - PAGE_SIZE + 1, PAGE_SIZE):
        flags = buf[start + 0x1B]
        if not flags or flags & _INDEX_PAGE:
            continue
        table = struct.unpack_from("<I", buf, start + 8)[0]
        rows = row_count(buf[start : start + PAGE_SIZE])
        if table in FIXED_TABLES or rows == 0:
            continue
        struct.pack_into("<HH", buf, start + 0x20, 1, rows - 1)
        _mark_last_row(buf, start, rows)
    return bytes(buf)
