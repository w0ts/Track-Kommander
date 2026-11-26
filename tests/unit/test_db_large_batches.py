"""Queries that name many tracks at once.

SQLite limits how many values one statement can bind — 32,766 in the build
Python ships, 999 in older ones. A query with one placeholder per track works
on a small library and raises on a large one.
"""

from __future__ import annotations

import sqlite3

import pytest

import core.db as db


@pytest.fixture
def library(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_db_path", lambda: tmp_path / "t.db")
    db.init_db()
    with db.get_conn() as c:
        c.executemany(
            "INSERT INTO tracks (path, filename, artist, title) VALUES (?, ?, ?, ?)",
            [(f"/m/{i}.mp3", f"{i}.mp3", f"Artist {i}", f"Title {i}") for i in range(50)],
        )
        c.commit()


def _more_than_one_statement_can_bind() -> int:
    with sqlite3.connect(":memory:") as c:
        return c.getlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER) + 10


def test_metadata_for_more_tracks_than_one_statement_can_name(library) -> None:
    asked = [f"/m/{i}.mp3" for i in range(_more_than_one_statement_can_bind())]

    held = db.get_metadata_batch(asked)

    assert len(held) == 50
    assert held["/m/7.mp3"][0] == "Artist 7"
    assert held["/m/7.mp3"][2] == "Title 7"


def test_metadata_for_no_tracks_is_nothing(library) -> None:
    assert db.get_metadata_batch([]) == {}
