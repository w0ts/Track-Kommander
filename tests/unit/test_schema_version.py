"""The library database knows which version of the application shaped it.

It did not. Every start re-ran every repair ever written, and dropped the
columns this build has retired — so a build older than the database would
have dropped columns a newer one had added back, and nothing anywhere said
"this library was written by a newer Track Kommander".

Now the database carries a version (SQLite's ``user_version``). A change that
is done once is a numbered step, run once, in order, and recorded. A database
from the future is left in the shape it has.
"""

from __future__ import annotations

import logging
import sqlite3

import pytest

import core.db as db
from core.feature_bits import mask_for, missing_features


@pytest.fixture
def path(tmp_path, monkeypatch):
    target = tmp_path / "library.db"
    monkeypatch.setattr(db, "_db_path", lambda: target)
    return target


def _version(path) -> int:
    with sqlite3.connect(path) as c:
        return c.execute("PRAGMA user_version").fetchone()[0]


def _columns(path) -> set[str]:
    with sqlite3.connect(path) as c:
        return {r[1] for r in c.execute("PRAGMA table_info(tracks)")}


def _an_old_library(path) -> None:
    """As a build from before versions left it: no version, a retired
    column, and a track analysed before there was a mask."""
    with sqlite3.connect(path) as c:
        c.execute(
            "CREATE TABLE tracks (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "path TEXT UNIQUE NOT NULL, filename TEXT NOT NULL, bpm REAL, "
            "key_num INTEGER, is_major INTEGER, camelot TEXT, "
            "duration REAL, analyzed_at TEXT, energy REAL, melodic REAL)"
        )
        c.execute(
            "INSERT INTO tracks (path, filename, energy, melodic) "
            "VALUES ('/m/a.mp3', 'a.mp3', 0.5, 0.7)"
        )
        c.commit()


# ── A new library ───────────────────────────────────────────────────────────


def test_a_new_library_is_at_the_current_version(path) -> None:
    db.init_db()
    assert _version(path) == db.SCHEMA_VERSION
    assert db.SCHEMA_VERSION == len(db.MIGRATIONS) >= 1
    assert not db.written_by_a_newer_build()


# ── A library from before versions ──────────────────────────────────────────


def test_an_old_library_is_brought_up_to_date(path) -> None:
    _an_old_library(path)

    db.init_db()

    assert _version(path) == db.SCHEMA_VERSION
    assert "energy" not in _columns(path), "a retired column"
    assert "computed" in _columns(path)
    with sqlite3.connect(path) as c:
        mask = c.execute("SELECT computed FROM tracks").fetchone()[0]
    assert missing_features(mask, mask_for(["melodic"])) == []


def test_what_is_done_once_is_not_done_again(path) -> None:
    """The repairs read the whole table. At every start, before the window
    exists, that was 367 ms on a library of 20,000."""
    _an_old_library(path)
    db.init_db()
    seen: list[str] = []
    real = db.get_conn

    def traced() -> sqlite3.Connection:
        c = real()
        c.set_trace_callback(seen.append)
        return c

    db.get_conn = traced
    try:
        db.init_db()
    finally:
        db.get_conn = real

    touched = [
        s
        for s in seen
        if s.lstrip().upper().startswith(("UPDATE", "DELETE", "ALTER TABLE TRACKS DROP"))
    ]
    assert touched == []
    assert _version(path) == db.SCHEMA_VERSION


def test_a_new_column_still_arrives_without_a_new_version(path, monkeypatch) -> None:
    """Adding a column is cheap to check and harmless to repeat, so it does
    not need a step of its own."""
    db.init_db()
    monkeypatch.setattr(db, "_ADDED_COLUMNS", (*db._ADDED_COLUMNS, "brand_new REAL"))

    db.init_db()

    assert "brand_new" in _columns(path)


# ── A step that fails ───────────────────────────────────────────────────────


def test_a_step_that_fails_is_not_recorded_as_done(path, monkeypatch) -> None:
    _an_old_library(path)

    def fails(_c: sqlite3.Connection) -> None:
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(db, "MIGRATIONS", (fails,))
    with pytest.raises(sqlite3.OperationalError):
        db.init_db()

    assert _version(path) == 0, "or the next start would skip it"


def test_steps_run_in_order_from_where_the_library_is(path, monkeypatch) -> None:
    ran: list[str] = []
    monkeypatch.setattr(db, "MIGRATIONS", (lambda c: ran.append("one"),))
    monkeypatch.setattr(db, "SCHEMA_VERSION", 1)
    db.init_db()

    monkeypatch.setattr(
        db,
        "MIGRATIONS",
        (lambda c: ran.append("one"), lambda c: ran.append("two"), lambda c: ran.append("three")),
    )
    monkeypatch.setattr(db, "SCHEMA_VERSION", 3)
    db.init_db()

    assert ran == ["one", "two", "three"]
    assert _version(path) == 3


# ── A library from the future ───────────────────────────────────────────────


def test_a_library_from_a_newer_build_is_left_in_its_shape(path, caplog) -> None:
    _an_old_library(path)
    with sqlite3.connect(path) as c:
        c.execute(f"PRAGMA user_version = {db.SCHEMA_VERSION + 1}")
        c.commit()

    with caplog.at_level(logging.WARNING):
        db.init_db()

    assert "energy" in _columns(path), "not ours to drop: we do not know it"
    assert _version(path) == db.SCHEMA_VERSION + 1
    assert db.written_by_a_newer_build()
    assert "newer" in caplog.text


def test_it_can_still_be_read_and_written(path) -> None:
    """Older code reading a newer library is the point of leaving it alone."""
    db.init_db()
    with sqlite3.connect(path) as c:
        c.execute(f"PRAGMA user_version = {db.SCHEMA_VERSION + 5}")
        c.execute(
            "INSERT INTO tracks (path, filename, artist) VALUES ('/m/a.mp3', 'a.mp3', 'Kuss')"
        )
        c.commit()

    db.init_db()

    assert db.get_metadata_batch(["/m/a.mp3"])["/m/a.mp3"][0] == "Kuss"
