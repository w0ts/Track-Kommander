"""A track's genre, label, year and import day, as the matcher should see them.

Traktor's free-text fields are noisy: download-site URLs in the genre and the
label, "Other" and "Unknown" as genres, a distributor where the imprint
should be, ``YYYY/1/1`` dates. Where the track has an accepted Discogs match
(:mod:`core.discogs_store`), its structured data is used instead; otherwise
the Traktor value, with the junk taken out so it counts as *no data* rather
than as the rarest, strongest match in the library.

Qt-free; the snapshot (:mod:`core.library_snapshot`) derives a column from
each rule, and the reference ranker in the tests calls the same functions.
"""

from __future__ import annotations

import json
import re
from datetime import date

# ── genre ─────────────────────────────────────────────────────────────────────

#: Genre values that mean "nobody filled this in".
_NULL_GENRES = frozenset(
    {"other", "others", "unknown", "misc", "miscellaneous", "various", "genre", "none",
     "n a", "na", "default", "undefined", "unclassifiable"}
)  # fmt: skip
#: A web address or a shop's name stuffed into a tag.
_URLISH = re.compile(
    r"https?://|www\.|\.(?:com|net|org|ru|info|io|fm|biz)\b|\bwww\b|\bhttps?\b", re.IGNORECASE
)


def clean_genre(genre: str | None) -> str:
    """The Traktor genre, or "" when it is junk: a URL, or a null marker."""
    text = (genre or "").strip()
    if not text or _URLISH.search(text):
        return ""
    folded = " ".join(re.findall(r"[^\W_]+", text.casefold()))
    return "" if folded in _NULL_GENRES else text


def _json_list(value) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [str(v) for v in value]
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return []
    return [str(v) for v in parsed] if isinstance(parsed, list) else []


def discogs_genre_text(genres, styles) -> str:
    """Discogs' genres and styles as one genre string ("Electronic, Techno,
    Minimal") — structured, so nothing in it is junk."""
    names = [*_json_list(genres), *_json_list(styles)]
    return ", ".join(dict.fromkeys(n for n in names if n))


def effective_genre(traktor_genre: str | None, discogs_genres=None, discogs_styles=None) -> str:
    """Discogs' when the track has a match, else the cleaned Traktor genre."""
    return discogs_genre_text(discogs_genres, discogs_styles) or clean_genre(traktor_genre)


# ── label ─────────────────────────────────────────────────────────────────────

#: A trailing word that names the company, not the imprint.
_LABEL_SUFFIX = re.compile(
    r"\s+(?:records?|recordings?|rec\.?|music|musik|ltd\.?|inc\.?|limited|label|digital|"
    r"productions?|audio)$",
    re.IGNORECASE,
)
_TRAILING_PAREN = re.compile(r"\s*[\(\[][^)\]]*[\)\]]\s*$")
#: Not an imprint: distributors, aggregators, pressing and mastering houses.
#: Whole names only — a bare "plant" would throw out M-Plant.
_NOT_A_LABEL = re.compile(
    r"^(?:not on label|self[- ]released|white label|unknown|none|n/?a|various|"
    r"believe|label ?worx|distrokid|tunecore|cd ?baby|awal|the orchard|"
    r"fuga|symphonic|ingrooves|amuse|routenote|spinnup|zebralution|finetunes|"
    r"rebeat|feiyr|iip-dds|dig ?dis|soundrop|revelator|kontor new media|"
    r"triple vision(?: music group)?|kompakt distribution|word and sound|"
    r"\d+ records dk|.*\bmastering\b.*|.*\bpressing\b.*|.*\bschallplattenfabrik\b.*|"
    r".*\bpressworks?\b.*)$",
    re.IGNORECASE,
)


def canonical_label(label: str | None) -> str:
    """A label reduced to what identifies it, or "" when it is not a label.

    "Planet Rhythm Records" and "Planet Rhythm" are one label; a URL, a
    distributor or a pressing plant is none. Compared exactly afterwards:
    "Mutual Rytm" and "Federation Of Rytm" share a word, not a label.
    """
    text = (label or "").strip()
    if not text or _URLISH.search(text):
        return ""
    text = re.sub(r"\s+\(\d+\)$", "", text)  # Discogs' "Planet Rhythm (2)"
    text = _TRAILING_PAREN.sub("", text).strip()
    if _NOT_A_LABEL.match(text):
        return ""
    for _ in range(2):  # "X Music Ltd"
        text = _LABEL_SUFFIX.sub("", text).strip()
    folded = " ".join(re.findall(r"[^\W_]+", text.casefold()))
    return folded


def effective_label(traktor_label: str | None, discogs_label: str | None = None) -> str:
    """Traktor's label when it is one, else the Discogs match's."""
    return canonical_label(traktor_label) or canonical_label(discogs_label)


# ── dates ─────────────────────────────────────────────────────────────────────

_TRAKTOR_DATE = re.compile(r"^\s*(\d{4})/(\d{1,2})/(\d{1,2})")


def _int(value) -> int | None:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if 1000 <= n <= 9999 else None


def effective_year(
    traktor_release_date: str | None,
    discogs_original_year=None,
    discogs_year=None,
    spotify_release_date: str | None = None,
) -> int | None:
    """When the record came out: Discogs' original year (the first pressing,
    not a reissue or the digital re-release), else its release year, else
    Spotify's release date, else the year of Traktor's release date."""
    for candidate in (discogs_original_year, discogs_year):
        if (year := _int(candidate)) is not None:
            return year
    if spotify_release_date and (year := _int(str(spotify_release_date)[:4])) is not None:
        return year
    m = _TRAKTOR_DATE.match(traktor_release_date or "")
    return _int(m[1]) if m else None


def import_day(traktor_import_date: str | None) -> int | None:
    """Traktor's import date as a day number (``date.toordinal``), or None."""
    m = _TRAKTOR_DATE.match(traktor_import_date or "")
    if not m:
        return None
    try:
        return date(int(m[1]), int(m[2]), int(m[3])).toordinal()
    except ValueError:
        return None
