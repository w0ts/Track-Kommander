"""TK's Spotify search for tags (core.spotify_api): the right track among its versions, and what
Spotify knows about it."""

from __future__ import annotations

import pytest

from core import spotify_api as api


def _search_answer(*items):
    return {
        "tracks": {
            "items": [
                {
                    "uri": f"spotify:track:{i}",
                    "name": name,
                    "duration_ms": ms,
                    "artists": [{"name": a} for a in artists],
                }
                for i, (name, artists, ms) in enumerate(items)
            ]
        }
    }


def test_the_sure_match_wins_over_the_first(monkeypatch):
    answer = _search_answer(
        ("Mommy (Radio Edit)", ["BRN"], 200_000),  # first, but the wrong length
        ("Mommy", ["BRN"], 492_000),
    )
    monkeypatch.setattr(api, "_call", lambda *a, **k: answer)
    match = api.search_track("t", "BRN", "Mommy (Original Mix)", 492.0)
    assert match.uri == "spotify:track:1" and match.sure


def test_nothing_found_is_none(monkeypatch):
    monkeypatch.setattr(api, "_call", lambda *a, **k: {"tracks": {"items": []}})
    assert api.search_track("t", "A", "T", 100.0) is None


def _spotify(url: str) -> dict:
    """Spotify's answers for one track, as its docs describe them."""
    path = url.removeprefix(api.API)
    if path.startswith("/search"):
        return _search_answer(("Mommy", ["BRN"], 492_000))
    if path.startswith("/tracks/0"):
        return {
            "track_number": 2,
            "external_ids": {"isrc": "GBKPL2100123"},
            "album": {"id": "alb", "name": "Mommy EP", "release_date": "2021-03-05"},
            "artists": [{"id": "a1"}, {"id": "a2"}],
        }
    if path.startswith("/albums/alb"):
        return {
            "copyrights": [
                {"text": "© 2021 X", "type": "C"},
                {"text": "℗ 2021 Typeless", "type": "P"},
            ]
        }
    raise AssertionError(path)


def test_a_tracks_details_come_with_isrc_and_the_label_from_the_p_line(monkeypatch):
    monkeypatch.setattr(api, "app_token", lambda: "t")
    monkeypatch.setattr(api, "_call", lambda method, url, *a, **k: _spotify(url))
    d = api.lookup_track("BRN", "Mommy", 492.0)
    assert d == api.TrackDetails(
        spotify_id="0",
        artist="BRN",
        title="Mommy",
        album="Mommy EP",
        release_date="2021-03-05",
        track_number=2,
        duration_s=492.0,
        label="Typeless",
        isrc="GBKPL2100123",
        sure=True,
    )


def test_a_track_spotify_does_not_know_is_none(monkeypatch):
    monkeypatch.setattr(api, "app_token", lambda: "t")
    monkeypatch.setattr(api, "_call", lambda *a, **k: {"tracks": {"items": []}})
    assert api.lookup_track("Nobody", "Nothing", 100.0) is None


def test_an_artist_named_in_the_title_is_searched_when_the_credit_is_wrong(monkeypatch):
    """Tagged "Piotr Blacha" (a mix's compiler); the title names Regis."""
    regis = {
        "uri": "spotify:track:asb",
        "name": "Asbestos - Sleeparchive Remix",
        "duration_ms": 347_000,
        "artists": [{"name": "Regis"}, {"name": "Sleeparchive"}],
    }
    asked = []

    def call(method, url, *a, **k):
        asked.append(url)
        hit = "Regis" in url.replace("%22", '"') or "regis" in url
        return {"tracks": {"items": [regis] if hit else []}}

    monkeypatch.setattr(api, "_call", call)
    match = api.search_track("t", "Piotr Blacha", "Regis - Asbestos (Sleeparchive Remix)", 349.0)
    assert match.uri == "spotify:track:asb" and match.sure
    assert len(asked) <= api.MAX_QUERIES


def test_the_callers_pacer_is_asked_before_every_request_and_can_stop(monkeypatch):
    monkeypatch.setattr(api, "app_token", lambda: "t")
    monkeypatch.setattr(api, "_call", lambda method, url, *a, **k: _spotify(url))
    asked = []
    api.lookup_track("BRN", "Mommy", 492.0, before_request=lambda: asked.append(1) or True)
    assert len(asked) == 3  # one search, the track, the album
    with pytest.raises(api.SpotifyError, match="stopped"):
        api.lookup_track("BRN", "Mommy", 492.0, before_request=lambda: False)


def test_another_song_is_not_found_rather_than_offered(monkeypatch):
    stranger = {
        "uri": "spotify:track:x",
        "name": "New Vision/Beatrice",
        "duration_ms": 363_000,
        "artists": [{"name": "Coconut Dalí"}],
    }
    monkeypatch.setattr(api, "_call", lambda *a, **k: {"tracks": {"items": [stranger]}})
    assert api.search_track("t", "Beatrice M./Jan Loup", "Shallow", 240.0) is None
