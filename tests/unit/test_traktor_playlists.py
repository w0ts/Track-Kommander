"""Traktor playlist .nml files written beside the Pioneer database.

Traktor has no device database. What it reads from a stick is a playlist
``.nml`` through *Import Playlist*, the same shape its own *Export Playlist*
writes. The things that matter: each track's ENTRY is the collection's own
(cues, grid, key — nothing converted), its LOCATION points at the copy on the
stick, and the PRIMARYKEY in the playlist matches that LOCATION exactly.
"""

from __future__ import annotations

import os
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from core.traktor_playlists import (
    NML_DIRNAME,
    entries_by_path,
    is_ours,
    location_of,
    render,
    volume_of,
    write_playlists,
)

COLLECTION = """<?xml version="1.0" encoding="UTF-8" standalone="no" ?>
<NML VERSION="20"><HEAD COMPANY="www.native-instruments.com" PROGRAM="Traktor Pro 4"></HEAD>
<COLLECTION ENTRIES="3">
<ENTRY AUDIO_ID="A0" TITLE="Title 0" ARTIST="Artist 0"><LOCATION DIR="/:music/:" FILE="t0.mp3" VOLUME="Macintosh HD" VOLUMEID="Macintosh HD"></LOCATION><TEMPO BPM="124.000000" BPM_QUALITY="100.000000"></TEMPO><CUE_V2 NAME="Drop" DISPL_ORDER="0" TYPE="0" START="61234.5" LEN="0.0" REPEATS="-1" HOTCUE="2"></CUE_V2></ENTRY>
<ENTRY AUDIO_ID="A1" TITLE="Title 1" ARTIST="Artist 1"><LOCATION DIR="/:music/:" FILE="t1.mp3" VOLUME="Macintosh HD" VOLUMEID="Macintosh HD"></LOCATION></ENTRY>
<ENTRY AUDIO_ID="A2" TITLE="Title 2" ARTIST="Artist 2"><LOCATION DIR="/:music/:" FILE="t2.mp3" VOLUME="Macintosh HD" VOLUMEID="Macintosh HD"></LOCATION></ENTRY>
</COLLECTION>
<PLAYLISTS></PLAYLISTS>
</NML>
"""


@dataclass
class T:
    source: Path
    device_path: str
    title: str = ""
    artist: str = ""


def _tracks(n=3):
    return [
        T(
            source=Path(f"/music/t{i}.mp3"),
            device_path=f"/Contents/t{i}.mp3",
            title=f"Title {i}",
            artist=f"Artist {i}",
        )
        for i in range(n)
    ]


def _collection(tmp_path: Path) -> Path:
    nml = tmp_path / "collection.nml"
    nml.write_text(COLLECTION, encoding="utf-8")
    return nml


def _stick(tmp_path: Path) -> Path:
    stick = tmp_path / "Volumes" / "DJSTICK"
    stick.mkdir(parents=True)
    return stick


def _mounted_at(stick: Path):
    return lambda p: Path(p) == stick


# ── Where the stick is ──────────────────────────────────────────────────────


def test_the_volume_is_the_sticks_name_on_macos(tmp_path) -> None:
    stick = _stick(tmp_path)
    mount, volume = volume_of(stick / "sub", is_mount=_mounted_at(stick), windows=False)
    assert mount == stick
    assert volume == "DJSTICK"


def test_the_volume_is_spelled_as_the_filesystem_lists_it() -> None:
    """macOS opens /Volumes/Key for a stick named KEY; Traktor wants KEY."""
    mount, volume = volume_of(
        Path("/Volumes/Key/sub"),
        windows=False,
        is_mount=lambda p: Path(p) == Path("/Volumes/Key"),
        listdir=lambda p: ["Macintosh HD", "KEY"],
    )
    assert mount == Path("/Volumes/Key")
    assert volume == "KEY"


def test_the_volume_is_the_drive_letter_on_windows() -> None:
    mount, volume = volume_of(Path("E:/"), is_mount=lambda p: True, windows=True, anchor="E:\\")
    assert volume == "E:"


def test_location_is_relative_to_the_volume_root(tmp_path) -> None:
    stick = _stick(tmp_path)
    loc = location_of(stick / "Contents" / "a" / "x.mp3", stick, "DJSTICK")
    assert loc == {
        "DIR": "/:Contents/:a/:",
        "FILE": "x.mp3",
        "VOLUME": "DJSTICK",
        "VOLUMEID": "DJSTICK",
    }


# ── The file itself ─────────────────────────────────────────────────────────


def _parse(body: bytes) -> ET.Element:
    return ET.fromstring(body)


def test_entries_are_the_collections_own_with_the_stick_location(tmp_path) -> None:
    stick = _stick(tmp_path)
    entries, version = entries_by_path(_collection(tmp_path))
    root = _parse(
        render(
            _tracks(),
            [0],
            "Warmup",
            entries=entries,
            mount=stick,
            volume="DJSTICK",
            dest=stick,
            version=version,
        )
    )
    entry = root.find("COLLECTION/ENTRY")
    assert entry.get("AUDIO_ID") == "A0"
    cue = entry.find("CUE_V2")
    assert cue.get("START") == "61234.5" and cue.get("HOTCUE") == "2"
    loc = entry.find("LOCATION")
    assert loc.get("DIR") == "/:Contents/:"
    assert loc.get("VOLUME") == "DJSTICK"


def test_the_source_collection_is_not_changed(tmp_path) -> None:
    stick = _stick(tmp_path)
    entries, version = entries_by_path(_collection(tmp_path))
    render(
        _tracks(),
        [0],
        "Warmup",
        entries=entries,
        mount=stick,
        volume="DJSTICK",
        dest=stick,
        version=version,
    )
    assert entries[str(Path("/music/t0.mp3"))].find("LOCATION").get("DIR") == "/:music/:"


def test_primary_keys_match_the_locations_in_order(tmp_path) -> None:
    stick = _stick(tmp_path)
    entries, version = entries_by_path(_collection(tmp_path))
    root = _parse(
        render(
            _tracks(),
            [2, 0],
            "Peak",
            entries=entries,
            mount=stick,
            volume="DJSTICK",
            dest=stick,
            version=version,
        )
    )
    node = root.find("PLAYLISTS/NODE/SUBNODES/NODE")
    assert node.get("TYPE") == "PLAYLIST" and node.get("NAME") == "Peak"
    keys = [k.get("KEY") for k in node.iter("PRIMARYKEY")]
    assert keys == ["DJSTICK/:Contents/:t2.mp3", "DJSTICK/:Contents/:t0.mp3"]
    assert node.find("PLAYLIST").get("ENTRIES") == "2"
    assert root.find("COLLECTION").get("ENTRIES") == "2"
    assert root.get("VERSION") == "20"


def test_a_track_that_never_reached_the_stick_is_skipped(tmp_path) -> None:
    stick = _stick(tmp_path)
    tracks = _tracks()
    tracks[1].device_path = ""
    entries, version = entries_by_path(_collection(tmp_path))
    root = _parse(
        render(
            tracks,
            [0, 1],
            "X",
            entries=entries,
            mount=stick,
            volume="DJSTICK",
            dest=stick,
            version=version,
        )
    )
    assert len(list(root.iter("PRIMARYKEY"))) == 1


def test_a_track_missing_from_the_collection_still_gets_an_entry(tmp_path) -> None:
    stick = _stick(tmp_path)
    tracks = [
        T(
            source=Path("/elsewhere/new.mp3"),
            device_path="/Contents/new.mp3",
            title="New",
            artist="Someone",
        )
    ]
    root = _parse(
        render(
            tracks, [0], "X", entries={}, mount=stick, volume="DJSTICK", dest=stick, version="20"
        )
    )
    entry = root.find("COLLECTION/ENTRY")
    assert entry.get("TITLE") == "New" and entry.get("ARTIST") == "Someone"
    assert entry.find("LOCATION").get("FILE") == "new.mp3"


# ── Writing them ────────────────────────────────────────────────────────────


def test_one_file_per_playlist_under_the_traktor_folder(tmp_path) -> None:
    stick = _stick(tmp_path)
    written = write_playlists(
        stick,
        _tracks(),
        {"Warmup": [0, 1], "Peak": [2]},
        _collection(tmp_path),
        is_mount=_mounted_at(stick),
    )
    assert sorted(p.name for p in written) == ["Peak.nml", "Warmup.nml"]
    assert all(p.parent == stick / NML_DIRNAME for p in written)
    _parse(written[0].read_bytes())  # well-formed


def test_an_empty_playlist_writes_no_file(tmp_path) -> None:
    stick = _stick(tmp_path)
    written = write_playlists(
        stick, _tracks(), {"Empty": []}, _collection(tmp_path), is_mount=_mounted_at(stick)
    )
    assert written == []


def test_same_named_playlists_do_not_overwrite_each_other(tmp_path) -> None:
    stick = _stick(tmp_path)
    written = write_playlists(
        stick,
        _tracks(),
        {"A/Set": [0], "B/Set": [1]},
        _collection(tmp_path),
        is_mount=_mounted_at(stick),
    )
    assert len({p.name for p in written}) == 2


def test_no_collection_still_writes_playlists(tmp_path) -> None:
    stick = _stick(tmp_path)
    written = write_playlists(stick, _tracks(), {"Warmup": [0]}, None, is_mount=_mounted_at(stick))
    assert len(written) == 1


def test_a_playlist_in_folders_lands_in_the_same_folders(tmp_path) -> None:
    """The file moves; LOCATION is from the stick's root, so it stays right."""
    stick = _stick(tmp_path)
    written = write_playlists(
        stick,
        _tracks(),
        {("Gigs", "Summer", "Set"): [0], ("Warmup",): [1]},
        _collection(tmp_path),
        is_mount=_mounted_at(stick),
    )
    assert sorted(p.relative_to(stick / NML_DIRNAME).as_posix() for p in written) == [
        "Gigs/Summer/Set.nml",
        "Warmup.nml",
    ]
    nested = next(p for p in written if p.name == "Set.nml")
    root = _parse(nested.read_bytes())
    assert root.find(".//NODE[@TYPE='PLAYLIST']").get("NAME") == "Set"
    loc = root.find("COLLECTION/ENTRY/LOCATION")
    if sys.platform == "win32":
        # A volume is a drive there, and this stick is a folder on one: the
        # root LOCATION counts from is the drive's.
        folders = (stick / "Contents").relative_to(stick.anchor).parts
        assert loc.get("VOLUME") == stick.drive
        assert loc.get("DIR") == "/:" + "".join(f"{p}/:" for p in folders)
    else:
        assert loc.get("DIR") == "/:Contents/:"
        assert loc.get("VOLUME") == "DJSTICK"
    # Whatever the root, the folder the playlist is in is no part of it.
    flat = next(p for p in written if p.name == "Warmup.nml")
    assert (_parse(flat.read_bytes()).find("COLLECTION/ENTRY/LOCATION").get("DIR")) == loc.get(
        "DIR"
    )


# ── Whose playlist it is ────────────────────────────────────────────────────
# As for the .m3u8 files: only a playlist that says it was written here is
# removed as a leftover.


def test_a_playlist_says_who_wrote_it_and_still_parses(tmp_path) -> None:
    stick = _stick(tmp_path)
    [written] = write_playlists(
        stick, _tracks(), {"Warmup": [0]}, _collection(tmp_path), is_mount=_mounted_at(stick)
    )
    body = written.read_bytes()

    assert body.startswith(b'<?xml version="1.0"'), "the declaration is first"
    assert is_ours(written)
    root = _parse(body)
    assert root.tag == "NML"
    assert root.find(".//NODE[@TYPE='PLAYLIST']").get("NAME") == "Warmup"


def test_our_own_flat_copy_is_removed(tmp_path) -> None:
    stick = _stick(tmp_path)
    [flat] = write_playlists(
        stick, _tracks(), {"Set": [0]}, _collection(tmp_path), is_mount=_mounted_at(stick)
    )

    write_playlists(
        stick, _tracks(), {("Gigs", "Set"): [0]}, _collection(tmp_path), is_mount=_mounted_at(stick)
    )

    assert not flat.exists()
    assert (stick / NML_DIRNAME / "Gigs" / "Set.nml").is_file()


def test_a_playlist_we_did_not_write_is_left_alone(tmp_path) -> None:
    """One exported from Traktor itself and copied to the stick by hand."""
    stick = _stick(tmp_path)
    (stick / NML_DIRNAME).mkdir()
    theirs = stick / NML_DIRNAME / "Set.nml"
    theirs.write_text(COLLECTION, encoding="utf-8")

    write_playlists(
        stick, _tracks(), {("Gigs", "Set"): [0]}, _collection(tmp_path), is_mount=_mounted_at(stick)
    )

    assert theirs.read_text(encoding="utf-8") == COLLECTION


def test_same_name_in_two_folders_keeps_its_own_name(tmp_path) -> None:
    stick = _stick(tmp_path)
    written = write_playlists(
        stick,
        _tracks(),
        {("A", "Set"): [0], ("B", "Set"): [1]},
        _collection(tmp_path),
        is_mount=_mounted_at(stick),
    )
    assert sorted(p.relative_to(stick / NML_DIRNAME).as_posix() for p in written) == [
        "A/Set.nml",
        "B/Set.nml",
    ]


def test_an_unchanged_playlist_is_not_rewritten(tmp_path) -> None:
    """Its UUID is random per render, so only that differs; the file on the
    stick keeps its own and is not touched."""
    stick = _stick(tmp_path)
    args = (stick, _tracks(), {"Warmup": [0, 1]}, _collection(tmp_path))
    [first] = write_playlists(*args, is_mount=_mounted_at(stick))
    os.utime(first, ns=(1_000_000_000, 1_000_000_000))
    before = first.read_bytes()
    [again] = write_playlists(*args, is_mount=_mounted_at(stick))
    assert again == first
    assert first.stat().st_mtime_ns == 1_000_000_000
    assert first.read_bytes() == before


def test_a_changed_playlist_is_rewritten(tmp_path) -> None:
    stick = _stick(tmp_path)
    [first] = write_playlists(
        stick, _tracks(), {"Warmup": [0, 1]}, _collection(tmp_path), is_mount=_mounted_at(stick)
    )
    write_playlists(
        stick, _tracks(), {"Warmup": [1]}, _collection(tmp_path), is_mount=_mounted_at(stick)
    )
    keys = list(_parse(first.read_bytes()).iter("PRIMARYKEY"))
    assert len(keys) == 1
