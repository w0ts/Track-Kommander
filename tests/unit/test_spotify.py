"""Reading Spotify's public pages (core.spotify): a playlist's embed page, a track's page."""

from __future__ import annotations

import json

import pytest

from core import spotify

EMBED = """<html><script id="__NEXT_DATA__" type="application/json">{data}</script></html>"""


def _embed(tracks: list[dict], name: str = "My Playlist #9") -> str:
    data = {
        "props": {"pageProps": {"state": {"data": {"entity": {"name": name, "trackList": tracks}}}}}
    }
    return EMBED.format(data=json.dumps(data))


def test_a_playlist_is_read_from_spotifys_embed_page():
    html = _embed(
        [
            {
                "uri": "spotify:track:aaa",
                "title": "Bone Mics",
                "subtitle": "Overmono",
                "duration": 301500,
            },
            {"uri": "spotify:episode:zzz", "title": "A podcast", "subtitle": "x", "duration": 1},
        ]
    )
    pl = spotify.parse_embed(html, "pid")
    assert pl.name == "My Playlist #9"
    assert pl.tracks == (spotify.SpotifyTrack("aaa", "Overmono", "Bone Mics", 301.5),)


def test_a_page_without_data_says_so():
    with pytest.raises(spotify.SpotifyError):
        spotify.parse_embed("<html></html>", "pid")


TRACK_PAGE = """<html><head>
<meta property="og:title" content="Patient Zero"/>
<meta property="og:description" content="Taylor Swift · The Life of a Showgirl: The Encore · Song · 2026"/>
<meta property="og:image" content="https://i.scdn.co/image/abc"/>
<meta name="music:album:track" content="1"/>
<meta name="music:release_date" content="2026-09-25"/>
</head></html>"""


def test_a_track_page_gives_album_date_number_and_cover():
    info = spotify.info_from_page(TRACK_PAGE)
    assert info == spotify.TrackInfo(
        "The Life of a Showgirl: The Encore", "2026-09-25", "1", "https://i.scdn.co/image/abc"
    )
    assert info.year == "2026"
