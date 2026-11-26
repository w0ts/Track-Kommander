"""Discogs lookups kept in our database (the ``track_discogs`` table).

One row per track that has been looked up:

``matched``  a release was accepted — automatically, above the confidence
             threshold, or by the user from the review list;
``review``   candidates were found but none was sure enough; kept for review;
``none``     Discogs had nothing for the artist and title;
``skipped``  the user said none of the candidates is the track;
``error``    the lookup failed (network, refused token): retried next run.

Only ``matched`` rows are ever written to Traktor.
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from core.db import get_conn
from core.discogs_fields import DiscogsRecord
from core.discogs_match import Query

Status = Literal["matched", "review", "none", "skipped", "spotify", "error"]

#: Statuses a lookup run leaves alone: there is already an answer. Errors are
#: retried; "review" is not — the candidates are stored, the user decides.
SETTLED: tuple[Status, ...] = ("matched", "review", "none", "skipped", "spotify")


@dataclass(frozen=True)
class Candidate:
    """A release offered for review: enough to recognise it in a list."""

    release_id: int
    title: str  # "Artist - Release"
    year: int | None
    label: str
    catno: str
    format: str
    score: float
    country: str = ""
    master_id: int | None = None


@dataclass(frozen=True)
class Lookup:
    path: str
    status: Status
    confidence: float | None = None
    record: dict[str, Any] | None = None  # the accepted release, see record_fields
    candidates: tuple[Candidate, ...] = ()
    error: str = ""
    looked_up_at: float = field(default_factory=time.time)


def record_fields(record: DiscogsRecord, **extra: Any) -> dict[str, Any]:
    """What a ``matched`` row stores: the writable fields plus display extras
    (master id, track position, format, community rating, cover URL…)."""
    data = asdict(record)
    data["genres"] = list(record.genres)
    data["styles"] = list(record.styles)
    data.update(extra)
    return data


def to_record(data: Mapping[str, Any]) -> DiscogsRecord:
    def _int(v: Any) -> int | None:
        try:
            return int(v) if v not in (None, "") else None
        except (TypeError, ValueError):
            return None

    return DiscogsRecord(
        release_id=_int(data.get("release_id")) or 0,
        genres=tuple(str(g) for g in data.get("genres") or ()),
        styles=tuple(str(s) for s in data.get("styles") or ()),
        label=str(data.get("label") or ""),
        catno=str(data.get("catno") or ""),
        country=str(data.get("country") or ""),
        released=str(data.get("released") or ""),
        year=_int(data.get("year")),
        original_year=_int(data.get("original_year")),
    )


def save(lookup: Lookup) -> None:
    with get_conn() as c:
        c.execute(
            "INSERT INTO track_discogs "
            "(path, status, confidence, release_id, record, candidates, error, looked_up_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(path) DO UPDATE SET status=excluded.status, "
            "confidence=excluded.confidence, release_id=excluded.release_id, "
            "record=excluded.record, candidates=excluded.candidates, "
            "error=excluded.error, looked_up_at=excluded.looked_up_at",
            (
                lookup.path,
                lookup.status,
                lookup.confidence,
                (lookup.record or {}).get("release_id"),
                json.dumps(lookup.record) if lookup.record is not None else None,
                json.dumps([asdict(c) for c in lookup.candidates]),
                lookup.error or None,
                lookup.looked_up_at,
            ),
        )
        c.commit()


def _from_row(r) -> Lookup:
    cands = tuple(Candidate(**c) for c in json.loads(r["candidates"] or "[]"))
    return Lookup(
        path=r["path"],
        status=r["status"],
        confidence=r["confidence"],
        record=json.loads(r["record"]) if r["record"] else None,
        candidates=cands,
        error=r["error"] or "",
        looked_up_at=r["looked_up_at"],
    )


def get(path: str) -> Lookup | None:
    with get_conn() as c:
        r = c.execute("SELECT * FROM track_discogs WHERE path = ?", (path,)).fetchone()
    return _from_row(r) if r else None


def by_status(status: Status) -> list[Lookup]:
    with get_conn() as c:
        rows = c.execute(
            "SELECT * FROM track_discogs WHERE status = ? ORDER BY path", (status,)
        ).fetchall()
    return [_from_row(r) for r in rows]


def settled_paths() -> set[str]:
    """Tracks a lookup run skips: they already have an answer."""
    marks = ",".join("?" * len(SETTLED))
    with get_conn() as c:
        rows = c.execute(
            f"SELECT path FROM track_discogs WHERE status IN ({marks})", SETTLED
        ).fetchall()
    return {r["path"] for r in rows}


def spotify_owed() -> set[str]:
    """Tracks Discogs could not settle (not found, or left for review) that
    Spotify has not been asked about, or not yet answered completely: what a
    Spotify run still owes. Misses Spotify already gave up on are not here."""
    from core import spotify_store

    with get_conn() as c:
        paths = [
            r["path"]
            for r in c.execute("SELECT path FROM track_discogs WHERE status IN ('none', 'review')")
        ]
    return {p for p in paths if spotify_store.should_ask(p)}


def pending(again: bool = False, spotify: bool = False) -> list[tuple[str, Query]]:
    """Tracks a lookup run should ask Discogs about: in the library, with an
    artist and a title. Artist order, so the progress line reads like the
    browser; tracks that failed before go last.

    By default only tracks without an answer — never looked up, or failed.
    With *again*, every track, except a release the user picked by hand in
    the review list: a run that asks again must not undo that choice.
    With *spotify* (a Spotify key is set), a track Discogs could not settle
    is also taken while Spotify still owes it an answer, so a run that was
    cut short by Spotify's per-run limit is finished by the next one.
    """
    with get_conn() as c:
        failed = {
            r["path"] for r in c.execute("SELECT path FROM track_discogs WHERE status = 'error'")
        }
        picked = {
            r["path"]
            for r in c.execute(
                "SELECT path FROM track_discogs "
                "WHERE status = 'matched' AND json_extract(record, '$.picked') = 1"
            )
        }
        rows = c.execute(
            "SELECT path, artist, title, album, duration FROM tracks "
            "ORDER BY artist COLLATE NOCASE, title COLLATE NOCASE"
        ).fetchall()
    skip = picked if again else settled_paths() - (spotify_owed() if spotify else set())
    spotify_names = _spotify_queries()
    todo = []
    for r in rows:
        if r["path"] in skip:
            continue
        # A playlist track is asked with Spotify's names — and is asked even
        # when the downloaded file's own tags are empty.
        query = spotify_names.get(r["path"])
        if query is None and r["artist"] and r["title"]:
            query = Query(r["artist"], r["title"], r["album"] or "", r["duration"])
        if query is not None:
            todo.append((r["path"], query))
    # Fresh tracks first, earlier failures after: a run is never stuck behind
    # the same few tracks that failed last time.
    return sorted(todo, key=lambda pq: pq[0] in failed)


def _spotify_query(path: str) -> Query | None:
    """Spotify's artist/title/album/length for a playlist track, when known —
    cleaner than a fresh download's tags (core.spotify_store)."""
    from core import spotify_store

    try:
        return spotify_store.query_for(path)
    except sqlite3.OperationalError:  # a database from before the table: the tags will do
        return None


def _spotify_queries() -> dict[str, Query]:
    from core import spotify_store

    try:
        return spotify_store.queries()
    except sqlite3.OperationalError:
        return {}


def query_for(path: str) -> Query | None:
    """The track's artist, title, album and length, for a lookup by hand —
    Spotify's when the track came from a playlist."""
    if (spotify := _spotify_query(path)) is not None:
        return spotify
    with get_conn() as c:
        r = c.execute(
            "SELECT artist, title, album, duration FROM tracks WHERE path = ?", (path,)
        ).fetchone()
    if r is None:
        return None
    return Query(r["artist"] or "", r["title"] or "", r["album"] or "", r["duration"])


def counts() -> dict[str, int]:
    with get_conn() as c:
        rows = c.execute("SELECT status, COUNT(*) AS n FROM track_discogs GROUP BY status")
        return {r["status"]: r["n"] for r in rows}


def review_paths() -> set[str]:
    """Tracks the review shows: Discogs' doubtful matches, and the tracks
    Spotify found but was not sure of (and Discogs has not matched)."""
    from core import spotify_store

    with get_conn() as c:
        discogs = {
            r["path"] for r in c.execute("SELECT path FROM track_discogs WHERE status = 'review'")
        }
        matched = {
            r["path"] for r in c.execute("SELECT path FROM track_discogs WHERE status = 'matched'")
        }
    try:
        guesses = set(spotify_store.unsure_guesses())
    except sqlite3.OperationalError:  # a database from before the table
        guesses = set()
    return discogs | (guesses - matched)


def matched_records(paths: Iterable[str] | None = None) -> dict[str, DiscogsRecord]:
    """``{path: record}`` for every accepted match (or those among *paths*)."""
    wanted = set(paths) if paths is not None else None
    out: dict[str, DiscogsRecord] = {}
    for lookup in by_status("matched"):
        if lookup.record and (wanted is None or lookup.path in wanted):
            out[lookup.path] = to_record(lookup.record)
    return out


def skip(path: str) -> None:
    """The user said none of the candidates is this track."""
    current = get(path)
    if current is not None:
        save(Lookup(path, "skipped", current.confidence, None, current.candidates))


def forget(paths: Iterable[str]) -> None:
    """Drop lookups so the next run asks Discogs again."""
    with get_conn() as c:
        c.executemany("DELETE FROM track_discogs WHERE path = ?", [(p,) for p in paths])
        c.commit()
