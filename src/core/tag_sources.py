"""One record per track from every metadata source, for filling Traktor.

The user's rule: take each value from whichever source has it — Discogs,
then Spotify — except genre and style, whose sources and order the user
picks (``GENRE_SOURCES_KEY``). Spotify's label is read from the album's ℗
line, so it only fills in when Discogs has none; its genres are the artists'
and, for an app without extended API access, always empty — a Spotify-only
genre setting fills nothing today.

The merged record is a :class:`core.discogs_fields.DiscogsRecord` whose
``origin`` names each value's source, so the fill and its preview work
unchanged and say where every value came from.
"""

from __future__ import annotations

from collections.abc import Mapping

from core.discogs_fields import DiscogsRecord
from core.spotify_store import SpotifyInfo

GENRE_SOURCES_KEY = "tagging/genre_sources"
#: Settings value → the sources tried for genre and style, in order.
GENRE_SOURCES: dict[str, tuple[str, ...]] = {
    "discogs,spotify": ("discogs", "spotify"),
    "spotify,discogs": ("spotify", "discogs"),
    "discogs": ("discogs",),
    "spotify": ("spotify",),
}
GENRE_SOURCE_LABELS: dict[str, str] = {
    "discogs,spotify": "Discogs, then Spotify",
    "spotify,discogs": "Spotify, then Discogs",
    "discogs": "Discogs only",
    "spotify": "Spotify only",
}
DEFAULT_GENRE_SOURCES = "discogs,spotify"


def _spotify_year(info: SpotifyInfo) -> int | None:
    try:
        return int(info.release_date[:4]) if info.release_date else None
    except ValueError:
        return None


def merge(
    discogs: DiscogsRecord | None,
    spotify: SpotifyInfo | None,
    genre_order: tuple[str, ...] = GENRE_SOURCES[DEFAULT_GENRE_SOURCES],
) -> DiscogsRecord | None:
    """One track's values, each from the first source that has it."""
    if discogs is None and spotify is None:
        return None
    d = discogs or DiscogsRecord(release_id=0)
    origin: dict[str, str] = {}

    genres: tuple[str, ...] = ()
    styles: tuple[str, ...] = ()
    for source in genre_order:
        if source == "discogs" and discogs is not None and (d.genres or d.styles):
            genres, styles = d.genres, d.styles
            break
        if source == "spotify" and spotify is not None and spotify.genres:
            genres, styles = spotify.genres, ()
            origin.update(genres="Spotify", styles="Spotify")
            break

    label = d.label
    if not label and spotify is not None and spotify.label:
        label = spotify.label
        origin["label"] = "Spotify"

    released, year, original_year = d.released, d.year, d.original_year
    if not (released or year or original_year) and spotify is not None and spotify.release_date:
        released, year, original_year = spotify.release_date, _spotify_year(spotify), None
        origin["year"] = "Spotify"

    return DiscogsRecord(
        release_id=d.release_id,
        genres=genres,
        styles=styles,
        label=label,
        catno=d.catno,
        country=d.country,
        released=released,
        year=year,
        original_year=original_year,
        origin=tuple(origin.items()),
    )


def merged_records(
    discogs: Mapping[str, DiscogsRecord],
    spotify: Mapping[str, SpotifyInfo],
    genre_sources: str = DEFAULT_GENRE_SOURCES,
) -> dict[str, DiscogsRecord]:
    """``{path: record}`` for every track either source knows."""
    order = GENRE_SOURCES.get(genre_sources, GENRE_SOURCES[DEFAULT_GENRE_SOURCES])
    out = {}
    for path in {*discogs, *spotify}:
        record = merge(discogs.get(path), spotify.get(path), order)
        if record is not None:
            out[path] = record
    return out
