"""Spotify's Web API: find a track and what Spotify knows about it (tags, release date).

Needs the user's developer app (Settings ▸ Traktor ▸ Spotify): its client id and secret.
Searching uses the app's own token (client credentials, core.spotify.api_token). The app's
redirect URI, :data:`REDIRECT_URI`, is shown in Settings for add-ons that log the user in.

NOT YET TRIED against Spotify (written 2026-10-04 before the user had a
key); every call goes through :func:`_call`, which tests replace.
"""

from __future__ import annotations

import difflib
import json
import logging
import re
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote, urlencode

from . import artists
from .spotify import SpotifyError, SpotifyHTTPError, SpotifyUnreachable

log = logging.getLogger(__name__)

API = "https://api.spotify.com/v1"
TOKEN = "https://accounts.spotify.com/api/token"
REDIRECT_PORT = 8765
REDIRECT_URI = f"http://127.0.0.1:{REDIRECT_PORT}/callback"
#: How far a match's length may be from the file's.
LENGTH_TOLERANCE_S = 4.0


def _retry_after(exc: HTTPError) -> float:
    """Seconds Spotify asked us to wait, from the Retry-After header; 0 if none."""
    try:
        return max(0.0, float(exc.headers.get("Retry-After") or 0))
    except (TypeError, ValueError, AttributeError):
        return 0.0


def _call(
    method: str,
    url: str,
    token: str = "",
    data: dict | None = None,
    form: dict | None = None,
    auth: str = "",
) -> dict:
    """One HTTP request to Spotify, JSON in and out; SpotifyError on failure."""
    headers = {"Accept": "application/json"}
    body = None
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if auth:
        headers["Authorization"] = f"Basic {auth}"
    if data is not None:
        body = json.dumps(data).encode()
        headers["Content-Type"] = "application/json"
    if form is not None:
        body = urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read()
    except HTTPError as exc:
        raise SpotifyHTTPError(
            exc.code,
            f"Spotify said {exc.code}: {exc.read()[:200]!r}",
            _retry_after(exc),
        ) from exc
    except OSError as exc:
        raise SpotifyUnreachable(f"could not reach Spotify: {exc}") from exc
    return json.loads(raw) if raw else {}


# ── the user's login (PKCE) ──────────────────────────────────────────────────


@dataclass(frozen=True)
class Match:
    uri: str
    artist: str
    title: str
    duration_s: float
    sure: bool  # length and names agree


#: Requests one search may make before settling for what it found.
MAX_QUERIES = 8


def _who(artist: str, title: str, path: str = "") -> list[str]:
    """Names to search under, as written, likeliest first: the credit's
    artists, an artist named in front of the title ("Regis - Asbestos …",
    credited to a mix's compiler), the remixer, the file name's artists."""
    names = [p.strip() for p in artists.split_credit(artist)]
    head, sep, _rest = title.partition(" - ")
    if sep:
        names.append(head.strip())
    remixer = re.search(r"[(\[-]\s*([^()\[\]-]+?)\s+(?:remix|rework|edit|dub|mix)\b", title, re.I)
    if remixer and remixer.group(1).strip().lower() not in {
        "original",
        "extended",
        "radio",
        "club",
    }:
        names.append(remixer.group(1).strip())
    if path:
        stem_head = Path(path).stem.split(" - ")[0]
        names += [p.strip() for p in artists.split_credit(stem_head)]
    seen, out = set(), []
    for n in names:
        k = artists.key(n)
        if k and k not in seen:
            seen.add(k)
            out.append(n)
    return out


def _cores(title: str, artist: str) -> list[str]:
    """The bare song title, both ways "A - B" can be read: "Artist - Song"
    or "Song - Version"."""
    from core.song_names import song_title

    head, sep, rest = title.partition(" - ")
    found = [song_title(title, artist)] + ([song_title(rest, head)] if sep else [])
    return [c for c in dict.fromkeys(found) if c]


def _judge(
    item: dict, who: list[str], cores: list[str], title: str, duration_s: float
) -> tuple[Match, bool]:
    from core.song_names import remix_of, song_title

    names = ", ".join(a["name"] for a in item.get("artists", []))
    name = item.get("name", "")
    length = item.get("duration_ms", 0) / 1000
    theirs = song_title(name, names)
    same_song = any(
        c == theirs or difflib.SequenceMatcher(None, c, theirs).ratio() >= 0.85 for c in cores
    )
    by_them = not who or any(artists.share(w, names) for w in who)
    sure = (
        abs(length - duration_s) <= LENGTH_TOLERANCE_S
        and same_song
        and remix_of(name) == remix_of(title)
        and by_them
    )
    match = Match(item["uri"], names, name, length, sure)
    return match, same_song and by_them  # (match, close enough to mention)


def _gate(before) -> None:
    """Ask the caller's pacer before a request: False means stop."""
    if before is not None and not before():
        raise SpotifyError("stopped")


def search_track(
    token: str,
    artist: str,
    title: str,
    duration_s: float,
    path: str = "",
    before=None,
) -> Match | None:
    """The best Spotify track for a file; ``sure`` when its length, song,
    remix and an artist fit. Tries the likely artists one by one with the
    bare title (a file's credit is often wrong: "Piotr Blacha" for Regis
    "Asbestos"), then free text; stops at the first sure match. *before*,
    if given, is asked before each request (see :func:`lookup_track`)."""
    who, cores = _who(artist, title, path), _cores(title, artist)
    queries = [f'track:"{c}" artist:"{w}"' for w in who for c in cores]
    queries += [f"{w} {c}" for w in who[:2] for c in cores[:1]]
    queries = queries or [f'track:"{title}"']
    best = None
    for query in list(dict.fromkeys(queries))[:MAX_QUERIES]:
        _gate(before)
        found = _call("GET", f"{API}/search?type=track&limit=5&q={quote(query)}", token)
        for item in (found.get("tracks") or {}).get("items") or []:
            match, close = _judge(item, who, cores, title, duration_s)
            if match.sure:
                return match
            if close:  # the same song by them, another length or remix
                best = best or match
    return best  # None: nothing that is even the same song


@dataclass(frozen=True)
class TrackDetails:
    """What Spotify says about one track. ``sure``: length and names agree."""

    spotify_id: str
    artist: str
    title: str
    album: str
    release_date: str  # "2026-09-25", "2026-09" or "2026"
    track_number: int | None
    duration_s: float
    label: str = ""  # the album's label
    isrc: str = ""
    genres: tuple[str, ...] = ()  # the artists' genres (often empty)
    sure: bool = False
    cover_url: str = ""  # the album's largest image (added 2026-10-05)


_app_token: dict[str, float | str] = {}


def app_token() -> str:
    """The app's token from Settings ▸ Traktor ▸ Spotify, reused until
    it expires; SpotifyError when no key is set."""
    from core import spotify_key
    from core.secrets import SPOTIFY_SECRET, get_secret

    from .spotify import api_token

    if _app_token and float(_app_token["until"]) > time.time() + 60:
        return str(_app_token["token"])
    client, secret = spotify_key.client_id(), get_secret(SPOTIFY_SECRET)
    if not client or not secret:
        raise SpotifyError("no Spotify API key (Settings ▸ Traktor ▸ Spotify)")
    _app_token.update(token=api_token(client, secret), until=time.time() + 3600)
    return str(_app_token["token"])


_RIGHTS_FIRST = re.compile(r"^all\s+rights\s+reserved\s*(?:to|by|for)?\s*", re.IGNORECASE)
_LICENSE = re.compile(r"\b(?:under|exclusive|licen[cs]e|distributed)\b", re.IGNORECASE)
#: A catalogue number that landed in the copyright line: "AKIVV011". Three
#: digits at least, so a short label name like "ISH11" is kept.
_CATNO = re.compile(r"^[A-Z]{3,}\d{3,}$")
_ALL_RIGHTS = re.compile(r"[\s.,;·-]*\ball\s+rights\b.*$", re.IGNORECASE | re.DOTALL)
_TRAILING_YEAR = re.compile(r"[\s,]+(?:19|20)\d{2}$")
#: Names a label-services company generates for a self-distributing artist.
_AUTO_LABEL = re.compile(r"^\d{5,}(?:\s+Records?(?:\s+DK)?)?$", re.IGNORECASE)


def clean_label(text: str) -> str:
    """A label from a copyright line's text, or "" when it names no label.

    Takes off the year and the ℗/© marks, "All rights reserved", and anything
    from "under exclusive licence to…" on. Then refuses what is not an imprint:
    a distributor, a generated name ("2429111 Records DK"), a catalogue
    number (rules shared with core.track_meta.canonical_label, except that
    catalogue numbers are only recognised here, where they are known to
    turn up)."""
    from core.track_meta import canonical_label

    text = re.sub(r"^(?:\s|℗|©|\([PpCc]\))*(?:\d{4}\b)?[\s,]*", "", text or "").strip()
    text = _RIGHTS_FIRST.sub("", text)
    text = _ALL_RIGHTS.sub("", text)  # "Frenzy Recordings 2024. All rights of the producer…"
    text = _LICENSE.split(text, maxsplit=1)[0].strip(" ,;-–")
    text = _TRAILING_YEAR.sub("", text).strip(" ,;-–")
    if not text or _CATNO.match(text) or _AUTO_LABEL.match(text):
        return ""
    return text if canonical_label(text) else ""


def label_of(copyrights: list[dict]) -> str:
    """The label from an album's ℗ line ("℗ 2017 Ninja Tune" → "Ninja Tune").

    The user's app (made 2026-10-04) gets no ``label`` on albums, no genres
    on artists, and 403 on /artists?ids=; the copyright lines are left."""
    lines = sorted(copyrights, key=lambda c: c.get("type") != "P")  # ℗ first
    for c in lines:
        text = clean_label(c.get("text", ""))
        if text:
            return text
    return ""


def lookup_track(
    artist: str, title: str, duration_s: float, *, before_request=None
) -> TrackDetails | None:
    """Find a track on Spotify and say what Spotify knows about it; None when
    nothing is found. 1-8 searches (search_track), then the track and its
    album. *before_request* (``() -> bool``), if given, is called right
    before each of those requests: True goes on, False raises
    ``SpotifyError("stopped")`` — the caller's pacer bounds the requests,
    not the tracks. The app token (fetched about once an hour) is not
    counted."""
    token = app_token()
    match = search_track(token, artist, title, duration_s, before=before_request)
    if match is None:
        return None
    track_id = match.uri.rsplit(":", 1)[-1]
    _gate(before_request)
    track = _call("GET", f"{API}/tracks/{track_id}", token)
    album = track.get("album") or {}
    full = {}
    if album.get("id"):
        _gate(before_request)
        full = _call("GET", f"{API}/albums/{album['id']}", token)
    return TrackDetails(
        spotify_id=track_id,
        artist=match.artist,
        title=match.title,
        album=album.get("name", ""),
        release_date=album.get("release_date", ""),
        track_number=track.get("track_number"),
        duration_s=match.duration_s,
        label=clean_label(full.get("label") or "") or label_of(full.get("copyrights") or []),
        isrc=(track.get("external_ids") or {}).get("isrc", ""),
        genres=tuple(full.get("genres") or ()),
        sure=match.sure,
        cover_url=((full.get("images") or album.get("images") or [{}])[0] or {}).get("url", ""),
    )
