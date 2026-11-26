"""Generate a blank ``export.pdb`` instead of copying one off a stick.

The device export builds its database by editing an existing blank one
(:mod:`rekordbox_pdb` can open and extend a file, not create one), so until
now a blank had to be found on a USB key Rekordbox had already exported to.
That put a "borrow a prepared stick once" step in front of first use, and it
meant carrying a Pioneer-produced binary around.

There is very little in one. Measured against a real blank, of its 41 pages
twenty are entirely zero, twenty are the same index page differing in four
bytes, and everything that is neither structure nor padding comes to under a
kilobyte. So it is written here from the format instead.

Layout
------

The file is an array of 4096-byte pages. Page 0 is the header: page size,
table count, the next page nobody has used, and one 16-byte descriptor per
table giving ``(type, empty_candidate, first_page, last_page)``.

Each table then owns two pages:

* an **index page** (flags ``0x64``, "not a data page"), whose small heap
  names the table's first and last *data* pages — ``0x03FFFFFF`` while there
  are none, which is exactly the sentinel
  ``rekordbox_pdb.edit.PdbEditor._allocate_page`` looks for when it turns an
  empty table into one with rows;
* an all-zero **empty candidate**, the page the editor will claim first.

The rest of an index page is a fixed run of DeviceSQL index entries. The
header's own field at ``0x24`` says how many (1004), so the body is generated
rather than stored.

What this does not carry
------------------------

A Rekordbox-written blank also has rows in ``COLORS`` (its eight colour
names) and ``COLUMNS`` (the browser's column labels). Nothing in the export
reads them and no player behaviour is known to depend on them, but that is
**not confirmed on hardware** — like the rest of the device export. If a
player turns out to want them, they are 31 fixed strings to add here.
"""

from __future__ import annotations

import logging
import struct
from pathlib import Path

log = logging.getLogger(__name__)

PAGE_SIZE = 4096
PAGE_HEADER_SIZE = 0x28

#: The twenty tables a Rekordbox export declares, in type order.
NUM_TABLES = 20

#: Marks "there is no such page" in an index page's heap. The editor tests
#: for exactly this value before linking a table's first data page.
NO_PAGE = 0x03FFFFFF

#: Index pages are flagged as carrying no rows (bit 0x40), so a reader skips
#: their body entirely and looks for data on the pages they point at.
INDEX_PAGE_FLAGS = 0x64

#: How many index entries fill out an index page. Not a magic number: the
#: page header records it at 0x24, and 20 + 4 * 1004 + 20 == 4056, which is
#: the page less its 40-byte header.
INDEX_ENTRIES = 1004

_INDEX_ENTRY = bytes((0xF8, 0xFF, 0xFF, 0x1F))
_INDEX_TAIL = bytes(20)


def _index_page(page_index: int, table_type: int, next_page: int) -> bytes:
    """One table's index page, with no data pages behind it yet."""
    page = bytearray(PAGE_SIZE)
    struct.pack_into("<I", page, 0x04, page_index)
    struct.pack_into("<I", page, 0x08, table_type)
    struct.pack_into("<I", page, 0x0C, next_page)
    struct.pack_into("<I", page, 0x10, 1)
    page[0x18] = 0  # num_rows
    page[0x1B] = INDEX_PAGE_FLAGS
    struct.pack_into("<H", page, 0x20, 8191)
    struct.pack_into("<H", page, 0x22, 8191)
    struct.pack_into("<H", page, 0x24, INDEX_ENTRIES)

    heap = bytearray()
    heap += struct.pack("<I", page_index)  # this page
    heap += struct.pack("<I", NO_PAGE)  # first data page: none yet
    heap += struct.pack("<I", NO_PAGE)  # last data page: none yet
    heap += bytes(6)
    heap += bytes((0xFF, 0x1F))
    heap += _INDEX_ENTRY * INDEX_ENTRIES
    heap += _INDEX_TAIL
    assert len(heap) == PAGE_SIZE - PAGE_HEADER_SIZE, len(heap)
    page[PAGE_HEADER_SIZE:] = heap
    return bytes(page)


def build() -> bytes:
    """A blank ``export.pdb`` as bytes.

    Twenty tables, each with an index page and an empty candidate: pages
    1..40, with page 41 the next one nobody has touched.
    """
    first_free = 1 + 2 * NUM_TABLES

    header = bytearray(PAGE_SIZE)
    struct.pack_into("<I", header, 0x00, 0)
    struct.pack_into("<I", header, 0x04, PAGE_SIZE)
    struct.pack_into("<I", header, 0x08, NUM_TABLES)
    struct.pack_into("<I", header, 0x0C, first_free)  # next unused page
    struct.pack_into("<I", header, 0x10, 4)
    struct.pack_into("<I", header, 0x14, 1)  # save sequence
    struct.pack_into("<I", header, 0x18, 0)

    pages: list[bytes] = []
    for table_type in range(NUM_TABLES):
        index_page = 1 + 2 * table_type
        candidate = index_page + 1
        struct.pack_into(
            "<4I", header, 0x1C + table_type * 16, table_type, candidate, index_page, index_page
        )
        pages.append(_index_page(index_page, table_type, candidate))
        pages.append(bytes(PAGE_SIZE))  # the empty candidate

    return bytes(header) + b"".join(pages)


def write(dest: Path) -> Path:
    """Write a blank ``export.pdb`` to *dest*. Returns the path."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(build())
    log.info("generated a blank export.pdb → %s", dest)
    return dest
