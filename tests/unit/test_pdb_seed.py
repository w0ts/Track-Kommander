"""Generating a blank export.pdb instead of copying one off a stick.

The writer library can extend a database but not create one, so a blank had
to be found on a USB key Rekordbox had already exported to. That put a
"borrow a prepared stick" step in front of first use and meant carrying a
Pioneer-produced binary.

The load-bearing test is the last one: a stick built on a generated seed has
to describe the same library as one built on a real blank. Structure tests
above it exist to say *where* it broke when that fails.
"""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from core import pdb_seed
from core.device_export import find_seed, seed_is_blank

pytest.importorskip("rekordbox_pdb", reason="rekordbox-pdb not installed")

from rekordbox_pdb import Database
from rekordbox_pdb.edit import PdbEditor

PAGE = pdb_seed.PAGE_SIZE


@pytest.fixture(scope="module")
def blank(tmp_path_factory) -> Path:
    return pdb_seed.write(tmp_path_factory.mktemp("seed") / "export.pdb")


# ── Shape ───────────────────────────────────────────────────────────────────


def test_it_is_a_whole_number_of_pages(blank) -> None:
    size = blank.stat().st_size
    assert size % PAGE == 0
    assert size // PAGE == 1 + 2 * pdb_seed.NUM_TABLES


def test_the_header_declares_the_page_size_and_tables(blank) -> None:
    raw = blank.read_bytes()
    assert struct.unpack_from("<I", raw, 0x04)[0] == PAGE
    assert struct.unpack_from("<I", raw, 0x08)[0] == pdb_seed.NUM_TABLES


def test_every_table_is_declared_once(blank) -> None:
    db = Database.from_file(str(blank))
    assert sorted(t.type for t in db.tables) == list(range(pdb_seed.NUM_TABLES))


def test_each_table_has_an_index_page_and_a_candidate(blank) -> None:
    db = Database.from_file(str(blank))
    for t in db.tables:
        assert t.first_page == t.last_page, "a blank table owns one page"
        assert t.empty_candidate == t.first_page + 1


def test_index_pages_are_flagged_as_holding_no_rows(blank) -> None:
    """Bit 0x40 is what tells a reader to look elsewhere for data."""
    raw = blank.read_bytes()
    for table_type in range(pdb_seed.NUM_TABLES):
        page = 1 + 2 * table_type
        assert raw[page * PAGE + 0x1B] & 0x40


def test_the_empty_candidates_are_actually_empty(blank) -> None:
    raw = blank.read_bytes()
    for table_type in range(pdb_seed.NUM_TABLES):
        page = 2 + 2 * table_type
        assert raw[page * PAGE : (page + 1) * PAGE] == bytes(PAGE)


def test_an_empty_table_carries_the_no_page_sentinel(blank) -> None:
    """PdbEditor._allocate_page tests for exactly this before linking a
    table's first data page. Get it wrong and rows go nowhere."""
    raw = blank.read_bytes()
    for table_type in range(pdb_seed.NUM_TABLES):
        heap = (1 + 2 * table_type) * PAGE + pdb_seed.PAGE_HEADER_SIZE
        assert struct.unpack_from("<I", raw, heap + 4)[0] == pdb_seed.NO_PAGE
        assert struct.unpack_from("<I", raw, heap + 8)[0] == pdb_seed.NO_PAGE


def test_it_is_generated_not_stored(blank) -> None:
    """Two calls must produce identical bytes with nothing read from disk."""
    assert pdb_seed.build() == pdb_seed.build() == blank.read_bytes()


# ── It reads as a database ──────────────────────────────────────────────────


def test_the_parser_accepts_it(blank) -> None:
    db = Database.from_file(str(blank))
    assert len(db.tables) == pdb_seed.NUM_TABLES


def test_it_contains_nothing(blank) -> None:
    db = Database.from_file(str(blank))
    assert db.tracks == []
    assert list(db.playlist_tree) == []
    assert list(db.playlist_entries) == []
    assert db.artists == []


def test_it_passes_the_blank_check(blank) -> None:
    """The check the export uses before trusting a seed."""
    assert seed_is_blank(blank)


# ── It works as a seed ──────────────────────────────────────────────────────


def test_the_editor_can_add_a_track(blank) -> None:
    editor = PdbEditor.from_file(str(blank))
    tid = editor.add_track(
        title="A Track",
        file_path="/Contents/a.mp3",
        filename="a.mp3",
        artist="An Artist",
        tempo=12800,
        duration=60,
    )
    assert tid

    out = blank.parent / "with-a-track.pdb"
    editor.save(str(out))
    db = Database.from_file(str(out))
    assert [t.title for t in db.tracks] == ["A Track"]


def test_a_playlist_survives_a_save(blank) -> None:
    editor = PdbEditor.from_file(str(blank))
    tid = editor.add_track(title="T", file_path="/Contents/t.mp3", filename="t.mp3")
    pid = editor.create_playlist("Set A")
    editor.add_to_playlist(pid, tid)

    out = blank.parent / "with-a-playlist.pdb"
    editor.save(str(out))
    db = Database.from_file(str(out))
    assert [n.name for n in db.playlist_tree] == ["Set A"]
    assert len(list(db.playlist_entries)) == 1


def test_many_tracks_spill_onto_new_pages(blank) -> None:
    """One page does not hold a library. Allocating the next one is the part
    that depends on the empty-candidate wiring being right."""
    editor = PdbEditor.from_file(str(blank))
    for i in range(200):
        editor.add_track(
            title=f"Track {i:03}",
            file_path=f"/Contents/{i}.mp3",
            filename=f"{i}.mp3",
            artist=f"Artist {i}",
            tempo=12800,
            duration=180,
        )
    out = blank.parent / "many.pdb"
    editor.save(str(out))

    db = Database.from_file(str(out))
    assert len(db.tracks) == 200
    assert {t.title for t in db.tracks} == {f"Track {i:03}" for i in range(200)}


# ── Discovery ───────────────────────────────────────────────────────────────


def test_find_seed_generates_one_when_there_is_nothing(tmp_path, monkeypatch) -> None:
    """The whole point: no mounted stick required."""
    monkeypatch.setattr("core.device_export.scan_for_seed", list)
    found = find_seed(tmp_path)
    assert found is not None and found.is_file()
    assert seed_is_blank(found)


def test_a_generated_seed_is_cached_not_rebuilt(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("core.device_export.scan_for_seed", list)
    first = find_seed(tmp_path)
    stamp = first.stat().st_mtime_ns
    second = find_seed(tmp_path)
    assert second == first
    assert second.stat().st_mtime_ns == stamp, "it was rewritten"
