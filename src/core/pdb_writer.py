"""The ``export.pdb`` editor, with artist and album rows laid out like Rekordbox's.

DeviceSQL strings come short (one length byte, ASCII) or long (a 4-byte
header, then UTF-16 or ASCII). Rekordbox starts every long string on a
4-byte boundary, padding with zeros before it. ``rekordbox_pdb`` does that in
track rows but not in artist and album rows: a non-ASCII artist or album name
landed two bytes off.

An XDJ-1000 crashed on every stick of ours with such a name ("Temudo,
NØRBAK", "GRÜV #4") and loaded a one-track stick whose names were all ASCII.
It loaded Rekordbox's own stick of the same 13 tracks, and still loaded it
with our track table swapped in; it crashed once our artist and album tables
were in. Rekordbox's rows put those names at 12 and 0x18 instead of 10 and
0x16.

Genre, label and key rows need nothing: their names start at 4 and 8.
"""

from __future__ import annotations

import struct

from rekordbox_pdb.edit import PdbEditor, encode_string
from rekordbox_pdb.pdb import TableType

#: Where the name starts in each row shape, before any padding.
_ARTIST_NAME_AT = 0x0A
_ALBUM_NAME_AT = 0x16


def _align4(n: int) -> int:
    return (n + 3) & ~3


def _name_at(natural: int, encoded: bytes) -> int:
    """Offset of a name: *natural*, or the next multiple of 4 for a long string."""
    is_short = encoded[0] & 1
    return natural if is_short else _align4(natural)


class AlignedPdbEditor(PdbEditor):
    """``PdbEditor`` whose artist and album rows 4-align long names.

    It also remembers what it wrote. The library parses the whole file again
    after every row it appends, and reads it back to find the next playlist
    entry index or whether an artist already exists — for a 900-track stick
    with 55 playlists that was almost a minute of CPU in ``export.pdb``.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        #: table type -> {name: id}, read once, then kept up to date.
        self._names: dict[int, dict[str, int]] = {}
        #: playlist this editor created -> the entry index its next track takes.
        self._next_entry: dict[int, int] = {}

    def _get_or_create(self, table_type: int, name: str, build) -> int:
        names = self._names.get(table_type)
        if names is None:
            # First match wins, as in the library's own scan.
            names = {}
            for row in self.db.rows(table_type):
                names.setdefault(row.name, row.id)
            self._names[table_type] = names
        if name in names:
            return names[name]
        new_id = max(names.values(), default=0) + 1
        row_bytes, alloc, shift_at = build(new_id, name)
        self._append_row(table_type, row_bytes, alloc, index_shift_at=shift_at)
        names[name] = new_id
        return new_id

    def create_playlist(self, name: str, parent_id: int = 0, is_folder: bool = False) -> int:
        node_id = super().create_playlist(name, parent_id, is_folder)
        if not is_folder:
            self._next_entry[node_id] = 1
        return node_id

    def add_to_playlist(
        self, playlist_id: int, track_id: int, entry_index: int | None = None
    ) -> None:
        # Only a playlist made here is known to be empty at the start; any
        # other one, or an explicit index, goes through the library's checks.
        if entry_index is not None or playlist_id not in self._next_entry:
            super().add_to_playlist(playlist_id, track_id, entry_index)
            return
        entry_index = self._next_entry[playlist_id]
        row = struct.pack("<III", entry_index, track_id, playlist_id)
        self._append_row(TableType.PLAYLIST_ENTRIES, row, 12)
        self._next_entry[playlist_id] = entry_index + 1

    @staticmethod
    def _build_artist_row(row_id: int, name: str):
        s = encode_string(name)
        at = _name_at(_ARTIST_NAME_AT, s)
        head = struct.pack("<HHIBB", 0x60, 0, row_id, 0x03, at)
        row = head + bytes(at - len(head)) + s
        return row, _align4(at + _align4(len(s))) + 4, 2

    @staticmethod
    def _build_album_row(row_id: int, name: str, artist_id: int = 0):
        s = encode_string(name)
        at = _name_at(_ALBUM_NAME_AT, s)
        head = struct.pack("<HHIIIIBB", 0x80, 0, 0, artist_id, row_id, 0, 0x03, at)
        row = head + bytes(at - len(head)) + s
        return row, _align4(at + _align4(len(s))) + 4, 2
