"""Read a Spotify playlist: its name and its tracks, without an account.

The playlist's embed page (the player Spotify lets sites put in theirs)
carries the playlist as JSON in its ``__NEXT_DATA__`` script: name, and per
track the title, the artists, the length and the track's id. Checked
2026-10-04 on a 50-track public playlist. No developer app, no login.

The embed shows a limited number of tracks for long playlists (believed to
be 100, not yet measured); :attr:`Playlist.maybe_truncated` says when the
count sits exactly at a round limit, so the caller can say so.
"""

from __future__ import annotations

import json
import re
import urllib.request
from dataclasses import dataclass

EMBED_URL = "https://open.spotify.com/embed/playlist/{id}"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/605.1.15"
TIMEOUT_S = 20

_ID = re.compile(r"(?:playlist[/:])([A-Za-z0-9]{22})")
_NEXT_DATA = re.compile(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S)
#: Counts at which a playlist may have been cut short by the embed.
_ROUND_LIMITS = {100, 200}


class SpotifyError(Exception):
    """The link is not a playlist, or Spotify did not return one."""


class SpotifyUnreachable(SpotifyError):
    """Spotify could not be reached at all: offline, DNS, a timeout."""


class SpotifyHTTPError(SpotifyError):
    """Spotify answered with an error status."""

    def __init__(self, code: int, message: str, retry_after: float = 0.0) -> None:
        super().__init__(message)
        self.code = code
        #: Seconds Spotify asked us to wait (429's Retry-After header), or 0.
        self.retry_after = retry_after


class LoginNeeded(SpotifyError):
    """A browser login is needed, and the caller runs unattended."""


@dataclass(frozen=True)
class SpotifyTrack:
    id: str  # Spotify's track id
    artist: str  # all artists, as Spotify lists them ("A, B")
    title: str
    duration_s: float


@dataclass(frozen=True)
class Playlist:
    id: str
    name: str
    tracks: tuple[SpotifyTrack, ...]

    @property
    def maybe_truncated(self) -> bool:
        return len(self.tracks) in _ROUND_LIMITS


def playlist_id(link: str) -> str:
    """The id in an open.spotify.com/playlist/… link or a spotify:playlist:… URI."""
    m = _ID.search(link.strip())
    if not m:
        raise SpotifyError("not a Spotify playlist link")
    return m.group(1)


def parse_embed(html: str, pid: str) -> Playlist:
    m = _NEXT_DATA.search(html)
    if not m:
        raise SpotifyError("Spotify's page had no playlist data (the page may have changed)")
    try:
        entity = json.loads(m.group(1))["props"]["pageProps"]["state"]["data"]["entity"]
    except (KeyError, TypeError, ValueError) as exc:
        raise SpotifyError(f"Spotify's playlist data was not as expected: {exc}") from exc
    tracks = []
    for t in entity.get("trackList") or []:
        uri = t.get("uri") or ""
        if not uri.startswith("spotify:track:"):
            continue  # episodes and local files have no track to find
        tracks.append(
            SpotifyTrack(
                id=uri.rsplit(":", 1)[-1],
                artist=(t.get("subtitle") or "").replace(" ", " ").strip(),
                title=(t.get("title") or "").strip(),
                duration_s=(t.get("duration") or 0) / 1000,
            )
        )
    name = entity.get("name") or entity.get("title") or pid
    return Playlist(pid, name.strip(), tuple(tracks))


def fetch(link: str) -> Playlist:
    """The playlist behind *link*; raises :class:`SpotifyError` with a reason."""
    pid = playlist_id(link)
    request = urllib.request.Request(EMBED_URL.format(id=pid), headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
            html = response.read().decode("utf-8", errors="replace")
    except OSError as exc:
        raise SpotifyError(f"could not reach Spotify: {exc}") from exc
    return parse_embed(html, pid)


TRACK_PAGE = "https://open.spotify.com/track/{id}"
_META = re.compile(r'<meta (?:name|property)="([^"]+)" content="([^"]*)"')


@dataclass(frozen=True)
class TrackInfo:
    """What a track's public page says about it beyond the playlist."""

    album: str = ""
    release_date: str = ""  # "2026-09-25", or just the year
    track_number: str = ""
    cover_url: str = ""  # 640×640

    @property
    def year(self) -> str:
        return self.release_date[:4]


def info_from_page(page: str) -> TrackInfo:
    """Album ("Artist · Album · Song · Year"), date, track number and cover
    from a track page's meta tags."""
    import html as html_lib

    meta: dict[str, str] = {}
    for name, value in _META.findall(page):
        meta.setdefault(name, html_lib.unescape(value))
    fields = [f.strip() for f in meta.get("og:description", "").split("·")]
    return TrackInfo(
        album=fields[1] if len(fields) >= 3 else "",
        release_date=meta.get("music:release_date", ""),
        track_number=meta.get("music:album:track", ""),
        cover_url=meta.get("og:image", ""),
    )


def album_from_page(page: str) -> str:
    return info_from_page(page).album


def _get(url: str) -> bytes | None:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
            return response.read()
    except OSError:
        return None


def track_info(track_id: str) -> TrackInfo:
    """*track_id*'s album, date, number and cover, from its public page; empty
    fields when Spotify does not answer. (The playlist's embed page has none
    of these; this costs one request a track, so it is asked only for tracks
    actually placed.)"""
    page = _get(TRACK_PAGE.format(id=track_id))
    return info_from_page(page.decode("utf-8", errors="replace")) if page else TrackInfo()


def cover(url: str) -> bytes | None:
    """The cover image's bytes (JPEG), or None."""
    return _get(url) if url.startswith("https://i.scdn.co/") else None


# ── the official API (optional: a developer app's client id and secret) ─────

TOKEN_URL = "https://accounts.spotify.com/api/token"


def api_token(client_id: str, secret: str) -> str:
    """An app access token (client credentials); raises SpotifyError saying why."""
    import base64
    import json as json_lib
    from urllib.error import HTTPError
    from urllib.parse import urlencode

    auth = base64.b64encode(f"{client_id}:{secret}".encode()).decode()
    request = urllib.request.Request(
        TOKEN_URL,
        data=urlencode({"grant_type": "client_credentials"}).encode(),
        headers={"Authorization": f"Basic {auth}", "User-Agent": USER_AGENT},
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
            return json_lib.loads(response.read())["access_token"]
    except HTTPError as exc:
        raise SpotifyError(f"Spotify refused the key ({exc.code})") from exc
    except (OSError, KeyError, ValueError) as exc:
        raise SpotifyError(f"could not reach Spotify: {exc}") from exc
