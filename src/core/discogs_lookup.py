"""Look one track up on Discogs: search, confirm on a tracklist, decide.

One request when the search by artist and title is conclusive — every
strong result is the same record. Otherwise the best-ranked release is
fetched to find the track on it (and the runner-up when that one is not
convincing). Asked for exact dates, the release is always fetched.
The result is a :class:`core.discogs_store.Lookup` ready to save.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from core.discogs_api import DiscogsClient, DiscogsError, DiscogsStopped
from core.discogs_fields import DiscogsRecord
from core.discogs_match import (
    DEFAULT_REGION,
    PLAUSIBLE_ARTIST,
    Query,
    ReleaseMatch,
    norm_artist,
    norm_title,
    original_year,
    rank_candidates,
    release_preference,
    result_artist_score,
    score_release,
    search_title,
    similarity,
)
from core.discogs_store import Candidate, Lookup, record_fields

#: Releases fetched per track at most. The second only when the first fails.
MAX_RELEASES = 2
#: Candidates kept for the review list.
KEEP_CANDIDATES = 6
_DISCOGS_NUMBER = re.compile(r"\s+\(\d+\)$")


def _permanent(exc: DiscogsError) -> bool:
    """A 4xx other than a refused token or the rate limit: not worth retrying."""
    return 400 <= exc.status < 500 and exc.status not in (401, 429)


def _int(v: Any) -> int | None:
    try:
        return int(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _candidate(score: float, r: Mapping) -> Candidate:
    labels = r.get("label") or []
    formats = r.get("format") or []
    return Candidate(
        release_id=_int(r.get("id")) or 0,
        title=str(r.get("title", "")),
        year=_int(r.get("year")),
        label=str(labels[0]) if labels else "",
        catno="" if str(r.get("catno", "")).lower() == "none" else str(r.get("catno", "")),
        format=", ".join(str(f) for f in formats[:3]),
        score=round(score, 3),
        country=str(r.get("country") or ""),
        master_id=_int(r.get("master_id")),
    )


def build_record(release: Mapping, results: Sequence[Mapping], match: ReleaseMatch | None) -> dict:
    """The stored fields of an accepted release."""
    labels = release.get("labels") or []
    label = labels[0] if labels else {}
    catno = str(label.get("catno", ""))
    year = _int(release.get("year")) or None
    master_id = _int(release.get("master_id"))
    images = release.get("images") or []
    community = release.get("community") or {}
    rating = community.get("rating") or {}
    record = DiscogsRecord(
        release_id=_int(release.get("id")) or 0,
        genres=tuple(str(g) for g in release.get("genres") or ()),
        styles=tuple(str(s) for s in release.get("styles") or ()),
        label=_DISCOGS_NUMBER.sub("", str(label.get("name", ""))),
        catno="" if catno.lower() == "none" else catno,
        country=str(release.get("country") or ""),
        released=str(release.get("released") or ""),
        year=year,
        original_year=original_year(year, master_id, results),
    )
    return record_fields(
        record,
        master_id=master_id,
        title=str(release.get("title", "")),
        formats=[str(f.get("name", "")) for f in release.get("formats") or []],
        position=match.position if match else "",
        track_title=match.track_title if match else "",
        rating=rating.get("average"),
        votes=rating.get("count"),
        have=community.get("have"),
        want=community.get("want"),
        cover=str(images[0].get("uri150") or images[0].get("uri") or "") if images else "",
        uri=str(release.get("uri") or ""),
    )


#: A search result's artist must score this for the search alone to decide.
QUICK_ARTIST = 0.9


def _has_strong(query: Query, results: Sequence[Mapping]) -> bool:
    """Some result is by this artist (the release's own artist) — judged on
    the artist alone: a release named unlike Traktor's album still counts."""
    return any(result_artist_score(query, r) >= QUICK_ARTIST for r in results)


def conclusive(
    query: Query, ranked: Sequence[tuple[float, Mapping]], region: str = DEFAULT_REGION
) -> Mapping | None:
    """The search result to keep without fetching its release, or None.

    The search was for this artist *and* this title, so Discogs already
    matched the track. When every strong result is the same record — one
    master, however many pressings — the release-level fields (genre, style,
    label, year) are the same whichever pressing it is, and fetching the
    release would add only the exact date and the track's position. Of its
    pressings the preferred one is kept (:func:`release_preference`).
    """
    strong = [r for _s, r in ranked if result_artist_score(query, r) >= QUICK_ARTIST]
    if not strong:
        return None
    records = {r.get("master_id") or f"release:{r.get('id')}" for r in strong}
    if len(records) != 1:
        return None
    return min(strong, key=lambda r: release_preference(r, region))  # first of equals


def record_from_search(result: Mapping, results: Sequence[Mapping]) -> dict:
    """The stored fields of a match decided by the search alone. The date is
    the year only; ``verified`` is False — no tracklist was read."""
    labels = result.get("label") or []
    catno = str(result.get("catno", ""))
    year = _int(result.get("year"))
    master_id = _int(result.get("master_id"))
    _artist, _sep, title = str(result.get("title", "")).partition(" - ")
    record = DiscogsRecord(
        release_id=_int(result.get("id")) or 0,
        genres=tuple(str(g) for g in result.get("genre") or ()),
        styles=tuple(str(s) for s in result.get("style") or ()),
        label=_DISCOGS_NUMBER.sub("", str(labels[0])) if labels else "",
        catno="" if catno.lower() == "none" else catno,
        country=str(result.get("country") or ""),
        released=str(year) if year else "",
        year=year,
        original_year=original_year(year, master_id, results),
    )
    return record_fields(
        record,
        master_id=master_id,
        title=title or str(result.get("title", "")),
        formats=[str(f) for f in result.get("format") or []][:3],
        cover=str(result.get("thumb") or ""),
        uri=str(result.get("uri") or ""),
        verified=False,
    )


def lookup(
    client: DiscogsClient,
    path: str,
    query: Query,
    exact: bool = False,
    region: str = DEFAULT_REGION,
) -> Lookup:
    """Look *query* up and say what was found. Network failures and refusals
    come back as an ``error`` lookup, never raised — except Stop.

    One request when the search for artist and title is conclusive (see
    :func:`conclusive`); otherwise the best releases are fetched to find the
    track on their tracklists. *exact* always fetches, for the exact date.
    """
    try:
        results = client.search(artist=query.artist, track=search_title(query.title))
        if results and not exact:
            ranked = rank_candidates(query, results, region)
            quick = conclusive(query, ranked, region)
            if quick is not None:
                candidates = tuple(_candidate(s, r) for s, r in ranked[:KEEP_CANDIDATES])
                record = record_from_search(quick, results)
                confidence = result_artist_score(query, quick)
                return Lookup(path, "matched", confidence, record, candidates)
        if query.album and not _has_strong(query, results):
            # Discogs' artist filter matches the *release's* artist, so a
            # track that is only on a compilation ("Various") is never found
            # by it; the album Traktor knows finds the compilation, and its
            # tracklist names the track's own artist.
            on_album = client.search(release_title=query.album, track=search_title(query.title))
            album_ids = {r.get("id") for r in on_album}
            results = [*on_album, *(r for r in results if r.get("id") not in album_ids)]
        if not results:
            results = client.search(q=f"{query.artist} {search_title(query.title)}")
        if not results:
            return Lookup(path, "none")
        ranked = rank_candidates(query, results, region)
        best: tuple[ReleaseMatch, dict] | None = None
        has_track: set[int] = set()  # fetched releases with a close title on them
        for _score, result in ranked[:MAX_RELEASES]:
            release_id = _int(result.get("id"))
            if not release_id:
                continue
            release = client.release(release_id)
            match = score_release(query, release)
            if match.position:
                has_track.add(release_id)
            if best is None or match.confidence > best[0].confidence:
                best = (match, release)
            if match.accepted:
                break
    except DiscogsStopped:
        raise
    except DiscogsError as exc:
        if _permanent(exc):
            # A release that is gone, a query Discogs will never accept:
            # asking again next run would fail the same way.
            return Lookup(path, "none", error=str(exc))
        return Lookup(path, "error", error=str(exc))
    if best is not None and best[0].accepted:
        match, release = best
        record = {**build_record(release, results, match), "verified": True}
        candidates = tuple(_candidate(s, r) for s, r in ranked[:KEEP_CANDIDATES])
        return Lookup(path, "matched", match.confidence, record, candidates)
    # Review only what could be the record: by a resembling artist, or found
    # to carry a track of this title. The rest matched a word — "Sentimental"
    # the album, not Luis Miguel's — and is not worth anyone's time.
    plausible = [
        (s, r)
        for s, r in ranked
        if result_artist_score(query, r) >= PLAUSIBLE_ARTIST or _int(r.get("id")) in has_track
    ]
    # The same rule the review applies when it opens (prune_reviews), so the
    # count a run reports is the count the review shows.
    candidates = tuple(
        c
        for c in (_candidate(s, r) for s, r in plausible[:KEEP_CANDIDATES])
        if _plausible_stored(query, c)
    )
    if not candidates:
        return Lookup(path, "none")
    confidence = best[0].confidence if best is not None else None
    return Lookup(path, "review", confidence, None, candidates)


def _plausible_stored(query: Query, c: Candidate) -> bool:
    """A stored candidate's plausibility from what was kept of it: its
    artist, or — a compilation — its title being the track's album."""
    if result_artist_score(query, {"title": c.title}) >= PLAUSIBLE_ARTIST:
        return True
    artist, _sep, release_title = c.title.partition(" - ")
    if norm_artist(artist) != "various":
        return False  # a single named like the track is not the record
    album = norm_title(query.album)
    return bool(album) and similarity(album, norm_title(release_title)) >= 0.8


def prune_reviews() -> int:
    """Apply the plausibility rule to reviews stored before it existed.

    A track whose candidates are all by unrelated artists becomes "none"
    (not on Discogs); its implausible candidates are dropped from the rest.
    Returns how many tracks left the review list.
    """
    from core import discogs_store

    moved = 0
    for lookup_ in discogs_store.by_status("review"):
        query = discogs_store.query_for(lookup_.path)
        if query is None:
            continue
        keep = tuple(c for c in lookup_.candidates if _plausible_stored(query, c))
        if keep == lookup_.candidates:
            continue
        status = "review" if keep else "none"
        moved += status == "none"
        discogs_store.save(
            Lookup(lookup_.path, status, lookup_.confidence, None, keep, lookup_.error)
        )
    return moved


def accept(client: DiscogsClient, path: str, query: Query, candidate: Candidate) -> Lookup:
    """The user picked *candidate* from the review list: fetch it and keep it.

    The original year then comes from this release alone — the search
    results it would be compared with were not kept. Marked ``picked`` so a
    "look up again" run leaves the user's choice alone.
    """
    release = client.release(candidate.release_id)
    match = score_release(query, release)
    record = {**build_record(release, [], match), "picked": True}
    return Lookup(path, "matched", match.confidence, record, (candidate,))


@dataclass(frozen=True)
class TracklistRow:
    position: str
    artists: str  # the track's own artists, "" when they are the release's
    title: str
    duration: str
    is_match: bool  # the row the query's track was found as


def tracklist(query: Query, release: Mapping) -> list[TracklistRow]:
    """A release's tracklist for the review dialog, the matching row marked —
    what tells one pressing or compilation from another."""
    match = score_release(query, release)
    rows = []
    for track in release.get("tracklist") or []:
        if track.get("type_", "track") != "track":
            continue
        names = track.get("artists") or []
        rows.append(
            TracklistRow(
                position=str(track.get("position", "")),
                artists=", ".join(str(a.get("name", "")) for a in names),
                title=str(track.get("title", "")),
                duration=str(track.get("duration", "")),
                is_match=bool(match.position) and str(track.get("position", "")) == match.position,
            )
        )
    return rows
