"""init_db's column migrations: only the ALTERs that are needed, and real
failures are not hidden.

Every launch used to fire ~50 ``ADD COLUMN`` and ~11 ``DROP COLUMN``
statements and swallow whatever came back. On an up-to-date database that
is 60 expected errors; it also swallowed "database is locked", disk I/O
errors and read-only volumes, leaving the app running on a half-migrated
schema.
"""

from __future__ import annotations

import sqlite3

import pytest

import core.db


@pytest.fixture
def db_mod(tmp_path, monkeypatch):
    monkeypatch.setattr(core.db, "_db_path", lambda: tmp_path / "t.db")
    return core.db


def _columns(path) -> set[str]:
    with sqlite3.connect(path) as c:
        return {r[1] for r in c.execute("PRAGMA table_info(tracks)")}


def _trace_statements(db_mod, monkeypatch) -> list[str]:
    """Record every SQL statement init_db attempts.

    A subclass rather than ``set_trace_callback``: the trace callback only
    fires for statements that compile, and the old migration loop's
    duplicate-column ALTERs fail at compile time, so it would miss them.
    """
    seen: list[str] = []

    class Recording(sqlite3.Connection):
        def execute(self, sql, *args):
            seen.append(sql)
            return super().execute(sql, *args)

    def recording_conn() -> sqlite3.Connection:
        conn = sqlite3.connect(str(db_mod._db_path()), factory=Recording)
        conn.row_factory = sqlite3.Row
        return conn

    monkeypatch.setattr(db_mod, "get_conn", recording_conn)
    return seen


def test_fresh_database_gets_every_column(db_mod, tmp_path) -> None:
    db_mod.init_db()
    cols = _columns(tmp_path / "t.db")
    for expected in (
        "artist",
        "rating",
        "genres_vector",
        "computed",
        "onset_density",
        "analysis_version",
    ):
        assert expected in cols
    for legacy in ("raw_energy", "energy", "genres_vector_clap"):
        assert legacy not in cols


def test_second_run_is_a_no_op(db_mod, tmp_path, monkeypatch) -> None:
    db_mod.init_db()
    before = _columns(tmp_path / "t.db")
    seen = _trace_statements(db_mod, monkeypatch)
    db_mod.init_db()
    assert _columns(tmp_path / "t.db") == before
    alters = [s for s in seen if "ALTER TABLE" in s.upper()]
    assert alters == []


def test_old_schema_is_brought_up_to_date(db_mod, tmp_path) -> None:
    with sqlite3.connect(tmp_path / "t.db") as c:
        c.execute(
            "CREATE TABLE tracks (id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " path TEXT UNIQUE NOT NULL, filename TEXT NOT NULL, bpm REAL,"
            " key_num INTEGER, is_major INTEGER, camelot TEXT, duration REAL,"
            " analyzed_at TEXT, raw_energy REAL, genres_vector_clap BLOB)"
        )
        c.execute(
            "INSERT INTO tracks (path, filename, bpm, raw_energy)"
            " VALUES ('/m/a.mp3', 'a.mp3', 124.0, 0.3)"
        )
    db_mod.init_db()
    cols = _columns(tmp_path / "t.db")
    assert "raw_energy" not in cols and "genres_vector_clap" not in cols
    assert "artist" in cols and "genres_vector" in cols
    with sqlite3.connect(tmp_path / "t.db") as c:
        assert c.execute("SELECT bpm FROM tracks WHERE path='/m/a.mp3'").fetchone() == (124.0,)


def test_a_failing_migration_is_not_swallowed(db_mod, tmp_path) -> None:
    """SQLite refuses to drop an indexed column. The old loop ignored that
    and carried on with the legacy column still there; now it surfaces."""
    with sqlite3.connect(tmp_path / "t.db") as c:
        c.execute(
            "CREATE TABLE tracks (id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " path TEXT UNIQUE NOT NULL, filename TEXT NOT NULL, bpm REAL,"
            " key_num INTEGER, is_major INTEGER, camelot TEXT, duration REAL,"
            " analyzed_at TEXT, energy REAL)"
        )
        c.execute("CREATE INDEX idx_legacy_energy ON tracks(energy)")
    with pytest.raises(sqlite3.OperationalError, match="energy"):
        db_mod.init_db()
