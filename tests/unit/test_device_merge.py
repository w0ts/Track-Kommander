"""Adding tracks to a key that already has some.

Building a key up over several sessions is the normal way to use one, but the
export used to rebuild `export.pdb` from the blank seed every time. The audio
already there was not recopied — `plan_export` skips identical files — so the
files stayed, but they dropped out of the database and became orphans the
player could no longer see.

Merging **refreshes** rather than preserves. Anything named in this export
wins: a track already on the key gets its analysis rewritten and its row
replaced, because the reason to export it again is that something about it
changed — re-detected hotcues, a corrected grid. A playlist of the same name
is replaced outright, not appended to, so removing a track from it in Traktor
removes it from the key. Only what this export does *not* mention is carried
over untouched.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from core import anlz, pdb_seed
from core.device_export import (
    DB_DIR,
    DeviceTrack,
    already_on_device,
    export,
    read_manifest,
)

#: Bound here rather than imported at the top: rekordbox-pdb is optional,
#: so the whole module is skipped when it is absent.
Database = pytest.importorskip("rekordbox_pdb", reason="rekordbox-pdb not installed").Database

SEED_CANDIDATES = (
    Path(__file__).parent / "data" / "export-seed.pdb",
    Path.home() / "Library/Application Support/Track Kommander/export-seed.pdb",
)


def _seed() -> Path:
    """A blank export.pdb. Found on a stick if there is one, generated if not.

    These used to skip wholesale on any machine without a Rekordbox-prepared
    key — which is every CI machine, so the export went untested there.
    """
    for p in SEED_CANDIDATES:
        if p.is_file():
            return p
    out = Path(tempfile.mkdtemp(prefix="pdb-seed-")) / "export.pdb"
    return pdb_seed.write(out)


@pytest.fixture
def audio(tmp_path):
    soundfile = pytest.importorskip("soundfile")
    np = pytest.importorskip("numpy")
    if "MP3" not in soundfile.available_formats():
        pytest.skip("libsndfile has no MP3 support")
    src = tmp_path / "src"
    src.mkdir()
    out = []
    for i in range(4):
        p = src / f"track{i}.mp3"
        soundfile.write(p, np.zeros(44100, dtype="float32"), 44100, format="MP3")
        out.append(p)
    return out


def _track(p: Path, title: str) -> DeviceTrack:
    return DeviceTrack(
        source=p, title=title, artist="A", bpm=128.0, duration_s=60, bitrate_kbps=320
    )


def _titles(pdb: Path) -> set[str]:
    return {t.title for t in Database.from_file(str(pdb)).tracks}


def _playlists(pdb: Path) -> dict[str, int]:
    db = Database.from_file(str(pdb))
    counts: dict[str, int] = {}
    by_id = {p.id: p.name for p in db.playlist_tree}
    for e in db.playlist_entries:
        name = by_id.get(e.playlist_id)
        if name:
            counts[name] = counts.get(name, 0) + 1
    return counts


# ── The old behaviour is still the default ──────────────────────────────────


def test_without_merge_the_library_is_replaced(tmp_path, audio) -> None:
    dest = tmp_path / "usb"
    export([_track(audio[0], "First")], dest, _seed())
    report = export([_track(audio[1], "Second")], dest, _seed())
    assert _titles(report.db_path) == {"Second"}


# ── Merging ─────────────────────────────────────────────────────────────────


def test_merge_keeps_what_was_already_there(tmp_path, audio) -> None:
    dest = tmp_path / "usb"
    export([_track(audio[0], "First")], dest, _seed())
    report = export([_track(audio[1], "Second")], dest, _seed(), merge=True)
    assert _titles(report.db_path) == {"First", "Second"}


def test_two_files_of_one_size_and_time_are_two_tracks(tmp_path, audio) -> None:
    """Size and modification time stand in for a file the key cannot name,
    and two files can share both: copied with their dates, or written in one
    tick of the clock, which on Windows is 1/64 s long. The second was then
    taken for the first — filed at its path, and the first dropped from the
    database."""
    same = audio[0].stat()
    os.utime(audio[1], ns=(same.st_atime_ns, same.st_mtime_ns))
    assert audio[1].stat().st_size == same.st_size
    dest = tmp_path / "usb"

    export([_track(audio[0], "First")], dest, _seed())
    report = export([_track(audio[1], "Second")], dest, _seed(), merge=True)

    assert _titles(report.db_path) == {"First", "Second"}
    assert len(read_manifest(dest)) == 2


def test_a_file_that_moved_is_still_the_track_on_the_key(tmp_path, audio) -> None:
    """What the stand-in is for: the library moved, so the key's record of
    where the file came from names a path that is no longer there."""
    dest = tmp_path / "usb"
    export([_track(audio[0], "First")], dest, _seed())
    moved = audio[0].with_name("somewhere else.mp3")
    audio[0].rename(moved)

    report = export([_track(moved, "First")], dest, _seed(), merge=True)

    assert _titles(report.db_path) == {"First"}
    assert list(read_manifest(dest)) == ["/Contents/A/Unknown Album/track0.mp3"]


def test_merge_onto_an_empty_key_is_a_plain_export(tmp_path, audio) -> None:
    dest = tmp_path / "usb"
    report = export([_track(audio[0], "Only")], dest, _seed(), merge=True)
    assert _titles(report.db_path) == {"Only"}
    assert report.replaced == 0


def test_merge_replaces_a_track_that_is_already_there(tmp_path, audio) -> None:
    """Re-exporting a track refreshes it. It is not added twice, and it is not
    left alone either — the point of exporting it again is that it changed."""
    dest = tmp_path / "usb"
    export([_track(audio[0], "First")], dest, _seed())
    report = export(
        [_track(audio[0], "First, redone"), _track(audio[1], "Second")], dest, _seed(), merge=True
    )
    assert _titles(report.db_path) == {"First, redone", "Second"}
    assert report.replaced == 1


def test_merge_rewrites_the_analysis_of_a_replaced_track(tmp_path, audio) -> None:
    """Hotcues redone in Traktor must reach the key."""
    dest = tmp_path / "usb"
    export([_track(audio[0], "First")], dest, _seed())

    again = _track(audio[0], "First")
    again.cues = [anlz.Cue(time_ms=4321, hot_cue=5)]
    report = export([again], dest, _seed(), merge=True)

    track = next(t for t in Database.from_file(str(report.db_path)).tracks if t.title == "First")
    ext = (dest / track.analyze_path.lstrip("/")).with_suffix(".EXT")
    assert b"\x10\xe1" in ext.read_bytes(), "4321 ms should be in a cue"


def test_merge_reuses_the_slot_of_a_replaced_track(tmp_path, audio) -> None:
    """Rewritten in place, so no orphan analysis folder is left behind."""
    dest = tmp_path / "usb"
    export([_track(audio[0], "First")], dest, _seed())
    before = next(iter(Database.from_file(str(dest / DB_DIR / "export.pdb")).tracks)).analyze_path

    report = export([_track(audio[0], "First")], dest, _seed(), merge=True)
    after = next(iter(Database.from_file(str(report.db_path)).tracks))
    assert after.analyze_path == before


def test_merge_keeps_tracks_this_export_does_not_mention(tmp_path, audio) -> None:
    dest = tmp_path / "usb"
    export([_track(audio[0], "First"), _track(audio[1], "Second")], dest, _seed())
    report = export([_track(audio[2], "Third")], dest, _seed(), merge=True)
    assert _titles(report.db_path) == {"First", "Second", "Third"}


def test_merge_preserves_carried_over_metadata(tmp_path, audio) -> None:
    """A carried-over track keeps what it had, not blanks."""
    dest = tmp_path / "usb"
    keep = DeviceTrack(
        source=audio[0],
        title="Keeper",
        artist="Someone",
        album="An Album",
        genre="Techno",
        key="8A",
        bpm=123.0,
        duration_s=222,
        bitrate_kbps=320,
    )
    export([keep], dest, _seed())
    report = export([_track(audio[1], "New one")], dest, _seed(), merge=True)

    got = next(t for t in Database.from_file(str(report.db_path)).tracks if t.title == "Keeper")
    assert got.tempo == 12300
    assert got.duration == 222
    assert got.analyze_path.endswith("ANLZ0000.DAT")
    assert (dest / got.analyze_path.lstrip("/")).is_file()


def test_merge_analysis_files_all_exist(tmp_path, audio) -> None:
    dest = tmp_path / "usb"
    export([_track(audio[0], "First")], dest, _seed())
    report = export([_track(audio[1], "Second")], dest, _seed(), merge=True)
    for t in Database.from_file(str(report.db_path)).tracks:
        dat = dest / t.analyze_path.lstrip("/")
        assert dat.is_file(), f"{t.title}: {t.analyze_path} missing"
        assert dat.with_suffix(".EXT").is_file()


def test_merge_gives_every_track_its_own_analysis_folder(tmp_path, audio) -> None:
    dest = tmp_path / "usb"
    export([_track(audio[0], "First")], dest, _seed())
    export([_track(audio[1], "Second")], dest, _seed(), merge=True)
    report = export([_track(audio[2], "Third")], dest, _seed(), merge=True)
    paths = [t.analyze_path for t in Database.from_file(str(report.db_path)).tracks]
    assert len(set(paths)) == 3, paths


def test_merge_keeps_playlists_this_export_does_not_mention(tmp_path, audio) -> None:
    dest = tmp_path / "usb"
    export([_track(audio[0], "First")], dest, _seed(), playlists={"Monday": [0]})
    report = export(
        [_track(audio[1], "Second")], dest, _seed(), playlists={"Tuesday": [0]}, merge=True
    )
    assert _playlists(report.db_path) == {"Monday": 1, "Tuesday": 1}


def test_merge_replaces_a_playlist_of_the_same_name(tmp_path, audio) -> None:
    """Replaced, not appended: dropping a track from the playlist in Traktor
    has to drop it from the key too."""
    dest = tmp_path / "usb"
    export(
        [_track(audio[0], "First"), _track(audio[1], "Second")],
        dest,
        _seed(),
        playlists={"Set": [0, 1]},
    )
    report = export([_track(audio[1], "Second")], dest, _seed(), playlists={"Set": [0]}, merge=True)
    assert _playlists(report.db_path) == {"Set": 1}
    assert _titles(report.db_path) == {"First", "Second"}, (
        "the dropped track stays on the key, just not in that playlist"
    )


def test_merge_survives_an_unreadable_existing_database(tmp_path, audio) -> None:
    """A corrupt export.pdb must not lose the tracks being added."""
    dest = tmp_path / "usb"
    export([_track(audio[0], "First")], dest, _seed())
    (dest / DB_DIR / "export.pdb").write_bytes(b"not a database")

    report = export([_track(audio[1], "Second")], dest, _seed(), merge=True)
    assert report.db_path is not None
    assert "Second" in _titles(report.db_path)


# ── Keeping what is already there, for speed ────────────────────────────────
# Rewriting a track means decoding its audio again for the waveforms, which is
# the slow part of an export. When nothing about a track has changed that is
# wasted work, so it can be turned off.


def test_keep_does_not_rewrite_an_existing_track(tmp_path, audio) -> None:
    dest = tmp_path / "usb"
    export([_track(audio[0], "First")], dest, _seed())
    before = (dest / DB_DIR / "export.pdb").parent
    first = next(iter(Database.from_file(str(before / "export.pdb")).tracks))
    ext = (dest / first.analyze_path.lstrip("/")).with_suffix(".EXT")
    stamp = ext.read_bytes()

    again = _track(audio[0], "First")
    again.cues = [anlz.Cue(time_ms=4321, hot_cue=5)]
    report = export([again], dest, _seed(), merge=True, refresh_existing=False)

    assert ext.read_bytes() == stamp, "left exactly as it was"
    assert report.kept == 1
    assert report.replaced == 0


def test_keep_still_adds_the_new_tracks(tmp_path, audio) -> None:
    dest = tmp_path / "usb"
    export([_track(audio[0], "First")], dest, _seed())
    report = export(
        [_track(audio[0], "First"), _track(audio[1], "Second")],
        dest,
        _seed(),
        merge=True,
        refresh_existing=False,
    )
    assert _titles(report.db_path) == {"First", "Second"}
    assert report.kept == 1


def test_keep_leaves_a_kept_track_usable_in_a_playlist(tmp_path, audio) -> None:
    """It is not being rewritten, but it still has to land in the playlist."""
    dest = tmp_path / "usb"
    export([_track(audio[0], "First")], dest, _seed())
    report = export(
        [_track(audio[0], "First"), _track(audio[1], "Second")],
        dest,
        _seed(),
        playlists={"Set": [0, 1]},
        merge=True,
        refresh_existing=False,
    )
    assert _playlists(report.db_path) == {"Set": 2}


def test_keep_is_not_the_default(tmp_path, audio) -> None:
    """A track that *did* change is rewritten unless keeping is asked for.

    This used to re-export the identical track and expect it to be replaced.
    Change detection now recognises that as unchanged and does nothing, which
    is the point of it — so the track has to actually differ for the default
    to have anything to do.
    """
    dest = tmp_path / "usb"
    export([_track(audio[0], "First")], dest, _seed())
    changed = _track(audio[0], "First, retitled")

    report = export([changed], dest, _seed(), merge=True)
    assert report.replaced == 1
    assert report.kept == 0


def test_an_identical_re_export_does_nothing(tmp_path, audio) -> None:
    dest = tmp_path / "usb"
    export([_track(audio[0], "First")], dest, _seed())
    report = export([_track(audio[0], "First")], dest, _seed(), merge=True)
    assert report.unchanged == 1
    assert report.replaced == 0


# ── Knowing what to skip before the slow part ───────────────────────────────


def test_already_on_device_finds_what_the_key_holds(tmp_path, audio) -> None:
    """The caller computes waveforms before calling export, so it has to be
    able to ask what is already there — otherwise "keep" still pays the
    decoding cost it exists to avoid."""
    dest = tmp_path / "usb"
    export([_track(audio[0], "First")], dest, _seed())
    got = already_on_device(dest, [audio[0], audio[1]])
    assert got == {audio[0]}


def test_already_on_device_is_empty_for_a_fresh_key(tmp_path, audio) -> None:
    assert already_on_device(tmp_path / "nothing", [audio[0]]) == set()


def test_already_on_device_ignores_an_unreadable_database(tmp_path, audio) -> None:
    dest = tmp_path / "usb"
    export([_track(audio[0], "First")], dest, _seed())
    (dest / DB_DIR / "export.pdb").write_bytes(b"not a database")
    assert already_on_device(dest, [audio[0]]) == set()


# ── Retagging does not move audio ───────────────────────────────────────────


def _rows(pdb: Path) -> dict[str, str]:
    return {t.title: t.file_path for t in Database.from_file(str(pdb)).tracks}


def test_a_retagged_track_keeps_its_place_on_the_key(tmp_path, audio) -> None:
    """Tracks are filed by artist and album, so changing either in Traktor
    would move the file — a recopy plus an orphan, for nothing. A track the
    key already holds stays where it is; only its row changes."""
    dest = tmp_path / "usb"
    export([_track(audio[0], "First")], dest, _seed())
    first_path = _rows(dest / DB_DIR / "export.pdb")["First"]
    assert first_path.startswith("/Contents/A/")

    retagged = _track(audio[0], "First")
    retagged.artist = "B"
    report = export([retagged], dest, _seed(), merge=True)

    assert _rows(report.db_path)["First"] == first_path
    assert len(list((dest / "Contents").rglob("*.mp3"))) == 1
    assert not (dest / "Contents" / "B").exists()


def test_a_retagged_track_keeps_its_place_on_replace_too(tmp_path, audio) -> None:
    dest = tmp_path / "usb"
    export([_track(audio[0], "First")], dest, _seed())
    first_path = _rows(dest / DB_DIR / "export.pdb")["First"]
    retagged = _track(audio[0], "First")
    retagged.artist = "B"
    report = export([retagged], dest, _seed())
    assert _rows(report.db_path)["First"] == first_path
    assert report.copied == 0
