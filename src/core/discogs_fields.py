"""Which Discogs value goes into which Traktor field, and in Traktor's format.

A Discogs match is kept whole in our database (:mod:`core.discogs_store`).
Writing it into ``collection.nml`` is a separate, explicit step that only
fills fields the user mapped, and only where Traktor's field is empty — or,
for the release date, a ``YYYY/1/1`` placeholder when the user asks for that.

Qt-free: the settings page stores a ``{target: source}`` mapping and this
module turns a stored match into the values to write.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date

# ── what a stored match holds ────────────────────────────────────────────────


@dataclass(frozen=True)
class DiscogsRecord:
    """The fields of one accepted match that can be written to Traktor."""

    release_id: int
    genres: tuple[str, ...] = ()
    styles: tuple[str, ...] = ()
    label: str = ""
    catno: str = ""
    country: str = ""
    released: str = ""  # Discogs' own form: "1987-07-00", "1998", ""
    year: int | None = None  # this release's year
    original_year: int | None = None  # earliest year of its master release
    #: Which source each field came from, when the record merges several
    #: (core.tag_sources): ``(("label", "Spotify"), …)``. Empty: all Discogs.
    origin: tuple[tuple[str, str], ...] = ()

    def source_of(self, attr: str) -> str:
        return dict(self.origin).get(attr, "Discogs")


# ── Traktor's fields ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TraktorField:
    key: str  # settings id
    label: str  # as Traktor's browser names the column
    attr: str  # attribute on the NML entry's INFO element


#: The INFO attributes a mapping can fill. "Comment 2" is stored by Traktor
#: as INFO RATING — the user's collection holds free text there.
TRAKTOR_FIELDS: tuple[TraktorField, ...] = (
    TraktorField("genre", "Genre", "GENRE"),
    TraktorField("comment", "Comment", "COMMENT"),
    TraktorField("comment2", "Comment 2", "RATING"),
    TraktorField("label", "Label", "LABEL"),
    TraktorField("catalog_no", "Catalog No", "CATALOG_NO"),
    TraktorField("release_date", "Release Date", "RELEASE_DATE"),
)
TRAKTOR_BY_KEY: dict[str, TraktorField] = {f.key: f for f in TRAKTOR_FIELDS}


# ── Discogs values ───────────────────────────────────────────────────────────


def _first(xs: tuple[str, ...]) -> str:
    return xs[0] if xs else ""


def _joined(xs: tuple[str, ...]) -> str:
    return ", ".join(xs)


def _genre_style(r: DiscogsRecord) -> str:
    parts = [p for p in (_first(r.genres), _first(r.styles)) if p]
    return " / ".join(parts)


@dataclass(frozen=True)
class DiscogsSource:
    key: str
    label: str
    value: Callable[[DiscogsRecord], str]
    #: Only offered for the release-date field, and only there: a date is not
    #: a genre, and a genre is not a date.
    is_date: bool = False


DISCOGS_SOURCES: tuple[DiscogsSource, ...] = (
    DiscogsSource("style", "First style (Deep House)", lambda r: _first(r.styles)),
    DiscogsSource("styles", "All styles (Deep House, Minimal)", lambda r: _joined(r.styles)),
    DiscogsSource("genre", "First genre (Electronic)", lambda r: _first(r.genres)),
    DiscogsSource("genres", "All genres (Electronic, Pop)", lambda r: _joined(r.genres)),
    DiscogsSource("genre_style", "Genre / style (Electronic / Deep House)", _genre_style),
    DiscogsSource("label", "Label", lambda r: r.label),
    DiscogsSource("catno", "Catalog number", lambda r: r.catno),
    DiscogsSource("country", "Country", lambda r: r.country),
    DiscogsSource(
        "original_date", "Original release (earliest pressing)", lambda r: original_date(r), True
    ),
    DiscogsSource(
        "release_date", "This release's date", lambda r: traktor_date(r.released, r.year), True
    ),
)
SOURCE_BY_KEY: dict[str, DiscogsSource] = {s.key: s for s in DISCOGS_SOURCES}

#: Out of the box: the fields most libraries miss, filled with the values a
#: DJ expects there. Comments stay the user's until they map them.
DEFAULT_MAPPING: dict[str, str] = {
    "genre": "style",
    "comment": "",
    "comment2": "",
    "label": "label",
    "catalog_no": "catno",
    "release_date": "original_date",
}


#: QSettings keys. The mapping is one key per Traktor field; "" means off.
MAPPING_PREFIX = "discogs/map/"
PLACEHOLDER_KEY = "discogs/replace_placeholder_dates"
#: Fetch every release for its exact date (one more request per track);
#: off, a match the search decides alone carries the year only.
EXACT_DATES_KEY = "discogs/exact_dates"
#: Preferred region (a key of core.discogs_match.REGIONS) for records
#: released per country; Europe out of the box.
REGION_KEY = "discogs/region"


def mapping_key(field_key: str) -> str:
    return MAPPING_PREFIX + field_key


def read_mapping(settings) -> dict[str, str]:
    """``{traktor field: discogs source}`` from *settings* (a QSettings, or
    anything with its ``value(key, default)``), defaults filled in."""
    out = {}
    for f in TRAKTOR_FIELDS:
        value = str(settings.value(mapping_key(f.key), DEFAULT_MAPPING[f.key]) or "")
        out[f.key] = value if value in SOURCE_BY_KEY else ""
    return out


def sources_for(field_key: str) -> tuple[DiscogsSource, ...]:
    """The Discogs values that make sense in one Traktor field."""
    want_date = field_key == "release_date"
    return tuple(s for s in DISCOGS_SOURCES if s.is_date == want_date)


# ── dates ─────────────────────────────────────────────────────────────────────

_DISCOGS_DATE = re.compile(r"^(\d{4})(?:-(\d{1,2}))?(?:-(\d{1,2}))?")


def traktor_date(released: str, year: int | None = None) -> str:
    """A Discogs date in Traktor's ``YYYY/M/D`` form, or "" when unknown.

    Discogs writes unknown parts as ``00`` ("1987-07-00"); Traktor needs a
    whole date, so an unknown month or day becomes 1 — the same convention
    Traktor itself uses for a year-only tag.
    """
    m = _DISCOGS_DATE.match(released or "")
    if m:
        y, mo, d = int(m[1]), int(m[2] or 0), int(m[3] or 0)
    elif year:
        y, mo, d = year, 0, 0
    else:
        return ""
    if not 1000 <= y <= 9999:
        return ""
    if not 1 <= mo <= 12:
        mo, d = 1, 1  # no month: a day on its own means nothing
    try:
        date(y, mo, d)
    except ValueError:
        d = 1  # no such day (Feb 31): keep the month, drop the day
    return f"{y}/{mo}/{d}"


def original_date(r: DiscogsRecord) -> str:
    """The first-release date: this release's exact date when it *is* the
    first pressing, else the master's year (day unknown, so Jan 1)."""
    if r.original_year and (r.year is None or r.original_year < r.year):
        return f"{r.original_year}/1/1"
    return traktor_date(r.released, r.year)


_PLACEHOLDER = re.compile(r"^\d{4}/0?1/0?1$")


def is_placeholder_date(value: str) -> bool:
    """``2024/1/1``: a year with no real month or day, as most taggers write it."""
    return bool(_PLACEHOLDER.match(value.strip()))


# ── what to write ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class FieldChange:
    field: TraktorField
    old: str
    new: str
    source: str = "Discogs"  # where the new value came from


#: The record attribute each Discogs value is read from — for its source.
_SOURCE_ATTR = {
    "style": "styles",
    "styles": "styles",
    "genre": "genres",
    "genres": "genres",
    "genre_style": "genres",
    "label": "label",
    "catno": "catno",
    "country": "country",
    "original_date": "year",
    "release_date": "year",
}


def planned_changes(
    record: DiscogsRecord,
    current: Mapping[str, str],
    mapping: Mapping[str, str],
    replace_placeholder_dates: bool = False,
) -> list[FieldChange]:
    """The fields this match would fill for one track.

    *current* is the track's INFO attributes (``{"GENRE": "…", …}``). A field
    is filled only when it is empty — never overwritten — except a
    placeholder release date when *replace_placeholder_dates* is set. A
    mapped value that would be empty, or equal to what is there, is skipped.
    """
    changes: list[FieldChange] = []
    for field in TRAKTOR_FIELDS:
        source = SOURCE_BY_KEY.get(mapping.get(field.key, ""))
        if source is None or source not in sources_for(field.key):
            continue
        new = source.value(record).strip()
        old = (current.get(field.attr) or "").strip()
        if not new or new == old:
            continue
        replaceable = (
            field.key == "release_date"
            and replace_placeholder_dates
            and is_placeholder_date(old)
            # Only by a real date: one January 1st for another is a guess
            # replacing a guess (a master's year, day unknown).
            and not is_placeholder_date(new)
        )
        if old and not replaceable:
            continue
        source = record.source_of(_SOURCE_ATTR.get(source.key, ""))
        changes.append(FieldChange(field, old, new, source))
    return changes
