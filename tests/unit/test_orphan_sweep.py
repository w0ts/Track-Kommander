"""Files the key's database no longer lists.

A rebuild writes a fresh library from the blank seed and does not recopy
audio already on the drive, so the files stay while their rows go. They are
invisible to a player and they are the biggest thing on the stick.

This deletes audio, so the tests that matter most are the ones about *not*
deleting: an unreadable database, a path outside the export, a file the
database still points at.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import core.device_export as de
from core.device_export import (
    ANLZ_DIR,
    DB_DIR,
    Orphans,
    audio_not_in,
    find_orphans,
    remove_orphans,
)
from core.usb_export import AUDIO_DIRNAME


@pytest.fixture
def stick(tmp_path) -> Path:
    """A key with two tracks' worth of files on it."""
    (tmp_path / AUDIO_DIRNAME).mkdir()
    (tmp_path / DB_DIR).mkdir(parents=True)
    for name in ("kept.mp3", "gone.mp3"):
        (tmp_path / AUDIO_DIRNAME / name).write_bytes(b"x" * 100)
    for slot in ("00000001", "00000002"):
        d = tmp_path / ANLZ_DIR / "P000" / slot
        d.mkdir(parents=True)
        (d / "ANLZ0000.DAT").write_bytes(b"y" * 50)
    (tmp_path / DB_DIR / "export.pdb").write_bytes(b"not a real pdb")
    return tmp_path


class _Carried:
    def __init__(self, analyze_path: str) -> None:
        self.analyze_path = analyze_path
        self.file_path = ""
        self.fields: dict = {}


def _existing(monkeypatch, tracks: dict) -> None:
    """Stand in for reading the key's export.pdb."""

    class _E:
        def __init__(self):
            self.tracks = tracks
            self.playlists = {}
            self.used_slots = set()

    monkeypatch.setattr(de, "_read_existing", lambda pdb: _E())


ONE_TRACK = {
    "/Contents/kept.mp3": _Carried(f"/{ANLZ_DIR}/P000/00000001/ANLZ0000.DAT"),
}


# ── Finding ─────────────────────────────────────────────────────────────────


def test_finds_audio_no_row_points_at(stick, monkeypatch) -> None:
    _existing(monkeypatch, ONE_TRACK)
    found = find_orphans(stick)
    assert [p.name for p in found.audio] == ["gone.mp3"]


def test_finds_analysis_folders_no_row_points_at(stick, monkeypatch) -> None:
    _existing(monkeypatch, ONE_TRACK)
    found = find_orphans(stick)
    assert [p.name for p in found.analysis] == ["00000002"]


def test_counts_what_it_would_free(stick, monkeypatch) -> None:
    _existing(monkeypatch, ONE_TRACK)
    found = find_orphans(stick)
    assert found.bytes == 150  # one 100-byte mp3 + one 50-byte DAT
    assert found.count == 2
    assert bool(found) is True


def test_a_fully_referenced_key_has_no_orphans(stick, monkeypatch) -> None:
    _existing(
        monkeypatch,
        {
            "/Contents/kept.mp3": _Carried(f"/{ANLZ_DIR}/P000/00000001/ANLZ0000.DAT"),
            "/Contents/gone.mp3": _Carried(f"/{ANLZ_DIR}/P000/00000002/ANLZ0000.DAT"),
        },
    )
    found = find_orphans(stick)
    assert not found
    assert found.count == 0


def test_a_name_the_drive_spells_differently_is_not_an_orphan(stick, monkeypatch) -> None:
    """exFAT sticks come back decomposed; the database holds the composed form.

    "Maōh" is one code point in the row and two on the drive. Comparing the
    two spellings byte for byte makes a track the key still lists look
    unreferenced — and this deletes audio.
    """
    composed = "Ma\u014dh.mp3"
    decomposed = "Mao\u0304h.mp3"
    (stick / AUDIO_DIRNAME / decomposed).write_bytes(b"z" * 100)
    _existing(
        monkeypatch,
        {
            **ONE_TRACK,
            f"/Contents/{composed}": _Carried(f"/{ANLZ_DIR}/P000/00000002/ANLZ0000.DAT"),
        },
    )
    found = find_orphans(stick)
    assert [p.name for p in found.audio] == ["gone.mp3"]
    assert found.analysis == []


def test_dotfiles_are_left_alone(stick, monkeypatch) -> None:
    """macOS sidecars are swept elsewhere and are not ours to judge here."""
    (stick / AUDIO_DIRNAME / "._kept.mp3").write_bytes(b"z")
    _existing(monkeypatch, ONE_TRACK)
    assert all(not p.name.startswith(".") for p in find_orphans(stick).audio)


# ── Refusing ────────────────────────────────────────────────────────────────


def test_an_unreadable_database_finds_nothing(stick, monkeypatch) -> None:
    """The one mistake that must never happen: a misread makes every file
    look unreferenced. Better to sweep nothing than to sweep the stick."""
    _existing(monkeypatch, {})
    assert not find_orphans(stick)


def test_no_database_at_all_finds_nothing(tmp_path) -> None:
    (tmp_path / AUDIO_DIRNAME).mkdir()
    (tmp_path / AUDIO_DIRNAME / "loose.mp3").write_bytes(b"x")
    assert not find_orphans(tmp_path)


def test_an_empty_key_is_not_an_error(tmp_path) -> None:
    assert find_orphans(tmp_path).count == 0


# ── Removing ────────────────────────────────────────────────────────────────


def test_removes_only_what_was_found(stick, monkeypatch) -> None:
    _existing(monkeypatch, ONE_TRACK)
    found = find_orphans(stick)
    removed, errors = remove_orphans(stick, found)

    assert (removed, errors) == (2, [])
    assert (stick / AUDIO_DIRNAME / "kept.mp3").is_file()
    assert not (stick / AUDIO_DIRNAME / "gone.mp3").exists()
    assert (stick / ANLZ_DIR / "P000" / "00000001").is_dir()
    assert not (stick / ANLZ_DIR / "P000" / "00000002").exists()


def test_it_refuses_a_path_outside_the_export(stick, tmp_path) -> None:
    """The last thing between a bad caller and someone's music."""
    outside = tmp_path.parent / "precious.mp3"
    outside.write_bytes(b"do not touch")
    removed, errors = remove_orphans(stick, Orphans(audio=[outside]))

    assert removed == 0
    assert outside.is_file(), "a file outside the stick must survive"
    assert errors and "refused" in errors[0]


def test_it_refuses_the_database_itself(stick) -> None:
    pdb = stick / DB_DIR / "export.pdb"
    removed, errors = remove_orphans(stick, Orphans(audio=[pdb]))
    assert removed == 0
    assert pdb.is_file()
    assert errors


def test_a_file_that_vanished_is_reported_not_raised(stick, monkeypatch) -> None:
    _existing(monkeypatch, ONE_TRACK)
    found = find_orphans(stick)
    (stick / AUDIO_DIRNAME / "gone.mp3").unlink()  # someone got there first

    removed, errors = remove_orphans(stick, found)
    assert removed == 1  # the analysis folder
    assert len(errors) == 1


def test_removing_nothing_is_fine(stick) -> None:
    assert remove_orphans(stick, Orphans()) == (0, [])


# ── What Replace will delete, before it deletes it ──────────────────────────


def test_it_names_what_a_replace_would_delete(stick, tmp_path) -> None:
    """Replace can only be confirmed honestly if the number comes first."""
    src = tmp_path / "src"
    src.mkdir()
    keeper = src / "kept.mp3"
    keeper.write_bytes(b"x" * 100)
    count, size = audio_not_in(stick, [keeper])
    assert count == 1  # gone.mp3 is not in this export
    assert size == 100


def test_an_export_of_everything_deletes_nothing(stick, tmp_path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    both = []
    for name in ("kept.mp3", "gone.mp3"):
        f = src / name
        f.write_bytes(b"x" * 100)
        both.append(f)
    assert audio_not_in(stick, both) == (0, 0)


def test_a_key_with_no_audio_yet_deletes_nothing(tmp_path) -> None:
    assert audio_not_in(tmp_path, []) == (0, 0)


def test_dotfiles_do_not_count_towards_the_warning(stick, tmp_path) -> None:
    """macOS sidecars are swept anyway; counting them would inflate the
    number the user is asked to agree to."""
    (stick / AUDIO_DIRNAME / "._gone.mp3").write_bytes(b"s" * 40)
    src = tmp_path / "src"
    src.mkdir()
    both = []
    for name in ("kept.mp3", "gone.mp3"):
        f = src / name
        f.write_bytes(b"x" * 100)
        both.append(f)
    assert audio_not_in(stick, both) == (0, 0)


def test_a_name_the_drive_spells_differently_is_not_counted(stick, tmp_path) -> None:
    """Same decomposed-filename trap as the sweep itself — here it would
    scare the user with a track that is not going anywhere."""
    (stick / AUDIO_DIRNAME / "Mao\u0304h.mp3").write_bytes(b"z" * 100)
    src = tmp_path / "src"
    src.mkdir()
    sources = []
    for name in ("kept.mp3", "gone.mp3", "Ma\u014dh.mp3"):
        f = src / name
        f.write_bytes(b"x" * 100)
        sources.append(f)
    assert audio_not_in(stick, sources) == (0, 0)


# ── Folder layout ───────────────────────────────────────────────────────────


def test_orphans_are_found_inside_artist_folders(stick, monkeypatch) -> None:
    nested = stick / AUDIO_DIRNAME / "Artist" / "Album"
    nested.mkdir(parents=True)
    (nested / "deep.mp3").write_bytes(b"x" * 100)
    _existing(
        monkeypatch,
        {
            "/Contents/kept.mp3": _Carried(f"/{ANLZ_DIR}/P000/00000001/ANLZ0000.DAT"),
            "/Contents/gone.mp3": _Carried(f"/{ANLZ_DIR}/P000/00000002/ANLZ0000.DAT"),
        },
    )
    found = de.find_orphans(stick)
    assert [p.name for p in found.audio] == ["deep.mp3"]


def test_removing_the_last_track_of_an_album_removes_its_folder(stick, monkeypatch) -> None:
    nested = stick / AUDIO_DIRNAME / "Artist" / "Album"
    nested.mkdir(parents=True)
    (nested / "deep.mp3").write_bytes(b"x" * 100)
    _existing(
        monkeypatch,
        {
            "/Contents/kept.mp3": _Carried(f"/{ANLZ_DIR}/P000/00000001/ANLZ0000.DAT"),
            "/Contents/gone.mp3": _Carried(f"/{ANLZ_DIR}/P000/00000002/ANLZ0000.DAT"),
        },
    )
    de.remove_orphans(stick, de.find_orphans(stick))
    assert not (stick / AUDIO_DIRNAME / "Artist").exists()
    assert (stick / AUDIO_DIRNAME).is_dir()


def test_a_replace_counts_audio_inside_folders_too(stick, tmp_path) -> None:
    nested = stick / AUDIO_DIRNAME / "Artist" / "Album"
    nested.mkdir(parents=True)
    (nested / "deep.mp3").write_bytes(b"x" * 100)
    assert audio_not_in(stick, []) == (3, 300)


# ── Case is not a difference on a FAT/exFAT key ─────────────────────────────
# Artist tags spelled Benza and BENZA land in one folder — whichever spelling
# came first — while the row keeps the other. The sweep deletes audio, so it
# must not mistake that for an orphan.


def test_a_row_that_differs_only_in_case_is_not_an_orphan(stick, monkeypatch) -> None:
    nested = stick / AUDIO_DIRNAME / "Benza" / "Album"
    nested.mkdir(parents=True)
    (nested / "track.mp3").write_bytes(b"x" * 100)
    _existing(
        monkeypatch,
        {
            "/Contents/kept.mp3": _Carried(f"/{ANLZ_DIR}/P000/00000001/ANLZ0000.DAT"),
            "/Contents/gone.mp3": _Carried(f"/{ANLZ_DIR}/P000/00000002/ANLZ0000.DAT"),
            "/Contents/BENZA/Album/track.mp3": _Carried(f"/{ANLZ_DIR}/P000/00000003/ANLZ0000.DAT"),
        },
    )
    assert de.find_orphans(stick).audio == []


def test_a_replace_does_not_count_a_case_difference(stick, tmp_path) -> None:
    from core.usb_export import Source

    nested = stick / AUDIO_DIRNAME / "BENZA" / "Album"
    nested.mkdir(parents=True)
    (nested / "track.mp3").write_bytes(b"x" * 100)
    src = tmp_path / "src"
    src.mkdir()
    both = []
    for name in ("kept.mp3", "gone.mp3"):
        f = src / name
        f.write_bytes(b"x" * 100)
        both.append(f)
    t = src / "track.mp3"
    t.write_bytes(b"x" * 100)
    assert audio_not_in(stick, [*both, Source(str(t), artist="Benza", album="Album")]) == (0, 0)
