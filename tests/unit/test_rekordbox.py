"""Tests for the Traktor → Rekordbox XML conversion.

The conversion is where units and conventions quietly disagree: Traktor stores
cue positions in milliseconds and Rekordbox wants seconds, Traktor mirrors its
beat-grid anchor as a phantom "AutoGrid" hotcue, and the key is an index that
means nothing without the lookup. Each of those gets a test.

The key mapping was validated against all 24 values in a real 897-track
collection by cross-checking the derived Camelot code against the Open Key
string Traktor writes to ``INFO@KEY``; the table below encodes a sample of
that result.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from core.rekordbox import (
    Cue,
    Track,
    build_library,
    camelot,
    key_parts,
    location_url,
    parse_cue,
    parse_entry,
    position_marks,
    tonality,
    track_element,
)

GRID = Cue(name="AutoGrid", type=4, start_ms=376.54, grid_bpm=120.0)


def _track(**kw) -> Track:
    base = {"path": "/music/a.mp3", "title": "A", "artist": "B", "bpm": 120.0}
    base.update(kw)
    return Track(**base)


# ── Keys ────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0, (0, True)),  # C major
        (11, (11, True)),  # B major
        (12, (0, False)),  # C minor
        (23, (11, False)),  # B minor
    ],
)
def test_key_parts_splits_major_and_minor(value: int, expected) -> None:
    assert key_parts(value) == expected


@pytest.mark.parametrize("value", [-1, 24, 99, None])
def test_key_parts_rejects_out_of_range(value) -> None:
    assert key_parts(value) is None


@pytest.mark.parametrize(
    ("value", "cam"),
    [
        (0, "8B"),  # verified against nml KEY="1d"
        (1, "3B"),  # verified against nml KEY="8d"
        (12, "5A"),  # verified against nml KEY="10m"
        (21, "8A"),  # verified against nml KEY="1m"
    ],
)
def test_camelot_matches_the_real_collection(value: int, cam: str) -> None:
    assert camelot(value) == cam


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0, "C"),
        (1, "Db"),
        (9, "A"),
        (12, "Cm"),
        (21, "Am"),
        (23, "Bm"),
    ],
)
def test_tonality(value: int, expected: str) -> None:
    assert tonality(value) == expected


def test_unknown_key_is_empty_not_wrong() -> None:
    assert tonality(None) == ""
    assert camelot(-1) == ""


# ── Location URLs ───────────────────────────────────────────────────────────


def test_location_url_percent_encodes() -> None:
    got = location_url("/Users/x/Music/A Track (Remix).mp3")
    assert got == "file://localhost/Users/x/Music/A%20Track%20%28Remix%29.mp3"


def test_location_url_keeps_windows_drive_letter() -> None:
    got = location_url(r"C:\Music\a.mp3")
    assert got == "file://localhost/C:/Music/a.mp3"


def test_location_url_of_nothing_is_empty() -> None:
    assert location_url("") == ""


# ── Cue conversion ──────────────────────────────────────────────────────────


def test_milliseconds_become_seconds() -> None:
    t = _track(cues=[Cue("Drop", 0, 136356.63, hotcue=2)])
    mark = position_marks(t)[0]
    assert mark.get("Start") == "136.357"


def test_offset_shifts_every_position() -> None:
    t = _track(cues=[GRID, Cue("Drop", 0, 10_000.0, hotcue=1)])
    marks = position_marks(t, offset_ms=-25.0)
    assert marks[0].get("Start") == "9.975"


def test_grid_marker_is_not_a_position_mark() -> None:
    t = _track(cues=[GRID])
    assert position_marks(t) == []


def test_autogrid_phantom_hotcue_is_dropped() -> None:
    """Traktor mirrors the grid anchor as hotcue 0 named "AutoGrid"."""
    t = _track(cues=[GRID, Cue("AutoGrid", 0, 376.54, hotcue=0)])
    assert position_marks(t) == []


def test_the_phantom_is_dropped_under_any_name() -> None:
    """The name used to be the test. It leaked: the same mirror is spelled
    "AutoGrid", "n.n." and "Beat Marker" in one real collection, and the six
    that were not called "AutoGrid" reached the export."""
    for name in ("n.n.", "Beat Marker", ""):
        t = _track(cues=[GRID, Cue(name, 0, 376.54, hotcue=0)])
        assert position_marks(t) == [], f"{name!r} mirror survived"


def test_a_real_cue_at_the_grid_position_is_kept() -> None:
    """Slot 0 is where Traktor parks its mirror, so a cue there on the anchor
    is taken as one. Any other slot is the user's and survives."""
    t = _track(cues=[GRID, Cue("Intro", 0, 376.54, hotcue=3)])
    marks = position_marks(t)
    assert len(marks) == 1
    assert marks[0].get("Name") == "Intro"


def test_a_slot_zero_cue_away_from_the_grid_is_kept() -> None:
    """Slot 0 alone does not condemn a cue — 34 tracks in a real collection
    use it for an ordinary hotcue somewhere else in the track."""
    t = _track(cues=[GRID, Cue("Intro", 0, 45_000.0, hotcue=0)])
    marks = position_marks(t)
    assert len(marks) == 1
    assert marks[0].get("Num") == "0"


@pytest.mark.parametrize(
    ("traktor_type", "rb_type"),
    [
        (0, "0"),  # hotcue  → cue
        (1, "1"),  # fade-in
        (2, "2"),  # fade-out
        (3, "3"),  # load cue
    ],
)
def test_cue_types_map_across(traktor_type: int, rb_type: str) -> None:
    t = _track(cues=[Cue("x", traktor_type, 1000.0, hotcue=0)])
    assert position_marks(t)[0].get("Type") == rb_type


def test_loops_carry_an_end() -> None:
    t = _track(cues=[Cue("Loop", 5, 1000.0, len_ms=2000.0, hotcue=0)])
    mark = position_marks(t)[0]
    assert mark.get("Type") == "4"
    assert mark.get("Start") == "1.000"
    assert mark.get("End") == "3.000"


def test_a_zero_length_loop_is_not_a_loop() -> None:
    t = _track(cues=[Cue("Loop", 5, 1000.0, len_ms=0.0, hotcue=0)])
    assert "End" not in position_marks(t)[0].attrib


def test_cue_beyond_the_eight_slots_becomes_a_memory_cue() -> None:
    t = _track(cues=[Cue("Extra", 0, 1000.0, hotcue=9)])
    assert position_marks(t)[0].get("Num") == "-1"


def test_cue_without_a_hotcue_is_kept_as_a_memory_cue() -> None:
    """Dropping it would silently lose a marker the user placed."""
    t = _track(cues=[Cue("Memory", 0, 1000.0, hotcue=-1)])
    marks = position_marks(t)
    assert len(marks) == 1
    assert marks[0].get("Num") == "-1"


def test_hotcues_come_before_memory_cues_in_slot_order() -> None:
    t = _track(
        cues=[
            Cue("mem", 0, 5000.0, hotcue=-1),
            Cue("c", 0, 3000.0, hotcue=2),
            Cue("a", 0, 1000.0, hotcue=0),
        ]
    )
    assert [m.get("Num") for m in position_marks(t)] == ["0", "2", "-1"]


# ── Track element ───────────────────────────────────────────────────────────


def test_track_element_carries_the_grid_as_tempo() -> None:
    t = _track(cues=[GRID])
    el = track_element(t, 1)
    tempo = el.find("TEMPO")
    assert tempo is not None
    assert tempo.get("Inizio") == "0.377"
    assert tempo.get("Bpm") == "120.00"
    assert tempo.get("Metro") == "4/4"


def test_track_without_a_grid_has_no_tempo() -> None:
    assert track_element(_track(), 1).find("TEMPO") is None


def test_tempo_falls_back_to_the_track_bpm() -> None:
    t = _track(bpm=128.0, cues=[Cue("AutoGrid", 4, 0.0, grid_bpm=None)])
    assert track_element(t, 1).find("TEMPO").get("Bpm") == "128.00"


def test_rating_scale_is_passed_through_unchanged() -> None:
    """Traktor and Rekordbox both use 0/51/102/153/204/255."""
    assert track_element(_track(rating=153), 1).get("Rating") == "153"


def test_kind_comes_from_the_extension() -> None:
    assert track_element(_track(path="/m/a.flac"), 1).get("Kind") == "FLAC File"


# ── Parsing ─────────────────────────────────────────────────────────────────

REAL_ENTRY = """
<ENTRY TITLE="Uprising" ARTIST="Cosine Sun">
  <LOCATION DIR="/:Users/:x/:" FILE="a.mp3" VOLUME="Macintosh HD"/>
  <ALBUM TITLE="57"/>
  <INFO BITRATE="320000" GENRE="Electronic" PLAYCOUNT="1"
        PLAYTIME_FLOAT="338.5" RANKING="153" IMPORT_DATE="2010/3/14"/>
  <TEMPO BPM="140.000000"/>
  <MUSICAL_KEY VALUE="15"/>
  <CUE_V2 NAME="AutoGrid" TYPE="4" START="1530.0" LEN="0.0" HOTCUE="-1">
    <GRID BPM="140.0"/>
  </CUE_V2>
  <CUE_V2 NAME="tk: Drop" TYPE="0" START="70530.0" LEN="0.0" HOTCUE="2"/>
</ENTRY>
"""


@pytest.fixture
def parsed() -> Track:
    return parse_entry(ET.fromstring(REAL_ENTRY), "/Users/x/a.mp3")


def test_parse_reads_the_metadata(parsed: Track) -> None:
    assert parsed.title == "Uprising"
    assert parsed.artist == "Cosine Sun"
    assert parsed.album == "57"
    assert parsed.genre == "Electronic"
    assert parsed.bpm == pytest.approx(140.0)
    assert parsed.rating == 153
    assert parsed.play_count == 1


def test_parse_converts_bitrate_to_kbps(parsed: Track) -> None:
    assert parsed.bitrate == 320, "Traktor stores bits/s, Rekordbox wants kbps"


def test_parse_converts_the_import_date(parsed: Track) -> None:
    assert parsed.date_added == "2010-03-14"


def test_parse_finds_the_grid(parsed: Track) -> None:
    grid = parsed.grid_cue
    assert grid is not None
    assert grid.grid_bpm == pytest.approx(140.0)


def test_parse_cue_handles_a_missing_grid_child() -> None:
    cue = parse_cue(ET.fromstring('<CUE_V2 NAME="x" TYPE="0" START="5.0"/>'))
    assert cue.grid_bpm is None
    assert cue.start_ms == pytest.approx(5.0)


def test_parse_survives_a_sparse_entry() -> None:
    t = parse_entry(ET.fromstring("<ENTRY/>"), "/m/a.mp3")
    assert t.title == "" and t.bpm == 0.0 and t.cues == []


# ── Whole document ──────────────────────────────────────────────────────────


def test_library_structure() -> None:
    tracks = [_track(title="one"), _track(title="two")]
    root = build_library(tracks).getroot()

    assert root.tag == "DJ_PLAYLISTS"
    coll = root.find("COLLECTION")
    assert coll.get("Entries") == "2"
    assert [t.get("Name") for t in coll.findall("TRACK")] == ["one", "two"]
    assert [t.get("TrackID") for t in coll.findall("TRACK")] == ["1", "2"]


def test_playlists_reference_tracks_by_key_and_keep_their_order() -> None:
    tracks = [_track(title=n) for n in ("a", "b", "c")]
    root = build_library(tracks, playlists={"Set": [2, 0]}).getroot()

    node = root.find("PLAYLISTS").find("NODE").find("NODE")
    assert node.get("Name") == "Set"
    assert node.get("Entries") == "2"
    assert [t.get("Key") for t in node.findall("TRACK")] == ["3", "1"], (
        "playlist order is the point of exporting one — never re-sorted"
    )


def test_no_playlists_still_produces_a_valid_root() -> None:
    root = build_library([_track()]).getroot()
    assert root.find("PLAYLISTS").find("NODE").get("Count") == "0"


def test_document_round_trips_through_a_parser(tmp_path) -> None:
    tree = build_library(
        [_track(cues=[GRID, Cue("Drop", 0, 1000.0, hotcue=1)])], playlists={"Set": [0]}
    )
    dest = tmp_path / "rekordbox.xml"
    tree.write(dest, encoding="UTF-8", xml_declaration=True)

    reread = ET.parse(dest).getroot()
    mark = reread.find("COLLECTION").find("TRACK").find("POSITION_MARK")
    assert mark.get("Start") == "1.000"
    assert mark.get("Num") == "1"


def test_positions_survive_the_round_trip_within_a_millisecond(tmp_path) -> None:
    starts_ms = [376.54, 104361.315289, 136356.630641, 224343.747861]
    cues = [Cue(f"c{i}", 0, ms, hotcue=i) for i, ms in enumerate(starts_ms)]
    tree = build_library([_track(cues=cues)])
    dest = tmp_path / "rb.xml"
    tree.write(dest, encoding="UTF-8", xml_declaration=True)

    marks = ET.parse(dest).getroot().iter("POSITION_MARK")
    got = sorted(float(m.get("Start")) * 1000.0 for m in marks)
    for want, have in zip(sorted(starts_ms), got, strict=True):
        assert abs(want - have) <= 1.0, f"{want} ms drifted to {have} ms"


# ── Ratings ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("ranking", "expected"),
    [
        (0, 0),
        (51, 1),
        (102, 2),
        (153, 3),
        (204, 4),
        (255, 5),
        (50, 0),
        (60, 1),
        (300, 5),
        (-3, 0),
    ],
)
def test_stars_from_traktor_ranking(ranking: int, expected: int) -> None:
    """RANKING is 0–255 in steps of 51; the XML keeps that scale, the PDB
    wants 0–5. Off-ladder values round down; nothing leaves 0–5."""
    from core.rekordbox import stars

    assert stars(ranking) == expected
