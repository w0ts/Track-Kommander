"""Long strings in artist and album rows start on a 4-byte boundary.

Rekordbox pads before a UTF-16 (or long ASCII) name so it starts 4-aligned;
the writer library did that in track rows but not in artist and album rows.
An XDJ-1000 crashed on every stick with a non-ASCII artist or album name
("Temudo, NØRBAK", "GRÜV #4") and loaded the same stick once only those two
tables came from Rekordbox.
"""

from __future__ import annotations

import struct

import rekordbox_pdb.edit as edit
from rekordbox_pdb import Database
from rekordbox_pdb.pdb import TableType

from core.device_export import find_seed
from core.pdb_writer import AlignedPdbEditor


def test_a_utf16_artist_is_laid_out_like_rekordboxs() -> None:
    """Rekordbox 7.2.18's row for this artist, id 12, index shift aside:
    60 00 .. .. 0c 00 00 00 03 0c 00 00 90 20 00 00 54 00 65 00 …"""
    row, alloc, shift_at = AlignedPdbEditor._build_artist_row(12, "Temudo, NØRBAK")
    assert shift_at == 2
    assert row[:2] == bytes([0x60, 0x00])
    assert row[4:16] == bytes.fromhex("0c000000 030c0000 90200000")
    assert row[16:20] == "Te".encode("utf-16-le")
    assert alloc >= len(row) and alloc % 4 == 0


def test_an_ascii_artist_is_unchanged() -> None:
    """Short strings need no alignment; Rekordbox puts them at offset 10."""
    row, _alloc, _shift = AlignedPdbEditor._build_artist_row(1, "Benza")
    assert row == bytes.fromhex("6000 0000 01000000 03 0a") + bytes([0x0D]) + b"Benza"


def test_a_utf16_album_starts_at_0x18() -> None:
    """Rekordbox: … 03 18 00 00 90 …"""
    row, alloc, shift_at = AlignedPdbEditor._build_album_row(2, "GRÜV #4")
    assert shift_at == 2
    assert row[0x14:0x16] == bytes([0x03, 0x18])
    assert row[0x16:0x18] == b"\0\0"
    assert row[0x18] == 0x90
    assert alloc >= len(row) and alloc % 4 == 0


def test_an_ascii_album_is_unchanged() -> None:
    row, _alloc, _shift = AlignedPdbEditor._build_album_row(1, "Club Wave", artist_id=3)
    assert row[0x14:0x16] == bytes([0x03, 0x16])
    assert row[0x16] == (len("Club Wave") + 1) * 2 + 1
    assert struct.unpack_from("<I", row, 8)[0] == 3


def test_every_long_name_in_a_written_file_is_aligned(tmp_path) -> None:
    """End to end on the real seed path: add tracks with non-ASCII artist and
    album names and check where each name sits in the saved file."""
    seed = find_seed(tmp_path)
    assert seed is not None, "a blank seed is generated when none is cached"
    editor = AlignedPdbEditor.from_file(str(seed))
    for i, (artist, album) in enumerate(
        [("Temudo, NØRBAK", "GRÜV #4"), ("Benza", "Dancefloor Oriented"), ("Kölsch", "1977")]
    ):
        editor.add_track(title=f"t{i}", file_path=f"/Contents/t{i}.mp3", artist=artist, album=album)
    raw = editor.to_bytes()
    out = tmp_path / "export.pdb"
    out.write_bytes(raw)
    db = Database.from_file(str(out))
    for table, name_at in (
        (TableType.ARTISTS, lambda loc: raw[loc + 9]),
        (TableType.ALBUMS, lambda loc: raw[loc + 0x15]),
    ):
        for loc in db.row_locations(table):
            at = loc + name_at(loc)
            if not raw[at] & 1:
                assert at % 4 == 0, (table, hex(loc))
    assert {a.name for a in db.artists} == {"Temudo, NØRBAK", "Benza", "Kölsch"}
    assert {a.name for a in db.albums} == {"GRÜV #4", "Dancefloor Oriented", "1977"}


# ── Speed: no full re-parse per row ─────────────────────────────────────────


def _build(editor: AlignedPdbEditor) -> bytes:
    """Tracks sharing artists, albums and genres, in two playlists and a folder."""
    ids = [
        editor.add_track(
            title=f"t{i}",
            file_path=f"/Contents/t{i}.mp3",
            artist=f"Artist {i % 7}",
            album=f"Album {i % 5}",
            genre=f"Genre {i % 3}",
            key="Am",
        )
        for i in range(40)
    ]
    folder = editor.create_playlist("Sets", is_folder=True)
    for name, parent, members in (("One", folder, ids[::2]), ("Two", 0, ids[::-3])):
        pid = editor.create_playlist(name, parent_id=parent)
        for tid in members:
            editor.add_to_playlist(pid, tid)
    return editor.to_bytes()


class _LibraryPath(AlignedPdbEditor):
    """The same editor with the library's own lookups, as the reference."""

    _get_or_create = AlignedPdbEditor.__mro__[1]._get_or_create
    add_to_playlist = AlignedPdbEditor.__mro__[1].add_to_playlist


def test_cached_lookups_write_the_same_bytes(tmp_path) -> None:
    seed = find_seed(tmp_path)
    ours = _build(AlignedPdbEditor.from_file(str(seed)))
    reference = _build(_LibraryPath.from_file(str(seed)))
    assert ours == reference
    db = Database(ours)
    by_playlist: dict[int, list[int]] = {}
    for e in sorted(db.playlist_entries, key=lambda e: e.entry_index):
        by_playlist.setdefault(e.playlist_id, []).append(e.entry_index)
    assert all(idx == list(range(1, len(idx) + 1)) for idx in by_playlist.values())


def test_rows_do_not_reparse_the_whole_file(tmp_path, monkeypatch) -> None:
    """A full parse per playlist entry made a 900-track, 55-playlist export
    spend almost a minute on export.pdb."""
    parses = {"n": 0}
    real = edit.Database

    def counting(*args, **kwargs):
        parses["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(edit, "Database", counting)
    _build(AlignedPdbEditor.from_file(str(find_seed(tmp_path))))
    # add_track still parses once per track (40 here) to find its next id;
    # lookups and playlist entries must add almost nothing on top. The
    # library's own path parses 93 times for this build.
    assert parses["n"] <= 40 + 10
