"""Spotify metadata from the playlist jobs (core.spotify_store): synced by path,
the release year's fallback, and the Discogs search's names."""

from __future__ import annotations

from dataclasses import dataclass, field
from os.path import normpath

import pytest

import core.db as db
from core import discogs_store, spotify_store
from core import track_meta as tm


@dataclass(frozen=True)
class _Track:
    id: str
    artist: str
    title: str
    duration_s: float
    had: str = ""
    placed: str = ""
    album: str = ""
    release_date: str = ""
    track_number: str = ""


@dataclass(frozen=True)
class _Job:
    name: str
    tracks: tuple = field(default_factory=tuple)


@pytest.fixture()
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_db_path", lambda: tmp_path / "t.db")
    db.init_db()


def _job() -> _Job:
    return _Job(
        "Techno finds",
        (
            _Track("s1", "Tiga, Audion", "Let's Go Dancing", 412.0, placed=normpath("/m/a.mp3"),
                   album="Ciao!", release_date="2009-04-20", track_number="3"),
            _Track("s2", "Aero", "Come Again", 401.0, had=normpath("/m/b.mp3")),
            _Track("s3", "Nobody", "Not Found", 300.0),  # neither had nor placed
        ),
    )  # fmt: skip


def test_sync_keys_by_path_and_reports_what_is_new(fresh_db) -> None:
    assert sorted(spotify_store.sync([_job()])) == [normpath("/m/a.mp3"), normpath("/m/b.mp3")]
    assert spotify_store.sync([_job()]) == []  # nothing new the second time
    q = spotify_store.query_for(normpath("/m/a.mp3"))
    assert q is not None and (q.artist, q.title, q.album, q.seconds) == (
        "Tiga, Audion",
        "Let's Go Dancing",
        "Ciao!",
        412.0,
    )


def test_a_playlist_track_is_asked_with_spotifys_names_even_with_empty_tags(fresh_db) -> None:
    db.upsert_track(
        normpath("/m/a.mp3"), "a.mp3", 128.0, 1, True, "8A", 412.0
    )  # no artist/title tags
    db.upsert_track(
        normpath("/m/c.mp3"), "c.mp3", 128.0, 1, True, "8A", 300.0
    )  # no tags, no playlist
    spotify_store.sync([_job()])
    todo = dict(discogs_store.pending())
    assert set(todo) == {normpath("/m/a.mp3")}
    assert todo[normpath("/m/a.mp3")].artist == "Tiga, Audion"


def test_removing_a_track_removes_its_spotify_row(fresh_db) -> None:
    db.upsert_track(normpath("/m/a.mp3"), "a.mp3", 128.0, 1, True, "8A", 412.0)
    spotify_store.sync([_job()])
    db.remove_track(normpath("/m/a.mp3"))
    assert spotify_store.query_for(normpath("/m/a.mp3")) is None


def test_spotify_dates_the_record_only_when_discogs_cannot() -> None:
    assert tm.effective_year("2024/1/1", None, None, "2009-04-20") == 2009
    assert tm.effective_year("2024/1/1", 1994, 2016, "2009-04-20") == 1994
    assert tm.effective_year("2024/1/1", None, 2016, "2009") == 2016
    assert tm.effective_year("2024/1/1", None, None, None) == 2024
