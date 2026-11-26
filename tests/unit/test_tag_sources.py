"""Filling Traktor from Discogs and Spotify together (core.tag_sources): each
field from whichever source has it; genre/style in the user's order."""

from __future__ import annotations

from dataclasses import dataclass
from os.path import normpath

import pytest

import core.db as db
from core import spotify_store
from core.discogs_fields import DEFAULT_MAPPING, DiscogsRecord, planned_changes
from core.spotify_store import SpotifyInfo
from core.tag_sources import GENRE_SOURCES, merge, merged_records

DISCOGS = DiscogsRecord(
    1, ("Electronic",), ("Techno",), "Planet Rhythm", "PRR 1", "UK", "2019-04-12", 2019, 2019
)
SPOTIFY = SpotifyInfo("Album", "2021-06-01", "Ninja Tune", ("big beat",), True, "search")
BARE = SpotifyInfo("Album", "2021-06-01", "Ninja Tune", (), True, "search")


def test_discogs_first_spotify_where_discogs_has_nothing() -> None:
    no_label = DiscogsRecord(1, ("Electronic",), ("Techno",), "", released="2019", year=2019)
    record = merge(no_label, SPOTIFY)
    assert record is not None
    assert record.label == "Ninja Tune" and record.source_of("label") == "Spotify"
    assert record.year == 2019 and record.source_of("year") == "Discogs"
    assert record.styles == ("Techno",) and record.source_of("styles") == "Discogs"


def test_spotify_alone_fills_date_and_label() -> None:
    record = merge(None, BARE)
    assert record is not None
    assert (record.released, record.year, record.label) == ("2021-06-01", 2021, "Ninja Tune")
    changes = {c.field.key: c for c in planned_changes(record, {}, DEFAULT_MAPPING)}
    assert changes["release_date"].new == "2021/6/1" and changes["release_date"].source == "Spotify"
    assert changes["label"].source == "Spotify"
    assert "genre" not in changes  # Spotify had no genres


@pytest.mark.parametrize(
    ("order", "styles", "genres"),
    [
        ("discogs,spotify", ("Techno",), ("Electronic",)),
        ("spotify,discogs", (), ("big beat",)),
        ("discogs", ("Techno",), ("Electronic",)),
        ("spotify", (), ("big beat",)),
    ],
)
def test_genre_sources_in_the_users_order(order, styles, genres) -> None:
    record = merge(DISCOGS, SPOTIFY, GENRE_SOURCES[order])
    assert record is not None and (record.styles, record.genres) == (styles, genres)


def test_spotify_only_genres_fill_nothing_when_spotify_has_none() -> None:
    record = merge(DISCOGS, BARE, GENRE_SOURCES["spotify"])
    assert record is not None and (record.styles, record.genres) == ((), ())


def test_merged_records_cover_either_source() -> None:
    got = merged_records({"/a": DISCOGS}, {"/b": BARE})
    assert set(got) == {"/a", "/b"}


@dataclass(frozen=True)
class _Details:
    spotify_id: str = "sp1"
    artist: str = "Bicep"
    title: str = "Glue"
    album: str = "Bicep"
    release_date: str = "2017-09-01"
    track_number: int | None = 2
    duration_s: float = 269.0
    label: str = "Ninja Tune"
    isrc: str = "GBCFB1700586"
    genres: tuple = ()
    sure: bool = True


def test_searches_are_stored_and_only_sure_ones_fill(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(db, "_db_path", lambda: tmp_path / "t.db")
    db.init_db()
    spotify_store.save_search(normpath("/m/a.mp3"), _Details())
    spotify_store.save_search(normpath("/m/b.mp3"), _Details(sure=False))
    infos = spotify_store.infos()
    assert set(infos) == {normpath("/m/a.mp3")}
    assert (
        infos[normpath("/m/a.mp3")].label == "Ninja Tune"
        and infos[normpath("/m/a.mp3")].source == "search"
    )
    assert spotify_store.has(normpath("/m/b.mp3"))  # kept, so it is not asked again


def test_a_search_never_replaces_a_playlists_row(tmp_path, monkeypatch) -> None:
    from types import SimpleNamespace

    monkeypatch.setattr(db, "_db_path", lambda: tmp_path / "t.db")
    db.init_db()
    track = SimpleNamespace(
        id="pl1", artist="Bicep", title="Glue", duration_s=269.0, had="", placed=normpath("/m/a.mp3"),
        album="From The Playlist", release_date="2017-09-01", track_number="2",
    )  # fmt: skip
    spotify_store.sync([SimpleNamespace(name="P", tracks=(track,))])
    spotify_store.save_search(normpath("/m/a.mp3"), _Details(album="Other"))
    assert spotify_store.infos()[normpath("/m/a.mp3")].album == "From The Playlist"
