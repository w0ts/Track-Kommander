"""Tests for the cache that stops an export decoding the same track twice.

The point of the cache is that an export decodes a track once, ever. So the
tests that matter are the ones about *not* trusting it: a stale row served for
a file that changed would put the wrong waveform on a stick, which is worse
than the second it saves.

Each test gets its own database file — the module under test reaches for the
real one through :func:`core.db.get_conn`, so that is what is redirected.
"""

from __future__ import annotations

import os
import zlib

import pytest

from core import anlz, db, waveform_cache


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    """Point the database at a throwaway file and create the schema."""
    monkeypatch.setattr(db, "_db_path", lambda: tmp_path / "test.db")
    db.init_db()
    return tmp_path / "test.db"


@pytest.fixture
def track(tmp_path):
    """A file to key rows against. Its contents never matter here."""
    p = tmp_path / "track.mp3"
    p.write_bytes(b"\x00" * 2048)
    return p


def _waveform(columns: int = 64) -> anlz.Waveform:
    """A waveform with every value distinct enough to catch a mispacking."""
    return anlz.Waveform(
        preview=tuple((i * 7) % 32 for i in range(400)),
        detail=tuple((i * 3) % 32 for i in range(columns)),
        colour=tuple((i % 8, (i * 2) % 8, (i * 5) % 8) for i in range(columns)),
    )


def _touch(path, *, size: int | None = None) -> None:
    """Change the file the way an edit or a re-encode would."""
    if size is not None:
        path.write_bytes(b"\x00" * size)
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 10**9))


# ── The blob ────────────────────────────────────────────────────────────────


def test_encode_decode_is_lossless() -> None:
    wf = _waveform()
    back = waveform_cache.decode(waveform_cache.encode(wf), len(wf.preview), len(wf.detail))
    assert back == wf


def test_a_waveform_with_no_colour_survives() -> None:
    """analyse_waveform() returns this for audio it could not band-split."""
    wf = anlz.Waveform(preview=(1,) * 400, detail=(), colour=())
    back = waveform_cache.decode(waveform_cache.encode(wf), 400, 0)
    assert back == wf


def test_encoding_is_much_smaller_than_the_arrays() -> None:
    """A five-minute track at 150 columns a second.

    Not a performance assertion for its own sake: one row per track is what
    the whole feature costs on disk.
    """
    columns = 5 * 60 * anlz.DETAIL_RATE
    blob = waveform_cache.encode(_waveform(columns))
    assert len(blob) < (400 + columns * 4) // 2


def test_decode_rejects_a_corrupt_blob() -> None:
    assert waveform_cache.decode(b"not zlib at all", 400, 64) is None


def test_decode_rejects_a_blob_of_the_wrong_length() -> None:
    """A row whose body does not match its own column counts is not usable."""
    assert waveform_cache.decode(zlib.compress(b"\x01\x02\x03"), 400, 64) is None


# ── Round trips ─────────────────────────────────────────────────────────────


def test_a_stored_waveform_comes_back(track) -> None:
    wf = _waveform()
    waveform_cache.store(track, wf)
    assert waveform_cache.load(track) == wf


def test_a_real_analysis_survives_the_round_trip(tmp_path) -> None:
    """The whole feature in one assertion: analyse once, read it back."""
    np = pytest.importorskip("numpy")
    sf = pytest.importorskip("soundfile")
    p = tmp_path / "tone.wav"
    rate = 44100
    t = np.arange(int(rate * 1.5)) / rate
    sf.write(p, (0.4 * np.sin(2 * np.pi * 220 * t)).astype("float32"), rate)

    analysed = anlz.analyse_waveform(p)
    assert analysed is not None
    waveform_cache.store(p, analysed)
    assert waveform_cache.load(p) == analysed


def test_an_unknown_track_is_a_miss(track) -> None:
    assert waveform_cache.load(track) is None


def test_storing_twice_replaces_rather_than_accumulates(track) -> None:
    waveform_cache.store(track, _waveform(32))
    second = _waveform(64)
    waveform_cache.store(track, second)

    assert waveform_cache.load(track) == second
    with db.get_conn() as c:
        rows = c.execute("SELECT COUNT(*) AS n FROM track_waveforms").fetchone()
    assert rows["n"] == 1


def test_many_are_fetched_in_one_query(tmp_path) -> None:
    paths = []
    for i in range(5):
        p = tmp_path / f"t{i}.mp3"
        p.write_bytes(b"\x00" * (1024 + i))
        waveform_cache.store(p, _waveform(8 + i))
        paths.append(p)
    uncached = tmp_path / "new.mp3"
    uncached.write_bytes(b"\x00")

    hits = waveform_cache.load_many([*paths, uncached])
    assert set(hits) == {str(p) for p in paths}
    assert hits[str(paths[2])] == _waveform(10)


def test_load_many_of_nothing_touches_no_database() -> None:
    assert waveform_cache.load_many([]) == {}


# ── Not trusting a stale row ────────────────────────────────────────────────


def test_an_edited_file_misses(track) -> None:
    waveform_cache.store(track, _waveform())
    _touch(track)
    assert waveform_cache.load(track) is None


def test_a_file_that_changed_size_misses(track) -> None:
    waveform_cache.store(track, _waveform())
    _touch(track, size=4096)
    assert waveform_cache.load(track) is None


def test_a_superseded_analysis_misses(track, monkeypatch) -> None:
    """Improving the analysis must invalidate every row at a stroke."""
    waveform_cache.store(track, _waveform())
    assert waveform_cache.load(track) is not None
    monkeypatch.setattr(anlz, "WAVEFORM_VERSION", anlz.WAVEFORM_VERSION + 1)
    assert waveform_cache.load(track) is None


def test_a_row_written_by_a_newer_version_is_not_read(track, monkeypatch) -> None:
    """Downgrading the app must not read rows a newer analysis wrote."""
    current = anlz.WAVEFORM_VERSION
    monkeypatch.setattr(anlz, "WAVEFORM_VERSION", current + 1)
    waveform_cache.store(track, _waveform())
    # Not monkeypatch.undo(): that would also undo the database redirect this
    # module's autouse fixture installed, and the test would look at the real
    # database.
    monkeypatch.setattr(anlz, "WAVEFORM_VERSION", current)
    assert waveform_cache.load(track) is None


def test_an_unreadable_database_is_a_miss_not_an_error(track, monkeypatch) -> None:
    """An export must survive a database that cannot be read — decoding the
    audio is slower, but it still produces a correct stick."""

    def boom(*_a, **_kw):
        raise RuntimeError("database is locked")

    waveform_cache.store(track, _waveform())
    monkeypatch.setattr(db, "get_waveforms", boom)
    assert waveform_cache.load(track) is None


def test_a_missing_file_misses(tmp_path) -> None:
    gone = tmp_path / "gone.mp3"
    assert waveform_cache.load(gone) is None


def test_a_corrupt_row_is_a_miss_not_an_error(track) -> None:
    waveform_cache.store(track, _waveform())
    with db.get_conn() as c:
        c.execute("UPDATE track_waveforms SET blob = ?", (b"rubbish",))
        c.commit()
    assert waveform_cache.load(track) is None


def test_a_store_that_cannot_be_written_does_not_raise(track, monkeypatch) -> None:
    """A cache that cannot be written is slow, not broken — an export that
    otherwise worked must still finish."""

    def boom(*_a, **_kw):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(db, "upsert_waveform", boom)
    waveform_cache.store(track, _waveform())  # must not raise


# ── Following the tracks table ──────────────────────────────────────────────


def test_removing_a_track_drops_its_waveform(track) -> None:
    """These are the largest rows the database holds; a pruned track must
    take its waveform with it."""
    db.upsert_track(str(track), track.name, 128.0, 1, True, "8B", 60.0)
    waveform_cache.store(track, _waveform())

    db.remove_track(str(track))
    assert waveform_cache.load(track) is None


def test_pruning_tracks_not_in_the_collection_drops_their_waveforms(tmp_path) -> None:
    kept, gone = tmp_path / "kept.mp3", tmp_path / "gone.mp3"
    for p in (kept, gone):
        p.write_bytes(b"\x00" * 1024)
        db.upsert_track(str(p), p.name, 128.0, 1, True, "8B", 60.0)
        waveform_cache.store(p, _waveform())

    assert db.remove_tracks_not_in([str(kept)]) == 1
    assert waveform_cache.load(gone) is None
    assert waveform_cache.load(kept) is not None


def test_pruning_files_that_vanished_drops_their_waveforms(tmp_path) -> None:
    p = tmp_path / "vanishes.mp3"
    p.write_bytes(b"\x00" * 1024)
    db.upsert_track(str(p), p.name, 128.0, 1, True, "8B", 60.0)
    waveform_cache.store(p, _waveform())
    p.unlink()

    assert db.remove_missing_tracks() == 1
    with db.get_conn() as c:
        rows = c.execute("SELECT COUNT(*) AS n FROM track_waveforms").fetchone()
    assert rows["n"] == 0
