"""Unit tests for DB ↔ collection sync — core.db.remove_tracks_not_in."""

from __future__ import annotations

from pathlib import Path

import pytest

import core.db as db


@pytest.fixture()
def fresh_db(tmp_path, monkeypatch):
    """A real on-disk SQLite DB with the live schema, isolated per test."""
    dbfile = tmp_path / "t.db"
    monkeypatch.setattr(db, "_db_path", lambda: dbfile)
    db.init_db()
    return dbfile


def _add(path: str) -> None:
    db.upsert_track(path, Path(path).name, 120.0, 1, True, "8A", 200.0)


def test_removes_only_orphans_not_in_collection(fresh_db):
    _add("/lib/a.mp3")
    _add("/lib/b.mp3")
    _add("/old/c.mp3")  # orphan — renamed folder, no longer in the collection
    removed = db.remove_tracks_not_in(["/lib/a.mp3", "/lib/b.mp3"])
    assert removed == 1
    assert db.get_track_count()[0] == 2


def test_empty_keep_set_never_wipes_db(fresh_db):
    """A failed/empty NML parse must not delete every track."""
    _add("/lib/a.mp3")
    assert db.remove_tracks_not_in([]) == 0
    assert db.get_track_count()[0] == 1


def test_no_orphans_removes_nothing(fresh_db):
    _add("/lib/a.mp3")
    assert db.remove_tracks_not_in(["/lib/a.mp3", "/lib/extra.mp3"]) == 0
    assert db.get_track_count()[0] == 1


def test_prunes_orphan_even_when_file_exists(fresh_db, tmp_path):
    """The orphan's audio may still sit on disk (a duplicate folder); it must
    still be pruned, which remove_missing_tracks alone could never do."""
    real = tmp_path / "still_here.mp3"
    real.write_bytes(b"x")
    _add(str(real))
    assert db.remove_missing_tracks() == 0  # file exists → not "missing"
    assert db.remove_tracks_not_in(["/some/other.mp3"]) == 1
    assert db.get_track_count()[0] == 0
