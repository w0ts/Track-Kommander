"""The USB export from Traktor's memory: the same Track records the NML reader builds."""

from __future__ import annotations

from core import rekordbox as rb
from core.collection_source import parse_native_dump
from core.cue_points import Cue, TrackCues
from core.native_export import entries, entry, export_collection

PATH = "/Users/me/Music/Tracks/2025-10/Artist - Song.mp3"
# key path title artist album genre label comment comment2 bpm key rating plays import release
# bitrate playtime (libtk's collection dump, 17 columns)
DUMP = "\t".join(
    [
        "7",
        PATH,
        "Song",
        "Artist",
        "Album",
        "Techno",
        "Label",
        "a comment",
        "INFO",
        "128.000000",
        "21",
        "204",
        "3",
        str(2025 << 16 | 10 << 8 | 4),
        str(2024 << 16 | 1 << 8 | 1),
        "320000",
        "372",
    ]
)
CUES = [
    TrackCues(
        PATH,
        128.0,
        (
            Cue(type=4, hotcue=-1, start=112.5, name="AutoGrid"),
            Cue(type=0, hotcue=1, start=30112.5, name="drop"),
            Cue(type=5, hotcue=2, start=60000.0, name="loop", length=7500.0),
        ),
    )
]


def _native() -> rb.Track:
    _paths, meta = parse_native_dump(DUMP)
    by_path, order, skipped = export_collection(meta, CUES)
    assert order == [PATH] and skipped == 0
    return by_path[PATH]


def test_a_track_from_memory_has_the_nml_readers_fields() -> None:
    t = _native()
    assert (t.title, t.artist, t.album, t.genre, t.label, t.comment) == (
        "Song",
        "Artist",
        "Album",
        "Techno",
        "Label",
        "a comment",
    )
    assert t.bpm == 128.0 and t.musical_key == 21 and t.rating == 204 and t.play_count == 3
    assert t.bitrate == 320 and t.total_time_s == 372.0
    assert t.date_added == "2025-10-04" and t.release_date == "2024-01-01"
    grid = next(c for c in t.cues if c.is_grid)
    assert grid.start_ms == 112.5 and grid.grid_bpm == 128.0
    loop = next(c for c in t.cues if c.is_loop)
    assert loop.len_ms == 7500.0 and loop.hotcue == 2


def test_the_generated_entry_reads_back_as_the_same_track() -> None:
    """parse_entry(entry(track)) is the track: the stick's NML says what memory says."""
    t = _native()
    back = rb.parse_entry(entry(t), PATH)
    assert back == t


def test_entries_are_keyed_by_path_like_the_nml_reader() -> None:
    _paths, meta = parse_native_dump(DUMP)
    got = entries(meta, CUES)
    assert list(got) == [PATH]
    assert got[PATH].get("TITLE") == "Song"


def test_inside_traktor_the_sticks_playlists_never_read_the_nml(monkeypatch, tmp_path) -> None:
    import core.collection_source as cs
    from core import traktor_playlists

    monkeypatch.setenv("TK_EMBED", "/repo/traktor-native/re/embed_tk.py")
    monkeypatch.setattr(cs, "_native", lambda: parse_native_dump(DUMP))
    monkeypatch.setattr(cs, "_native_cues", lambda: CUES)
    nml = tmp_path / "collection.nml"  # does not exist: it would be an error to look
    got, version = traktor_playlists.entries_by_path(nml)
    assert list(got) == [PATH] and version


def test_an_older_libtk_without_the_duration_column_still_reads() -> None:
    old = DUMP.rsplit("\t", 1)[0]  # 16 columns
    _paths, meta = parse_native_dump(old)
    assert meta[PATH]["playtime_s"] == 0
    by_path, _order, _skipped = export_collection(meta, CUES)
    assert by_path[PATH].total_time_s == 0.0
