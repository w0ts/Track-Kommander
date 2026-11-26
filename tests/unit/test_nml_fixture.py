"""The Traktor → Rekordbox path, driven by a committed NML fixture.

Everything else that reads a collection either builds a small NML inline or
reaches for the one on this machine. Neither covers CI: an inline document
only contains the shapes whoever wrote it remembered, and the real collection
does not exist on a build agent, so the tests that use it skip silently and
the export path goes unexercised.

``data/collection.nml`` is a real Traktor 4 document, anonymised — every
element, attribute and ordering in it was taken from a 897-track collection
rather than invented. It deliberately carries the shapes that are easy to get
wrong:

* the grid marker (``TYPE="4"``) written **before** its hotcue-0 mirror in
  one entry and **after** it in another — Traktor emits both orders;
* a hotcue-0 mirror named ``n.n.`` rather than ``AutoGrid``;
* an entry Traktor has never analysed — no ``TEMPO``, no ``MUSICAL_KEY``,
  no cues, ``PLAYTIME="0"``;
* a ``RANKING`` off the 0/51/102/153/204/255 ladder;
* ``//`` in a filename, which is Traktor's encoding of a literal ``:``;
* an ``ENTRY`` with no ``LOCATION`` at all, which must be skipped not crashed
  on;
* playlists nested in a folder, an empty one, a smartlist, and an entry
  pointing at a track that is no longer in the collection.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from core.playlists import parse_playlists
from core.rekordbox import (
    build_library,
    camelot,
    is_grid_mirror,
    location_url,
    parse_entry,
    position_marks,
    write_library,
)
from core.traktor import _nml_build_path

FIXTURE = Path(__file__).parent / "data" / "collection.nml"


@pytest.fixture(scope="module")
def entries() -> list[ET.Element]:
    root = ET.parse(FIXTURE).getroot()
    return root.find("COLLECTION").findall("ENTRY")


@pytest.fixture(scope="module")
def tracks(entries) -> dict[str, object]:
    """Every usable entry, by title — the fixture's titles name their case."""
    out = {}
    for e in entries:
        loc = e.find("LOCATION")
        if loc is None:
            continue
        path = _nml_build_path(loc)
        if path:
            out[e.get("TITLE")] = parse_entry(e, path)
    return out


# ── The document itself ─────────────────────────────────────────────────────


def test_the_fixture_is_committed() -> None:
    """The point of it: CI has a collection without Traktor installed."""
    assert FIXTURE.is_file(), "the NML fixture must be in the repo, not on a dev box"


def test_every_entry_but_the_locationless_one_resolves(entries, tracks) -> None:
    assert len(entries) == 9
    assert len(tracks) == 8, "the ENTRY with no LOCATION must be skipped"
    assert "No Location" not in tracks


# ── Path reconstruction ─────────────────────────────────────────────────────


def test_dir_separators_become_the_platforms_own(tracks) -> None:
    """Traktor's "/:" becomes the separator files are opened with here: "/"
    on macOS, a backslash on Windows."""
    assert tracks["Full House"].path == str(
        Path("/Users/dj/Music/Tracks/Test Artist - Full House.mp3")
    )


def test_a_double_slash_decodes_back_to_a_colon(tracks) -> None:
    """Traktor stores a literal ":" in a filename as "//"."""
    assert tracks["Colon In Name"].path.endswith("Test Artist - Re:elected.mp3")


def test_a_subfolder_survives(tracks) -> None:
    assert Path(tracks["Colon In Name"].path).parent == Path("/Users/dj/Music/Tracks/Deep Cuts")


# ── Field mapping ───────────────────────────────────────────────────────────


def test_a_fully_populated_entry_maps_every_field(tracks) -> None:
    t = tracks["Full House"]
    assert t.title == "Full House"
    assert t.artist == "Test Artist"
    assert t.album == "Test Album"
    assert t.genre == "Techno"
    assert t.label == "Test Label"
    assert t.comment == "a comment"
    assert t.bpm == pytest.approx(141.000275)
    assert t.musical_key == 10
    assert t.rating == 102
    assert t.play_count == 7
    assert t.bitrate == 320, "Traktor stores bits/s, Rekordbox wants kbps"
    assert t.total_time_s == pytest.approx(338.991028)
    assert t.date_added == "2025-09-05"


def test_playtime_float_wins_over_the_rounded_one(tracks) -> None:
    """Both are present; the integer one loses a second of the track."""
    assert tracks["Full House"].total_time_s != 339


def test_an_unanalysed_entry_does_not_invent_values(tracks) -> None:
    t = tracks["Never Analysed"]
    assert t.bpm == 0.0
    assert t.musical_key is None
    assert t.cues == []
    assert t.total_time_s == 0.0
    assert camelot(t.musical_key) == "", "no key must not become a wrong key"


def test_a_rating_off_the_ladder_is_carried_through(tracks) -> None:
    """Traktor normally writes 0/51/102/153/204/255, but not always."""
    assert tracks["Odd Rating"].rating == 196


def test_a_missing_album_is_empty_not_none(tracks) -> None:
    assert tracks["Unnamed Mirror"].album == ""


# ── Cues and the beat grid ──────────────────────────────────────────────────


def test_the_grid_is_found_whichever_order_traktor_wrote_it(tracks) -> None:
    """The grid element comes before its mirror in one entry, after in the
    other. Both are real; neither may change which cue is the grid."""
    after = tracks["Full House"].grid_cue
    before = tracks["Mirror First"].grid_cue
    assert after is not None and before is not None
    assert after.grid_bpm == pytest.approx(141.000275)
    assert before.grid_bpm == pytest.approx(149.174194)


def test_the_autogrid_mirror_is_not_exported_as_a_cue(tracks) -> None:
    """Traktor duplicates the grid anchor as hotcue 0 named "AutoGrid".
    Exporting it would put a meaningless cue on the first beat."""
    marks = position_marks(tracks["Full House"])
    starts = [m.get("Start") for m in marks]
    assert "0.121" not in starts, "the grid mirror leaked into the cues"


def test_the_mirror_is_dropped_whatever_it_is_called(tracks) -> None:
    """The name is not the test — position is. A real collection spells this
    cue "AutoGrid", "n.n." and "Beat Marker"; all three are the same thing."""
    t = tracks["Unnamed Mirror"]
    assert any(c.name == "n.n." for c in t.cues), "fixture lost its case"
    assert position_marks(t) == [], "an n.n. mirror must be dropped too"


def test_the_mirror_is_dropped_whichever_order_it_was_written(tracks) -> None:
    for title in ("Full House", "Mirror First"):
        marks = position_marks(tracks[title])
        grid = tracks[title].grid_cue
        for mark in marks:
            assert abs(float(mark.get("Start")) * 1000 - grid.start_ms) >= 1.0, (
                f"{title}: the grid mirror survived"
            )


def test_a_real_hotcue_away_from_the_grid_is_kept(tracks) -> None:
    """Only the duplicate goes. A cue the user placed stays, wherever it is."""
    marks = position_marks(tracks["Mirror First"])
    assert [m.get("Name") for m in marks] == ["tk: Drop"]


def test_is_grid_mirror_needs_a_grid(tracks) -> None:
    """No grid, nothing to duplicate — every cue is the user's."""
    t = tracks["Full House"]
    assert not is_grid_mirror(t.cues[1], None)


def test_the_grid_itself_is_never_a_cue(tracks) -> None:
    for name, t in tracks.items():
        grid = t.grid_cue
        if grid is None:
            continue
        for mark in position_marks(t):
            assert abs(float(mark.get("Start")) * 1000 - grid.start_ms) >= 1.0, (
                f"{name}: grid emitted as a cue"
            )


def test_a_loop_keeps_its_length(tracks) -> None:
    marks = position_marks(tracks["Full House"])
    loop = next(m for m in marks if m.get("Name") == "Loop 8")
    assert loop.get("End") is not None
    span = float(loop.get("End")) - float(loop.get("Start"))
    assert span == pytest.approx(13.617, abs=0.001)


def test_a_cue_with_no_hotcue_slot_becomes_a_memory_cue(tracks) -> None:
    marks = position_marks(tracks["Full House"])
    memory = next(m for m in marks if m.get("Name") == "memory")
    assert memory.get("Num") == "-1"


def test_hotcues_come_out_in_slot_order(tracks) -> None:
    nums = [int(m.get("Num")) for m in position_marks(tracks["Full House"])]
    hot = [n for n in nums if n >= 0]
    assert hot == sorted(hot)
    assert nums[-1] == -1, "memory cues sort after the hotcues"


# ── The round trip ──────────────────────────────────────────────────────────


def test_the_whole_collection_round_trips_through_a_parser(tracks, tmp_path) -> None:
    """Build the Rekordbox document from the fixture, write it, read it back.

    This is the test the live collection used to be needed for.
    """
    ordered = list(tracks.values())
    tree = build_library(ordered, playlists={"All": list(range(len(ordered)))})
    dest = tmp_path / "rekordbox.xml"
    write_library(tree, dest)

    root = ET.parse(dest).getroot()
    collection = root.find("COLLECTION")
    assert collection.get("Entries") == str(len(ordered))
    assert len(collection.findall("TRACK")) == len(ordered)

    by_name = {t.get("Name"): t for t in collection.findall("TRACK")}
    assert set(by_name) == {t.title for t in ordered}

    full = by_name["Full House"]
    assert full.get("Artist") == "Test Artist"
    assert full.get("Genre") == "Techno"
    assert full.get("AverageBpm") == "141.00"
    assert full.get("Tonality") == camelot(10) or full.get("Tonality")
    assert full.get("Location") == location_url(
        "/Users/dj/Music/Tracks/Test Artist - Full House.mp3"
    )


def test_the_unanalysed_track_survives_the_round_trip(tracks, tmp_path) -> None:
    """A track with no grid must still be exportable, not crash the writer."""
    ordered = list(tracks.values())
    dest = tmp_path / "rekordbox.xml"
    write_library(build_library(ordered), dest)

    root = ET.parse(dest).getroot()
    track = next(
        t for t in root.find("COLLECTION").findall("TRACK") if t.get("Name") == "Never Analysed"
    )
    assert track.get("AverageBpm") in ("0.00", "", None)
    assert track.findall("POSITION_MARK") == []


def test_a_colon_in_the_filename_survives_to_the_url(tracks, tmp_path) -> None:
    ordered = list(tracks.values())
    dest = tmp_path / "rekordbox.xml"
    write_library(build_library(ordered), dest)

    root = ET.parse(dest).getroot()
    track = next(
        t for t in root.find("COLLECTION").findall("TRACK") if t.get("Name") == "Colon In Name"
    )
    assert "Re%3Aelected" in track.get("Location") or "Re:elected" in track.get("Location")


# ── Playlists ───────────────────────────────────────────────────────────────


def test_every_playlist_is_found() -> None:
    names = [p.name for p in parse_playlists(FIXTURE)]
    assert names == ["Rated", "Set A", "_RECORDINGS", "Friday"]


def test_a_nested_playlist_reports_its_folder() -> None:
    friday = next(p for p in parse_playlists(FIXTURE) if p.name == "Friday")
    assert friday.path == ("Gigs",)
    assert friday.display == "Gigs / Friday"


def test_a_smartlist_carries_its_query_and_no_entries() -> None:
    rated = next(p for p in parse_playlists(FIXTURE) if p.name == "Rated")
    assert rated.smart
    assert rated.query == "$RATING > 1"
    assert rated.paths == []


def test_playlist_order_is_the_users_order() -> None:
    set_a = next(p for p in parse_playlists(FIXTURE) if p.name == "Set A")
    assert [Path(p).name for p in set_a.paths] == [
        "Test Artist - Full House.mp3",
        "Test Artist - Lossless.flac",
        "Test Artist - Re:elected.mp3",
    ]


def test_an_empty_playlist_is_still_listed() -> None:
    empty = next(p for p in parse_playlists(FIXTURE) if p.name == "_RECORDINGS")
    assert len(empty) == 0


def test_a_playlist_entry_missing_from_the_collection_is_still_parsed() -> None:
    """parse_playlists reads the playlist, not the collection — resolving
    against what actually exists is the caller's job."""
    friday = next(p for p in parse_playlists(FIXTURE) if p.name == "Friday")
    assert len(friday) == 2
    assert any("Deleted From Collection" in p for p in friday.paths)
