"""Tests for Traktor playlist parsing and the USB export.

Two things here are easy to get quietly wrong:

* Traktor's ``PRIMARYKEY`` is a volume-prefixed ``/:``-separated string, not a
  path. Mis-parsing it yields entries that silently resolve to nothing, and a
  playlist that exports as empty.
* Flattening a library into one folder on a stick creates filename collisions.
  Overwriting on collision loses tracks without saying so.
"""

from __future__ import annotations

import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from core.anlz_path import anlz_dir
from core.playlists import Playlist, _primarykey_to_path, parse_playlists
from core.traktor import _nml_build_path
from core.usb_export import (
    MAX_NAME,
    Source,
    conforms,
    folder_name,
    free_space,
    plan_export,
    run_export,
    sanitise,
)

NML = """<?xml version="1.0" encoding="UTF-8"?>
<NML VERSION="19">
 <COLLECTION ENTRIES="0"></COLLECTION>
 <PLAYLISTS>
  <NODE TYPE="FOLDER" NAME="$ROOT">
   <SUBNODES COUNT="3">
    <NODE TYPE="SMARTLIST" NAME="Recently added"></NODE>
    <NODE TYPE="PLAYLIST" NAME="Set A">
     <PLAYLIST ENTRIES="2" TYPE="LIST">
      <ENTRY><PRIMARYKEY TYPE="TRACK" KEY="Macintosh HD/:Music/:a.mp3"/></ENTRY>
      <ENTRY><PRIMARYKEY TYPE="TRACK" KEY="Macintosh HD/:Music/:b.mp3"/></ENTRY>
     </PLAYLIST>
    </NODE>
    <NODE TYPE="FOLDER" NAME="Gigs">
     <SUBNODES COUNT="1">
      <NODE TYPE="PLAYLIST" NAME="Friday">
       <PLAYLIST ENTRIES="1" TYPE="LIST">
        <ENTRY><PRIMARYKEY TYPE="TRACK" KEY="Macintosh HD/:Music/:c.mp3"/></ENTRY>
       </PLAYLIST>
      </NODE>
     </SUBNODES>
    </NODE>
   </SUBNODES>
  </NODE>
 </PLAYLISTS>
</NML>
"""


@pytest.fixture
def nml(tmp_path) -> Path:
    p = tmp_path / "collection.nml"
    p.write_text(NML)
    return p


# ── Playlist parsing ────────────────────────────────────────────────────────


def test_finds_every_playlist(nml: Path) -> None:
    names = [p.name for p in parse_playlists(nml)]
    assert names == ["Recently added", "Set A", "Friday"]


#: "Set A" in the fixture, spelled as the platform spells a path: that is how
#: the collection's paths are spelled, which these are looked up in.
_SET_A = [str(Path("/Music/a.mp3")), str(Path("/Music/b.mp3"))]


def test_entries_are_resolved_to_paths(nml: Path) -> None:
    sets = {p.name: p.paths for p in parse_playlists(nml)}
    assert sets["Set A"] == _SET_A


def test_order_is_preserved(nml: Path) -> None:
    """A playlist's order is the whole point of exporting one."""
    pl = next(p for p in parse_playlists(nml) if p.name == "Set A")
    assert pl.paths == _SET_A


def test_folders_appear_in_the_display_path(nml: Path) -> None:
    friday = next(p for p in parse_playlists(nml) if p.name == "Friday")
    assert friday.display == "Gigs / Friday"


def test_root_is_not_shown_as_a_folder(nml: Path) -> None:
    set_a = next(p for p in parse_playlists(nml) if p.name == "Set A")
    assert set_a.display == "Set A", "$ROOT is Traktor's internal top level"


def test_smartlists_are_flagged_not_silently_empty(nml: Path) -> None:
    smart = next(p for p in parse_playlists(nml) if p.name == "Recently added")
    assert smart.smart is True
    assert len(smart) == 0


def test_missing_playlists_section_is_not_an_error(tmp_path) -> None:
    p = tmp_path / "empty.nml"
    p.write_text('<?xml version="1.0"?><NML><COLLECTION/></NML>')
    assert parse_playlists(p) == []


def test_unreadable_nml_raises_clearly(tmp_path) -> None:
    with pytest.raises(RuntimeError, match="cannot read playlists"):
        parse_playlists(tmp_path / "nope.nml")


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        ("Macintosh HD/:Users/:x/:a.mp3", "/Users/x/a.mp3"),
        ("C:/:Music/:a.mp3", "C:/Music/a.mp3"),
        ("", ""),
        # Traktor writes a literal ":" in a filename as "//". The collection
        # decoder has always undone that; this one did not, so every track with a
        # colon in its name resolved to a path that does not exist and silently
        # dropped out of the playlist — nine of them in a real 897-track library.
        ("Macintosh HD/:Music/:Re//elected.mp3", "/Music/Re:elected.mp3"),
        ("Macintosh HD/:Music/:MUSE// ADE 2024 - Medo.mp3", "/Music/MUSE: ADE 2024 - Medo.mp3"),
    ],
)
def test_primarykey_conversion(key: str, expected: str) -> None:
    # Written above with "/"; compared as the platform spells a path, which
    # is how the collection's own paths are spelled.
    assert _primarykey_to_path(key) == (os.path.normpath(expected) if expected else "")


def test_primarykey_and_collection_agree_on_a_colon() -> None:
    """The two decoders must produce the same path for the same file, or a
    playlist entry cannot be matched against the collection that holds it."""
    loc = ET.fromstring('<LOCATION DIR="/:Music/:" FILE="Re//elected.mp3" VOLUME="Macintosh HD"/>')
    key = "Macintosh HD/:Music/:Re//elected.mp3"
    assert _primarykey_to_path(key) == _nml_build_path(loc)


def test_primarykey_and_collection_agree_on_a_windows_drive() -> None:
    """On Windows the collection's paths are spelled with backslashes. A
    playlist entry spelled with "/" named none of them, so an export by
    playlist found every one of its tracks missing."""
    loc = ET.fromstring('<LOCATION DIR="/:Music/:Sets/:" FILE="a.mp3" VOLUME="C:"/>')
    assert _primarykey_to_path("C:/:Music/:Sets/:a.mp3") == _nml_build_path(loc)


def test_playlist_len() -> None:
    assert len(Playlist("x", paths=["a", "b"])) == 2


# ── Filename sanitising ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Artist - Title.mp3", "Artist - Title.mp3"),
        ("A/B: C?.mp3", "A_B_ C_.mp3"),
        ('bad"name<>|.mp3', "bad_name_.mp3"),
        ("trailing dot. ", "trailing dot"),
    ],
)
def test_sanitise_for_fat32(raw: str, expected: str) -> None:
    assert sanitise(raw) == expected


def test_sanitise_keeps_the_extension_when_truncating() -> None:
    long = "x" * 300 + ".mp3"
    got = sanitise(long)
    assert got.endswith(".mp3")
    assert len(got) <= MAX_NAME


def test_names_are_cut_short() -> None:
    """Rekordbox cut this to 48 characters; an XDJ-1000 froze on sticks of
    ours at 69 and at 48. Every name is now 20 at most, extension included."""
    raw = "IGLO - 5 Years Backhaus Fundraiser Compilation Part 3 - Alleviate.mp3"
    assert sanitise(raw) == "IGLO - 5 Years B.mp3"
    assert MAX_NAME == 20


def test_a_folder_is_cut_without_hunting_for_an_extension() -> None:
    """A dot in an album name is not an extension: "Vol. 2 …" must not be
    cut as though ". 2 …" had to survive."""
    assert (
        folder_name("5 Years Backhaus Fundraiser Compilation Part 3", "x") == "5 Years Backhaus Fun"
    )
    assert folder_name("Warehouse Vol. 2 Deluxe", "x") == "Warehouse Vol. 2 Del"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Kölsch.mp3", "Kolsch.mp3"),
        ("Beyoncé – Halo.mp3", "Beyonce _ Halo.mp3"),
        ("A  +  B!!.mp3", "A _ B_.mp3"),
        ("D&B (Remix).mp3", "D&B (Remix).mp3"),
        (".hidden.mp3", "hidden.mp3"),
        ("a'b,c;d.mp3", "a_b_c_d.mp3"),
    ],
)
def test_only_plain_ascii_reaches_the_stick(raw: str, expected: str) -> None:
    """Letters, digits, space and - _ . ( ) &, nothing else: the name is only
    seen in a player's folder view, and the database carries the real title,
    so there is nothing to gain from anything an old player might choke on."""
    assert sanitise(raw) == expected


@pytest.mark.parametrize(
    ("path", "ok"),
    [
        ("/Contents/IGLO/5 Years Backhaus Fun/IGLO - 5 Years B.mp3", True),
        ("/Contents/IGLO/5 Years Backhaus Fundraiser Compilation Part 3/x.mp3", False),
        ("/Contents/A/B/IGLO - 5 Years Backhaus Fundraiser Compilati.mp3", False),
        ("/Contents/Kölsch/B/x.mp3", False),
        ("/Contents/track.mp3", True),
        ("/Elsewhere/x.mp3", False),
        ("/Contents/" + "/".join(["a" * 20] * 4) + "/x.mp3", True),
        ("/Contents/" + "/".join(["a" * 20] * 5) + "/x.mp3", False),
    ],
)
def test_conforms_checks_every_name_in_the_path(path: str, ok: bool) -> None:

    assert conforms(path) is ok


def test_a_pinned_track_with_a_long_name_is_renamed(tmp_path) -> None:
    """A key written by an older build holds long names; the manifest pins
    them. The pin must not keep a name that breaks today's rules."""
    src = tmp_path / "src"
    src.mkdir()
    f = src / "IGLO - 5 Years Backhaus Fundraiser Compilation Part 3 - Alleviate.mp3"
    f.write_bytes(b"x")
    old = (
        tmp_path
        / "usb"
        / "Contents"
        / "IGLO"
        / "5 Years Backhaus Fundraiser Compilation Part 3"
        / "IGLO - 5 Years Backhaus Fundraiser Compilati.mp3"
    )
    old.parent.mkdir(parents=True)
    old.write_bytes(b"x")
    plan = plan_export(
        [Source(str(f), artist="IGLO", album="5 Years Backhaus Fundraiser Compilation Part 3")],
        tmp_path / "usb",
        pinned={str(f): str(old)},
    )
    rel = "/" + plan.copies[0].target.relative_to(tmp_path / "usb").as_posix()
    assert rel == "/Contents/IGLO/5 Years Backhaus Fun/IGLO - 5 Years B.mp3"


def test_a_name_with_nothing_left_is_track() -> None:
    assert sanitise("東京 - 夜.mp3") == "track.mp3"
    assert sanitise("Кино.mp3") == "track.mp3"


def test_a_longer_extension_still_fits() -> None:
    got = sanitise("y" * 80 + ".flac")
    assert got.endswith(".flac")
    assert len(got) == MAX_NAME


def test_a_collision_suffix_still_fits(tmp_path) -> None:
    """Two tracks whose names agree for 20 characters would collide once
    cut; the second gets a suffix, and the suffix must not break the cap."""
    src = tmp_path / "src"
    src.mkdir()
    base = "IGLO - 5 Years Backhaus Fundraiser Compilation Part 3 - "
    files = []
    for title in ("Alleviate", "Another"):
        f = src / f"{base}{title}.mp3"
        f.write_bytes(b"x")
        files.append(str(f))
    plan = plan_export(files, tmp_path / "usb")
    names = [c.target.name for c in plan.copies]
    assert names[0] == "IGLO - 5 Years B.mp3"
    assert names[1] == "IGLO - 5 Yea (2).mp3"
    assert all(len(n) <= MAX_NAME for n in names)


def test_a_path_whose_analysis_folder_is_taken_is_renamed(tmp_path) -> None:
    """A player finds a track's analysis in a folder hashed from its path.
    /Contents/A/B/t1013.mp3 and t7700.mp3 hash to the same one, so the
    second is renamed rather than left to read the first one's cues."""
    src = tmp_path / "src"
    src.mkdir()
    items = []
    for name in ("t1013.mp3", "t7700.mp3"):
        f = src / name
        f.write_bytes(b"x")
        items.append(Source(str(f), artist="A", album="B"))
    plan = plan_export(items, tmp_path / "usb")
    paths = ["/" + c.target.relative_to(tmp_path / "usb").as_posix() for c in plan.copies]
    assert paths[0] == "/Contents/A/B/t1013.mp3"
    assert paths[1] != "/Contents/A/B/t7700.mp3"
    assert paths[1].startswith("/Contents/A/B/t7700")
    assert anlz_dir(paths[0]) != anlz_dir(paths[1])


def test_a_cut_does_not_end_in_a_space() -> None:
    raw = "A" * 15 + " rest of a long title.mp3"
    got = sanitise(raw)
    assert got == "A" * 15 + ".mp3"


def test_sanitise_never_returns_empty() -> None:
    assert sanitise("///") == "track"
    assert sanitise("") == "track"


# ── Copy planning ───────────────────────────────────────────────────────────


@pytest.fixture
def sources(tmp_path) -> list[str]:
    out = []
    for sub, name in (("one", "track.mp3"), ("two", "track.mp3"), ("one", "other.mp3")):
        d = tmp_path / "src" / sub
        d.mkdir(parents=True, exist_ok=True)
        f = d / name
        f.write_bytes(b"x" * 1024)
        out.append(str(f))
    return out


def test_collisions_get_a_suffix_rather_than_overwriting(sources, tmp_path) -> None:
    """Flattening folders makes duplicate names likely; losing one silently
    would be the worst possible outcome."""
    plan = plan_export(sources, tmp_path / "usb")
    names = [c.target.name for c in plan.copies]
    assert names == ["track.mp3", "track (2).mp3", "other.mp3"]
    assert len(set(names)) == len(names)


def test_missing_sources_are_reported_not_dropped(sources, tmp_path) -> None:
    plan = plan_export([*sources, "/nowhere/gone.mp3"], tmp_path / "usb")
    assert len(plan.missing) == 1
    assert plan.missing[0].source.name == "gone.mp3"
    assert len(plan.to_copy) == 3


def test_planning_writes_nothing(sources, tmp_path) -> None:
    dest = tmp_path / "usb"
    plan_export(sources, dest)
    assert not dest.exists()


def test_bytes_needed_is_the_sum_of_the_copies(sources, tmp_path) -> None:
    assert plan_export(sources, tmp_path / "usb").bytes_needed == 3 * 1024


def test_mapping_points_source_to_destination(sources, tmp_path) -> None:
    plan = plan_export(sources, tmp_path / "usb")
    mapping = plan.mapping
    assert len(mapping) == 3
    contents = tmp_path / "usb" / "Contents"
    assert all(Path(v).is_relative_to(contents) for v in mapping.values())


# ── Copying ─────────────────────────────────────────────────────────────────


def test_run_export_copies_everything(sources, tmp_path) -> None:
    plan = plan_export(sources, tmp_path / "usb")
    copied, errors = run_export(plan)
    assert (copied, errors) == (3, [])
    assert len(list((tmp_path / "usb" / "Contents").rglob("*.mp3"))) == 3


def test_no_part_files_survive(sources, tmp_path) -> None:
    plan = plan_export(sources, tmp_path / "usb")
    run_export(plan)
    assert list((tmp_path / "usb").rglob("*.part")) == []


def test_already_copied_files_are_skipped_on_a_rerun(sources, tmp_path) -> None:
    dest = tmp_path / "usb"
    run_export(plan_export(sources, dest))
    second = plan_export(sources, dest)
    assert second.to_copy == [], "re-exporting should not recopy identical files"


def test_stop_halts_the_copy(sources, tmp_path) -> None:
    plan = plan_export(sources, tmp_path / "usb")
    copied, _ = run_export(plan, should_stop=lambda: True)
    assert copied == 0


def test_a_failed_copy_is_reported_and_others_continue(sources, tmp_path, monkeypatch) -> None:
    plan = plan_export(sources, tmp_path / "usb")
    real = __import__("shutil").copyfile
    calls = {"n": 0}

    def _flaky(src, dst, *a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("device removed")
        return real(src, dst, *a, **k)

    monkeypatch.setattr("core.usb_export.shutil.copyfile", _flaky)
    copied, errors = run_export(plan)
    assert copied == 2
    assert len(errors) == 1


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX path assumptions")
def test_free_space_reports_something(tmp_path) -> None:
    assert free_space(tmp_path) > 0


def test_free_space_of_a_nonexistent_path_walks_up(tmp_path) -> None:
    assert free_space(tmp_path / "not" / "there") > 0


# ── Folder layout ───────────────────────────────────────────────────────────
# One flat Contents/ dies on FAT32 somewhere in the thousands of tracks
# (65,534 directory entries, several per long filename) and gets slow on
# exFAT well before that. Rekordbox files by artist and album; so do we.


def test_tracks_are_filed_by_artist_and_album(sources, tmp_path) -> None:
    from core.usb_export import Source

    plan = plan_export(
        [Source(sources[0], artist="Daft Punk", album="Discovery")], tmp_path / "usb"
    )
    assert plan.copies[0].target == (
        tmp_path / "usb" / "Contents" / "Daft Punk" / "Discovery" / "track.mp3"
    )


def test_unknown_metadata_still_has_a_home(sources, tmp_path) -> None:
    plan = plan_export(sources[:1], tmp_path / "usb")
    rel = plan.copies[0].target.relative_to(tmp_path / "usb")
    assert rel.parts == ("Contents", "Unknown Artist", "Unknown Album", "track.mp3")


def test_folder_names_are_sanitised_like_filenames(sources, tmp_path) -> None:
    from core.usb_export import Source

    plan = plan_export([Source(sources[0], artist="AC/DC", album="Live: 1979 ")], tmp_path / "usb")
    rel = plan.copies[0].target.relative_to(tmp_path / "usb")
    assert rel.parts[1:3] == ("AC_DC", "Live_ 1979")


def test_same_name_in_different_albums_needs_no_suffix(sources, tmp_path) -> None:
    from core.usb_export import Source

    plan = plan_export(
        [Source(sources[0], artist="A", album="One"), Source(sources[1], artist="A", album="Two")],
        tmp_path / "usb",
    )
    assert [c.target.name for c in plan.copies] == ["track.mp3", "track.mp3"]


def test_same_name_in_one_album_still_gets_a_suffix(sources, tmp_path) -> None:
    from core.usb_export import Source

    plan = plan_export(
        [Source(sources[0], artist="A", album="One"), Source(sources[1], artist="A", album="One")],
        tmp_path / "usb",
    )
    assert [c.target.name for c in plan.copies] == ["track.mp3", "track (2).mp3"]


def test_a_track_already_on_the_key_stays_where_it_is(sources, tmp_path) -> None:
    """Keys written before the folder layout hold their audio flat. Adding to
    one must not recopy every track into a folder and orphan the flat copy."""
    from core.usb_export import Source

    dest = tmp_path / "usb"
    (dest / "Contents").mkdir(parents=True)
    (dest / "Contents" / "track.mp3").write_bytes(b"x" * 1024)
    plan = plan_export([Source(sources[0], artist="A", album="One")], dest)
    assert plan.copies[0].target == dest / "Contents" / "track.mp3"
    assert plan.to_copy == []


# ── Accents are folded to ASCII ─────────────────────────────────────────────
# macOS writes decomposed names to exFAT while the database row keeps the
# composed form; whether a player matches the two is not something to find
# out at a gig. Folding to ASCII sidesteps it wherever there is an ASCII form.


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Kölsch - Grey.mp3", "Kolsch - Grey.mp3"),
        ("Maōh.mp3", "Maoh.mp3"),
        ("Ólafur Arnalds", "Olafur Arnalds"),
        ("Møme", "Mome"),
        ("Straße Ærø Łódź", "Strasse AEro Lodz"),
        ("Kolsch.mp3", "Kolsch.mp3"),
    ],
)
def test_latin_accents_are_folded(raw: str, expected: str) -> None:
    assert sanitise(raw) == expected


def test_a_decomposed_name_folds_the_same_as_a_composed_one() -> None:
    import unicodedata

    composed = "Kölsch.mp3"
    decomposed = unicodedata.normalize("NFD", composed)
    assert sanitise(decomposed) == sanitise(composed) == "Kolsch.mp3"


def test_folders_are_folded_too(sources, tmp_path) -> None:
    from core.usb_export import Source

    plan = plan_export([Source(sources[0], artist="Kölsch", album="Fabric 86")], tmp_path / "usb")
    rel = plan.copies[0].target.relative_to(tmp_path / "usb")
    assert rel.parts[1] == "Kolsch"


def test_a_folder_that_exists_in_another_case_is_reused(sources, tmp_path) -> None:
    """Benza and BENZA are one folder on the key; the row must say the
    spelling the drive has, or the sweep sees an orphan."""
    from core.usb_export import Source

    dest = tmp_path / "usb"
    (dest / "Contents" / "BENZA" / "Album A").mkdir(parents=True)
    plan = plan_export(
        [
            Source(sources[0], artist="Benza", album="Album A"),
            Source(sources[1], artist="benza", album="Album B"),
        ],
        dest,
    )
    rel = [c.target.relative_to(dest).parts for c in plan.copies]
    assert rel[0][1:3] == ("BENZA", "Album A")
    assert rel[1][1:3] == ("BENZA", "Album B")
