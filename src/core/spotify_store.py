"""Spotify metadata for tracks that came from a Spotify playlist.

The Spotify playlists add-ons give (core.extensions) record, for each wanted track, the
library file that is it — one already there (``had``) or one downloaded for
it (``placed``) — with what Spotify's public pages said: artist, title,
length, album, release date. :func:`sync` copies that into the
``track_spotify`` table, keyed by path, so the rest of the app can use it
without reading job files: the matcher's release-year fallback
(core.track_meta) and the Discogs search (core.discogs_store.query_for).

No network: the jobs already hold it. No label or genre: Spotify's public
pages have neither (they need the developer API).
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Iterable
from dataclasses import asdict, dataclass

from core.db import get_conn
from core.discogs_match import Query
from core.spotify_api import TrackDetails, clean_label

log = logging.getLogger(__name__)


def sync(jobs: Iterable | None = None) -> list[str]:
    """Copy the playlist jobs' track metadata into ``track_spotify``.

    Returns the paths that are new to the table — the tracks just placed,
    which the caller may want looked up. *jobs* defaults to every playlist the add-ons give (core.extensions).
    """
    if jobs is None:
        from core.extensions import spotify_playlist_sources

        found: list = []
        for source in spotify_playlist_sources():  # an add-on's playlists
            try:
                found.extend(source())
            except Exception as exc:  # no playlist folder yet, or an unreadable one
                log.info("spotify metadata: a playlist source failed (%s)", exc)
        jobs = found
    now = time.time()
    rows = []
    for job in jobs:
        for t in job.tracks:
            path = t.placed or t.had
            if not path or not t.id:
                continue
            rows.append(
                (
                    os.path.normpath(path),
                    t.id,
                    t.artist,
                    t.title,
                    getattr(t, "album", "") or None,
                    getattr(t, "release_date", "") or None,
                    getattr(t, "track_number", "") or None,
                    t.duration_s or None,
                    job.name,
                    now,
                )
            )
    if not rows:
        return []
    with get_conn() as c:
        known = {r[0] for r in c.execute("SELECT path FROM track_spotify")}
        c.executemany(
            "INSERT INTO track_spotify (path, spotify_id, artist, title, album, release_date, "
            "track_number, duration_s, playlist, synced_at, source, sure) "
            "VALUES (?,?,?,?,?,?,?,?,?,?, 'playlist', 1) "
            "ON CONFLICT(path) DO UPDATE SET spotify_id=excluded.spotify_id, "
            "source='playlist', sure=1, "
            "artist=excluded.artist, title=excluded.title, "
            "album=COALESCE(excluded.album, album), "
            "release_date=COALESCE(excluded.release_date, release_date), "
            "track_number=COALESCE(excluded.track_number, track_number), "
            "duration_s=excluded.duration_s, playlist=excluded.playlist, "
            "synced_at=excluded.synced_at",
            rows,
        )
        c.commit()
    new = [r[0] for r in rows if r[0] not in known]
    log.info("spotify metadata: %d track(s), %d new", len(rows), len(new))
    return new


def query_for(path: str) -> Query | None:
    """The Discogs search for a playlist track, from Spotify's clean artist,
    title, album and length — better than a fresh download's tags."""
    with get_conn() as c:
        r = c.execute(
            "SELECT artist, title, album, duration_s FROM track_spotify WHERE path = ?",
            (os.path.normpath(path),),  # spelt as stored (sync, save_search)
        ).fetchone()
    if r is None or not r["artist"] or not r["title"]:
        return None
    return Query(r["artist"], r["title"], r["album"] or "", r["duration_s"])


def queries() -> dict[str, Query]:
    """:func:`query_for` for every playlist track at once — one read, for a
    lookup run over the whole library."""
    with get_conn() as c:
        rows = c.execute(
            "SELECT path, artist, title, album, duration_s FROM track_spotify "
            "WHERE COALESCE(artist, '') != '' AND COALESCE(title, '') != ''"
        ).fetchall()
    return {
        r["path"]: Query(r["artist"], r["title"], r["album"] or "", r["duration_s"]) for r in rows
    }


@dataclass(frozen=True)
class SpotifyInfo:
    """What Spotify said about one library track, for filling Traktor."""

    album: str
    release_date: str  # "YYYY-MM-DD", "YYYY-MM" or "YYYY"
    label: str  # from the album's ℗ line: less sure than a Discogs label
    genres: tuple[str, ...]  # the artists' — empty in practice
    sure: bool  # a playlist's own track, or a search whose length and names agreed
    source: str  # "playlist" or "search"


def has(path: str) -> bool:
    with get_conn() as c:
        return (
            c.execute(
                "SELECT 1 FROM track_spotify WHERE path = ?", (os.path.normpath(path),)
            ).fetchone()
            is not None
        )


def should_ask(path: str, again: bool = False) -> bool:
    """Whether the Spotify search should look for *path*.

    Not when its row already holds the album, the release date and the label
    (nothing left to find), and not when the search already tried it and
    found nothing — unless *again*, which retries those misses but never
    asks about a track that is complete (a "Look up again" over the whole
    library must not become thousands of Spotify searches). A playlist
    track's row exists from the start but holds only what the public page
    said, often no label and no date, so having a row is not enough to skip it.
    """
    key = os.path.normpath(path)
    with get_conn() as c:
        row = c.execute(
            "SELECT album, release_date, label FROM track_spotify WHERE path = ?", (key,)
        ).fetchone()
        if row is not None and row["album"] and row["release_date"] and row["label"]:
            return False
        if again:
            return True
        tried = c.execute("SELECT 1 FROM spotify_tried WHERE path = ?", (key,)).fetchone()
    return tried is None


def settles(path: str) -> bool:
    """Whether Spotify gave *path* something to fill Traktor with, from a
    match that is sure: a label or a release date. What lets a track that
    Discogs only half-matched skip the manual review."""
    got = infos().get(os.path.normpath(path))
    return got is not None and bool(got.label or got.release_date)


def note_tried(path: str, status: str, guess: str = "") -> None:
    """Remember that the search was asked about *path*: ok, no_match, no_label,
    unsure (found, but not sure it is the same recording; *guess* is what it
    found, as JSON) or rejected (the user said the guess is wrong)."""
    with get_conn() as c:
        c.execute(
            "INSERT INTO spotify_tried (path, status, tried_at, guess) VALUES (?,?,?,?) "
            "ON CONFLICT(path) DO UPDATE SET status=excluded.status, "
            "tried_at=excluded.tried_at, guess=excluded.guess",
            (os.path.normpath(path), status, time.time(), guess or None),
        )
        c.commit()


def unsure_guesses() -> dict[str, TrackDetails]:
    """``{path: TrackDetails}`` for tracks the search found but was not sure
    of, waiting for the user to say whether the guess is right (the review).
    Guesses saved before they were kept are rebuilt from the search's row."""
    out: dict[str, TrackDetails] = {}
    with get_conn() as c:
        rows = c.execute(
            "SELECT t.path, t.guess, s.spotify_id, s.artist, s.title, s.album, s.release_date, "
            "s.duration_s, s.label, s.isrc, s.source FROM spotify_tried t "
            "LEFT JOIN track_spotify s ON s.path = t.path WHERE t.status = 'unsure'"
        ).fetchall()
    for r in rows:
        try:
            if r["guess"]:
                data = json.loads(r["guess"])
                data["genres"] = tuple(data.get("genres") or ())
                out[r["path"]] = TrackDetails(**data)
            elif r["source"] == "search" and r["spotify_id"]:
                out[r["path"]] = TrackDetails(
                    spotify_id=r["spotify_id"],
                    artist=r["artist"] or "",
                    title=r["title"] or "",
                    album=r["album"] or "",
                    release_date=r["release_date"] or "",
                    track_number=None,
                    duration_s=r["duration_s"] or 0.0,
                    label=r["label"] or "",
                    isrc=r["isrc"] or "",
                )
        except (TypeError, ValueError):
            continue  # a guess saved in a shape this version does not know: skipped
    return out


def accept_guess(path: str) -> bool:
    """The user says Spotify's guess for *path* is the right track: keep it
    as a sure match. False when there is no guess to accept."""
    from dataclasses import replace

    guess = unsure_guesses().get(path)
    if guess is None:
        return False
    save_search(path, replace(guess, sure=True))
    return True


def reject_guess(path: str) -> None:
    """The user says Spotify's guess for *path* is wrong: not asked again."""
    note_tried(path, "rejected")


def forget_tried() -> int:
    """Forget every miss, so the next lookup run searches them again."""
    with get_conn() as c:
        n = c.execute("DELETE FROM spotify_tried").rowcount
        c.commit()
    return n


def save_search(path: str, details) -> None:
    """Keep what a Spotify search found for *path* (core.spotify_api.
    TrackDetails). A playlist's row is never replaced by a search's: only the
    fields it lacks are filled in, and only by a match that is sure."""
    key = os.path.normpath(path)
    with get_conn() as c:
        if details.sure:
            c.execute(
                "UPDATE track_spotify SET "
                "album = COALESCE(NULLIF(album, ''), ?), "
                "release_date = COALESCE(NULLIF(release_date, ''), ?), "
                "label = COALESCE(NULLIF(label, ''), ?), "
                "isrc = COALESCE(NULLIF(isrc, ''), ?) "
                "WHERE path = ? AND COALESCE(source, 'playlist') = 'playlist'",
                (
                    details.album or None,
                    details.release_date or None,
                    details.label or None,
                    details.isrc or None,
                    key,
                ),
            )
        c.execute(
            "INSERT INTO track_spotify (path, spotify_id, artist, title, album, release_date, "
            "track_number, duration_s, label, isrc, genres, sure, source, synced_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?, 'search', ?) "
            "ON CONFLICT(path) DO UPDATE SET spotify_id=excluded.spotify_id, "
            "album=excluded.album, release_date=excluded.release_date, "
            "track_number=excluded.track_number, label=excluded.label, isrc=excluded.isrc, "
            "genres=excluded.genres, sure=excluded.sure, synced_at=excluded.synced_at "
            "WHERE COALESCE(track_spotify.source, 'playlist') = 'search'",
            (
                key,
                details.spotify_id,
                details.artist,
                details.title,
                details.album or None,
                details.release_date or None,
                str(details.track_number) if details.track_number else None,
                details.duration_s or None,
                details.label or None,
                details.isrc or None,
                json.dumps(list(details.genres or ())),
                1 if details.sure else 0,
                time.time(),
            ),
        )
        c.commit()
    status = "ok" if details.sure and details.label else "no_label" if details.sure else "unsure"
    note_tried(path, status, json.dumps(asdict(details)) if status == "unsure" else "")


def infos() -> dict[str, SpotifyInfo]:
    """Every track's Spotify data that may fill Traktor: playlist rows, and
    searches that were sure."""
    with get_conn() as c:
        rows = c.execute(
            "SELECT path, album, release_date, label, genres, sure, source FROM track_spotify"
        ).fetchall()
    out = {}
    for r in rows:
        source = r["source"] or "playlist"
        sure = source == "playlist" or bool(r["sure"])
        try:
            genres = tuple(json.loads(r["genres"] or "[]"))
        except ValueError:
            genres = ()
        out[r["path"]] = SpotifyInfo(
            r["album"] or "",
            r["release_date"] or "",
            clean_label(r["label"] or ""),  # rows saved before the cleaning existed
            genres,
            sure,
            source,
        )
    return {p: i for p, i in out.items() if i.sure}
