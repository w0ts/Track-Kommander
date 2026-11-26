"""What an export must never do to a key, however it ends.

Each test pins one way an export could leave a stick worse than it found it:

* the database was written over in place, so an interrupted write left the
  player with half a file and no earlier one to fall back on;
* a database that could not be read was taken for an empty key, so a merge
  quietly became a replace and said "complete";
* stopping half-way recorded every track as done, including the ones whose
  analysis was never written, so the next export skipped them for good;
* a grid anchor or a cue before the start of the file aborted the whole
  export, after the audio was already copied.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from core import anlz, device_export, pdb_seed
from core.device_export import (
    DB_DIR,
    DeviceTrack,
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


def _track(p: Path, title: str, **more) -> DeviceTrack:
    return DeviceTrack(
        source=p, title=title, artist="A", bpm=128.0, duration_s=60, bitrate_kbps=320, **more
    )


def _rows(pdb: Path) -> dict[str, str]:
    """Title -> analyze path, for every track the database lists."""
    return {t.title: t.analyze_path for t in Database.from_file(str(pdb)).tracks}


# ── The database is replaced, never written over ────────────────────────────


def test_a_failed_database_write_leaves_the_old_one(tmp_path, audio, monkeypatch) -> None:
    dest = tmp_path / "usb"
    export([_track(audio[0], "First")], dest, _seed())
    pdb = dest / DB_DIR / "export.pdb"
    before = pdb.read_bytes()

    def full(path: Path, data: bytes, **_kw) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(device_export, "write_bytes_atomic", full)
    report = export([_track(audio[1], "Second")], dest, _seed(), merge=True)

    assert pdb.read_bytes() == before
    assert any("export.pdb" in e and "No space left" in e for e in report.errors)
    assert not report.ok


def test_no_partial_database_stays_on_the_key(tmp_path, audio) -> None:
    dest = tmp_path / "usb"

    export([_track(audio[0], "First")], dest, _seed())

    assert not list((dest / DB_DIR).glob("*.part"))


# ── An unreadable database is not an empty key ──────────────────────────────


def test_an_unreadable_database_is_reported_and_kept(tmp_path, audio) -> None:
    dest = tmp_path / "usb"
    export([_track(audio[0], "First")], dest, _seed())
    pdb = dest / DB_DIR / "export.pdb"
    pdb.write_bytes(b"not a database")

    report = export([_track(audio[1], "Second")], dest, _seed(), merge=True)

    # The export still goes through: the tracks being added are not lost.
    assert report.db_path is not None
    assert "Second" in _rows(report.db_path)
    # But it says what happened, and what was there can still be looked at.
    assert any("could not be read" in e for e in report.errors)
    assert not report.ok
    kept = pdb.with_name("export.pdb.unreadable")
    assert kept.read_bytes() == b"not a database"


def test_a_readable_database_leaves_no_copy_behind(tmp_path, audio) -> None:
    dest = tmp_path / "usb"
    export([_track(audio[0], "First")], dest, _seed())

    report = export([_track(audio[1], "Second")], dest, _seed(), merge=True)

    assert report.ok, report.errors
    assert not (dest / DB_DIR / "export.pdb.unreadable").exists()


# ── Stopping half-way ───────────────────────────────────────────────────────


class _StopAfterOneAnalysis:
    """Lets the audio copy finish, then stops once one track is analysed."""

    def __init__(self) -> None:
        self.analysed = 0

    def heard(self, _done: int, _total: int, _name: str) -> None:
        self.analysed += 1

    def __call__(self) -> bool:
        return self.analysed >= 1


def test_a_stopped_export_points_only_at_analysis_that_is_there(tmp_path, audio) -> None:
    """The audio of both is on the key, so both are listed and playable. Only
    the one that was analysed says it has analysis."""
    dest = tmp_path / "usb"
    stop = _StopAfterOneAnalysis()

    report = export(
        [_track(audio[0], "First"), _track(audio[1], "Second")],
        dest,
        _seed(),
        should_stop=stop,
        analysis_progress=stop.heard,
    )

    rows = _rows(report.db_path)
    assert set(rows) == {"First", "Second"}
    assert (dest / rows["First"].lstrip("/")).is_file()
    assert rows["Second"] == ""


def test_a_stopped_export_does_not_record_what_it_did_not_finish(tmp_path, audio) -> None:
    dest = tmp_path / "usb"
    stop = _StopAfterOneAnalysis()

    export(
        [_track(audio[0], "First"), _track(audio[1], "Second")],
        dest,
        _seed(),
        should_stop=stop,
        analysis_progress=stop.heard,
    )

    assert len(read_manifest(dest)) == 1


def test_the_next_export_finishes_what_a_stop_left(tmp_path, audio) -> None:
    dest = tmp_path / "usb"
    stop = _StopAfterOneAnalysis()
    export(
        [_track(audio[0], "First"), _track(audio[1], "Second")],
        dest,
        _seed(),
        should_stop=stop,
        analysis_progress=stop.heard,
    )

    report = export(
        [_track(audio[0], "First"), _track(audio[1], "Second")], dest, _seed(), merge=True
    )

    assert report.ok, report.errors
    rows = _rows(report.db_path)
    assert set(rows) == {"First", "Second"}
    for path in rows.values():
        assert path and (dest / path.lstrip("/")).is_file()
    assert report.unchanged == 1, "the finished one is not redone"


def test_a_stop_keeps_the_row_a_track_already_had(tmp_path, audio) -> None:
    """A track being refreshed, not added: its old row and analysis are
    still good and must not drop off the key because the refresh was cut."""
    dest = tmp_path / "usb"
    export([_track(audio[0], "First"), _track(audio[1], "Second")], dest, _seed())
    stop = _StopAfterOneAnalysis()
    changed = [
        _track(audio[0], "First", cues=[anlz.Cue(time_ms=1000, hot_cue=1)]),
        _track(audio[1], "Second", cues=[anlz.Cue(time_ms=2000, hot_cue=1)]),
    ]

    report = export(
        changed, dest, _seed(), merge=True, should_stop=stop, analysis_progress=stop.heard
    )

    rows = _rows(report.db_path)
    assert set(rows) == {"First", "Second"}
    assert all(rows.values())


# ── One track that cannot be analysed ───────────────────────────────────────


def test_a_track_whose_analysis_fails_is_reported_and_tried_again(
    tmp_path, audio, monkeypatch
) -> None:
    dest = tmp_path / "usb"
    real = anlz.make_dat

    def make_dat(path: str, *args, **kw) -> bytes:
        if "track1" in path:
            raise ValueError("a tag that will not pack")
        return real(path, *args, **kw)

    monkeypatch.setattr(anlz, "make_dat", make_dat)
    report = export([_track(audio[0], "First"), _track(audio[1], "Second")], dest, _seed())

    rows = _rows(report.db_path)
    assert rows["First"] and rows["Second"] == ""
    assert any("track1" in e and "analysis" in e for e in report.errors)
    assert len(read_manifest(dest)) == 1, "not recorded, so it is tried again"


# ── Times before the start of the file ──────────────────────────────────────


def test_a_grid_anchored_before_zero_exports(tmp_path, audio) -> None:
    dest = tmp_path / "usb"

    report = export([_track(audio[0], "First", first_beat_ms=-20.0)], dest, _seed())

    assert report.ok, report.errors
    assert _rows(report.db_path)["First"]


def test_a_cue_before_zero_exports(tmp_path, audio) -> None:
    dest = tmp_path / "usb"
    cues = [anlz.Cue(time_ms=-5, hot_cue=1), anlz.Cue(time_ms=-5)]

    report = export([_track(audio[0], "First", cues=cues)], dest, _seed())

    assert report.ok, report.errors
