"""Per-track and per-playlist comments: a sidecar the user writes, never
touching Traktor's collection. Scopes are independent, a blank note clears, and
the text is stripped.
"""

from __future__ import annotations

import pytest

from core import comments_store, db
from core.comments_store import GENERAL


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    dbfile = tmp_path / "t.db"
    monkeypatch.setattr(db, "_db_path", lambda: dbfile)
    db.init_db()


def test_absent_comment_reads_empty(fresh_db) -> None:
    assert comments_store.get_comment("/m/a.mp3") == ""
    assert comments_store.has_any_comment("/m/a.mp3") is False


def test_set_then_get_general(fresh_db) -> None:
    comments_store.set_comment("/m/a.mp3", "great opener")
    assert comments_store.get_comment("/m/a.mp3") == "great opener"
    assert comments_store.has_any_comment("/m/a.mp3") is True


def test_per_playlist_scopes_are_independent(fresh_db) -> None:
    comments_store.set_comment("/m/a.mp3", "general note")
    comments_store.set_comment("/m/a.mp3", "techno set note", playlist="Techno")
    assert comments_store.get_comment("/m/a.mp3") == "general note"
    assert comments_store.get_comment("/m/a.mp3", playlist="Techno") == "techno set note"
    assert comments_store.get_comment("/m/a.mp3", playlist="House") == ""


def test_set_overwrites(fresh_db) -> None:
    comments_store.set_comment("/m/a.mp3", "first")
    comments_store.set_comment("/m/a.mp3", "second")
    assert comments_store.get_comment("/m/a.mp3") == "second"


def test_blank_clears(fresh_db) -> None:
    comments_store.set_comment("/m/a.mp3", "note")
    comments_store.set_comment("/m/a.mp3", "   ")  # whitespace only
    assert comments_store.get_comment("/m/a.mp3") == ""
    assert comments_store.has_any_comment("/m/a.mp3") is False


def test_comment_is_stripped(fresh_db) -> None:
    comments_store.set_comment("/m/a.mp3", "  padded  ")
    assert comments_store.get_comment("/m/a.mp3") == "padded"


def test_comments_for_track_maps_scopes(fresh_db) -> None:
    comments_store.set_comment("/m/a.mp3", "gen")
    comments_store.set_comment("/m/a.mp3", "tk", playlist="Techno")
    assert comments_store.comments_for_track("/m/a.mp3") == {GENERAL: "gen", "Techno": "tk"}


def test_comments_for_playlist_maps_tracks(fresh_db) -> None:
    comments_store.set_comment("/m/a.mp3", "a", playlist="Set 1")
    comments_store.set_comment("/m/b.mp3", "b", playlist="Set 1")
    comments_store.set_comment("/m/c.mp3", "c", playlist="Set 2")
    assert comments_store.comments_for_playlist("Set 1") == {"/m/a.mp3": "a", "/m/b.mp3": "b"}


def test_clearing_one_scope_keeps_the_other(fresh_db) -> None:
    comments_store.set_comment("/m/a.mp3", "gen")
    comments_store.set_comment("/m/a.mp3", "tk", playlist="Techno")
    comments_store.set_comment("/m/a.mp3", "", playlist="Techno")
    assert comments_store.get_comment("/m/a.mp3") == "gen"
    assert comments_store.get_comment("/m/a.mp3", playlist="Techno") == ""
    assert comments_store.has_any_comment("/m/a.mp3") is True
