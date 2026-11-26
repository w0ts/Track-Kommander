"""Plain .m3u8 playlists written beside the Pioneer database.

``export.pdb`` is read by CDJs and nothing else. These are for everything
else that plugs into the same stick, so the things that matter are the ones
that break on other people's hardware: relative paths, UTF-8, LF endings,
and not writing a file that points at a track which never got copied.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from core.m3u import (
    M3U_DIRNAME,
    MARKER,
    filename_for,
    is_ours,
    render,
    write_playlists,
)


@dataclass
class T:
    source: Path = Path("/music/x.mp3")
    device_path: str = "/Contents/x.mp3"
    title: str = "Title"
    artist: str = "Artist"
    duration_s: int = 200


def _tracks(n=3):
    return [
        T(
            source=Path(f"/music/t{i}.mp3"),
            device_path=f"/Contents/t{i}.mp3",
            title=f"Title {i}",
            artist=f"Artist {i}",
            duration_s=100 + i,
        )
        for i in range(n)
    ]


# ── The file itself ─────────────────────────────────────────────────────────


def test_it_starts_with_the_header() -> None:
    assert render(_tracks(), [0]).startswith("#EXTM3U\n")


def test_each_track_gets_a_duration_and_a_label() -> None:
    body = render(_tracks(), [1])
    assert "#EXTINF:101,Artist 1 - Title 1" in body


def test_paths_are_relative_to_the_playlist_not_the_stick() -> None:
    """The stick mounts somewhere different on every machine, so an absolute
    path is wrong everywhere but here."""
    body = render(_tracks(), [0])
    assert "../Contents/t0.mp3" in body
    assert "/Contents/t0.mp3\n" not in body.replace("../Contents/t0.mp3", "")


def test_order_is_the_order_given() -> None:
    body = render(_tracks(), [2, 0, 1])
    paths = [ln for ln in body.splitlines() if ln.startswith("..")]
    assert paths == ["../Contents/t2.mp3", "../Contents/t0.mp3", "../Contents/t1.mp3"]


def test_a_track_that_never_reached_the_stick_is_skipped() -> None:
    """No device_path means the copy failed. A line pointing at nothing is
    worse than a shorter playlist."""
    tracks = _tracks()
    tracks[1].device_path = ""
    body = render(tracks, [0, 1, 2])
    assert "t1.mp3" not in body
    assert body.count("#EXTINF") == 2


def test_an_out_of_range_index_is_ignored() -> None:
    assert render(_tracks(2), [0, 99, -1]).count("#EXTINF") == 1


def test_a_missing_artist_leaves_no_dangling_dash() -> None:
    tracks = [T(artist="", title="Just A Title")]
    assert "#EXTINF:200,Just A Title" in render(tracks, [0])


def test_a_missing_title_falls_back_to_the_filename() -> None:
    tracks = [T(source=Path("/music/Some File.mp3"), title="", artist="")]
    assert "Some File" in render(tracks, [0])


def test_a_newline_in_a_tag_cannot_forge_a_line() -> None:
    """A tag is untrusted text from the file; a raw newline in it would make
    the rest look like a path."""
    tracks = [T(title="Bad\nTitle")]
    body = render(tracks, [0])
    assert len(body.splitlines()) == 4  # header, marker, EXTINF, path
    assert "Bad Title" in body


# ── Filenames ───────────────────────────────────────────────────────────────


def test_an_unsafe_name_is_made_safe() -> None:
    name = filename_for("Friday / Saturday: late", set())
    assert "/" not in name and ":" not in name
    assert name.endswith(".m3u8")


def test_two_playlists_of_the_same_name_do_not_collide() -> None:
    """Traktor allows the same name in two folders; one directory cannot."""
    taken: set[str] = set()
    first = filename_for("Set", taken)
    second = filename_for("Set", taken)
    assert first != second


def test_an_empty_name_still_produces_a_file() -> None:
    assert filename_for("", set()).endswith(".m3u8")


# ── Writing ─────────────────────────────────────────────────────────────────


def test_playlists_land_in_their_own_folder(tmp_path) -> None:
    written = write_playlists(tmp_path, _tracks(), {"Set A": [0, 1]})
    assert len(written) == 1
    assert written[0].parent == tmp_path / M3U_DIRNAME
    assert written[0].name == "Set A.m3u8"


def test_nothing_is_written_when_there_are_no_playlists(tmp_path) -> None:
    assert write_playlists(tmp_path, _tracks(), {}) == []
    assert not (tmp_path / M3U_DIRNAME).exists()


def test_an_empty_playlist_is_not_written(tmp_path) -> None:
    """A file containing only the header is noise on the stick."""
    written = write_playlists(tmp_path, _tracks(), {"Empty": []})
    assert written == []


def test_a_playlist_whose_tracks_all_failed_is_not_written(tmp_path) -> None:
    tracks = _tracks(2)
    for t in tracks:
        t.device_path = ""
    assert write_playlists(tmp_path, tracks, {"Set A": [0, 1]}) == []


def test_the_file_is_utf8_and_survives_an_accent(tmp_path) -> None:
    """The 8 in m3u8 is the whole point of choosing it over m3u."""
    tracks = [T(artist="Nørbak", title="Amigo")]
    written = write_playlists(tmp_path, tracks, {"Set": [0]})
    assert written[0].read_text(encoding="utf-8").count("Nørbak") == 1


def test_lines_end_with_lf_not_crlf(tmp_path) -> None:
    """Some hardware players show the CR as a stray character in the path."""
    written = write_playlists(tmp_path, _tracks(), {"Set": [0]})
    assert b"\r\n" not in written[0].read_bytes()


def test_every_written_path_resolves_to_a_real_location(tmp_path) -> None:
    """The relative path has to land on the audio from where the file sits."""
    (tmp_path / "Contents").mkdir()
    (tmp_path / "Contents" / "t0.mp3").write_bytes(b"x")
    written = write_playlists(tmp_path, _tracks(1), {"Set": [0]})
    line = [ln for ln in written[0].read_text().splitlines() if not ln.startswith("#")][0]
    assert (written[0].parent / line).resolve().is_file()


# ── Folders ─────────────────────────────────────────────────────────────────
# A playlist keyed by its tree position — folders outermost first, name last —
# lands in the same folders under PLAYLISTS/, and its paths climb out of them.


def test_a_playlist_in_folders_lands_in_the_same_folders(tmp_path) -> None:
    written = write_playlists(tmp_path, _tracks(), {("Gigs", "2026"): [0], ("Warmup",): [1]})
    assert sorted(p.relative_to(tmp_path / M3U_DIRNAME).as_posix() for p in written) == [
        "Gigs/2026.m3u8",
        "Warmup.m3u8",
    ]


def test_a_nested_playlist_climbs_out_of_every_folder(tmp_path) -> None:
    (tmp_path / "Contents").mkdir()
    (tmp_path / "Contents" / "t0.mp3").write_bytes(b"x")
    written = write_playlists(tmp_path, _tracks(1), {("Gigs", "Summer", "Set"): [0]})
    line = [ln for ln in written[0].read_text().splitlines() if not ln.startswith("#")][0]
    assert line == "../../../Contents/t0.mp3"
    assert (written[0].parent / line).resolve().is_file()


def test_same_name_in_two_folders_keeps_its_own_name(tmp_path) -> None:
    written = write_playlists(tmp_path, _tracks(), {("A", "Set"): [0], ("B", "Set"): [1]})
    assert sorted(p.relative_to(tmp_path / M3U_DIRNAME).as_posix() for p in written) == [
        "A/Set.m3u8",
        "B/Set.m3u8",
    ]


def test_an_unsafe_folder_name_is_made_safe(tmp_path) -> None:
    written = write_playlists(tmp_path, _tracks(), {("a:b", "Set"): [0]})
    assert written[0].parent.parent == tmp_path / M3U_DIRNAME
    assert ":" not in written[0].parent.name


# ── Whose playlist it is ────────────────────────────────────────────────────
# A playlist moved into a folder leaves its old copy at the top level, and
# that copy is removed. It used to be removed on its name alone — so a
# playlist somebody else had put there, of the same name, went with it.
# Every file written here now says so, and only one that does is removed.


def test_a_playlist_says_who_wrote_it() -> None:
    lines = render(_tracks(), [0]).splitlines()
    assert lines[0] == "#EXTM3U", "players look for this on the first line"
    assert lines[1] == MARKER
    assert lines[1].startswith("#"), "a comment, so a player skips it"


def test_our_own_flat_copy_is_removed(tmp_path) -> None:
    [flat] = write_playlists(tmp_path, _tracks(), {"Set": [0]})
    assert is_ours(flat)

    write_playlists(tmp_path, _tracks(), {("Gigs", "Set"): [0]})

    assert not flat.exists()
    assert (tmp_path / M3U_DIRNAME / "Gigs" / "Set.m3u8").is_file()


def test_a_playlist_we_did_not_write_is_left_alone(tmp_path) -> None:
    out = tmp_path / M3U_DIRNAME
    out.mkdir()
    theirs = "#EXTM3U\n#EXTINF:200,Someone - Else\n../Contents/else.mp3\n"
    (out / "Set.m3u8").write_text(theirs)

    write_playlists(tmp_path, _tracks(), {("Gigs", "Set"): [0]})

    assert (out / "Set.m3u8").read_text() == theirs


def test_a_file_that_cannot_be_read_is_not_ours(tmp_path) -> None:
    assert not is_ours(tmp_path / "nothing.m3u8")
    (tmp_path / "bytes.m3u8").write_bytes(b"\xff\xfe\x00 not text")
    assert not is_ours(tmp_path / "bytes.m3u8")


def test_a_top_level_playlist_of_that_name_is_not_removed(tmp_path) -> None:
    written = write_playlists(tmp_path, _tracks(), {("Set",): [0], ("Gigs", "Set"): [1]})
    assert (tmp_path / M3U_DIRNAME / "Set.m3u8") in written
    assert (tmp_path / M3U_DIRNAME / "Set.m3u8").is_file()


def test_an_unchanged_playlist_is_not_rewritten(tmp_path) -> None:
    [first] = write_playlists(tmp_path, _tracks(), {"Set": [0, 1]})
    os.utime(first, ns=(1_000_000_000, 1_000_000_000))
    [again] = write_playlists(tmp_path, _tracks(), {"Set": [0, 1]})
    assert again == first and first.stat().st_mtime_ns == 1_000_000_000
