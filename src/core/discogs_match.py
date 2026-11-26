"""Decide which Discogs release a Traktor track is on, and how sure we are.

Discogs describes *releases*; a track is found through a search for its
artist and title, then confirmed against a release's tracklist. Two calls per
track, scored here without any I/O:

1. :func:`rank_candidates` orders the search results by how well their
   artist (and album, when Traktor has one) agree with the track.
2. :func:`score_release` finds the track in the best candidate's tracklist —
   title, then length — and gives the match one confidence in [0, 1].

The original year comes free: the earliest year among the search results
that share the release's master. No third call for the master release.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher

#: At or above: accepted without asking. Below: kept for review.
ACCEPT_AT = 0.85
#: A candidate is worth reviewing only if its release artist is at least this
#: close to the track's — below, the search matched a word, not the record.
PLAUSIBLE_ARTIST = 0.6
#: Artist and title must each clear this on their own — a perfect title by
#: the wrong artist is a cover, not a match.
PART_FLOOR = 0.75

# Mix descriptors that name no different recording. Remixes keep their name:
# "(Floorplan Remix)" is another track.
_GENERIC_MIX = re.compile(
    r"[\(\[]\s*(original|extended|club|album|radio|single)?\s*"
    r"(mix|version|edit|remaster(ed)?(\s+\d{4})?)\s*[\)\]]",
    re.IGNORECASE,
)
_FEAT = re.compile(r"\s+(feat\.?|ft\.?|featuring)\s+.*$", re.IGNORECASE)
# "(2014)" after a title: a year some libraries add and Discogs mostly not.
_YEAR = re.compile(r"[\(\[]\s*(?:19|20)\d{2}\s*[\)\]]")
# Between the names in one artist field: "A/B", "A, B", "A & B", "A vs B".
_ARTIST_SPLIT = re.compile(r",|&|/|;| vs\.? | and | x ", re.IGNORECASE)
_DISCOGS_NUMBER = re.compile(r"\s+\(\d+\)$")  # "Swayzak (2)"
_PUNCT = re.compile(r"[^\w\s]")


#: Letters Unicode does not decompose into a base letter and a mark, so
#: NFKD alone leaves them: "Nørbak" must still meet "Norbak".
_LETTERS = str.maketrans(
    {
        "ø": "o", "Ø": "O", "æ": "ae", "Æ": "AE", "œ": "oe", "Œ": "OE",
        "ß": "ss", "đ": "d", "Đ": "D", "ł": "l", "Ł": "L", "þ": "th",
        "Þ": "TH", "ð": "d", "Ð": "D", "ı": "i",
    }
)  # fmt: skip


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.translate(_LETTERS))
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = _PUNCT.sub(" ", text.casefold().replace("&", " and "))
    return " ".join(text.split())


def norm_title(title: str) -> str:
    """A title with generic mix tags, a bracketed year and featured artists
    taken off."""
    return _fold(_FEAT.sub("", _YEAR.sub(" ", _GENERIC_MIX.sub(" ", title or ""))))


def title_similarity(ours: str, theirs: str) -> float:
    """0–1 for two track titles, as typed.

    One containing the other word for word ("Songs from Liquid Days: No. 1,
    Changing Opinion" / "Changing Opinion") is a strong match. A title of
    punctuation alone ("////\\\\") normalises to nothing and is compared as
    typed instead.
    """
    a, b = norm_title(ours), norm_title(theirs)
    if not a or not b:
        raw_a, raw_b = "".join((ours or "").split()), "".join((theirs or "").split())
        return 1.0 if raw_a and raw_a.casefold() == raw_b.casefold() else 0.0
    if a == b:
        return 1.0
    short, long_ = sorted((a, b), key=len)
    if (len(short.split()) >= 2 or len(short) >= 6) and f" {short} " in f" {long_} ":
        return 0.9
    return similarity(a, b)


def search_title(title: str) -> str:
    """The title as typed, minus "(Original Mix)" and the like, which Discogs
    tracklists never carry: what to send to the search."""
    return " ".join(_GENERIC_MIX.sub(" ", title or "").split())


def norm_artist(artist: str) -> str:
    """An artist with Discogs' disambiguation number and ANV star taken off."""
    artist = _DISCOGS_NUMBER.sub("", (artist or "").replace("*", "").strip())
    folded = _fold(_FEAT.sub("", artist))
    return folded.removeprefix("the ")


def similarity(a: str, b: str) -> float:
    """0–1: the better of character similarity and shared-word overlap.

    Word overlap forgives order ("Mills Jeff" / "Jeff Mills", "A and B" /
    "B and A"); character similarity forgives spelling.
    """
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    chars = SequenceMatcher(None, a, b).ratio()
    wa, wb = set(a.split()), set(b.split())
    words = len(wa & wb) / len(wa | wb)
    return max(chars, words)


# ── search results ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Query:
    artist: str
    title: str
    album: str = ""
    seconds: float | None = None  # the track's length, for the tracklist check


def _result_artist(result: Mapping) -> str:
    """Search results title releases "Artist - Release Title"."""
    artist, sep, _title = str(result.get("title", "")).partition(" - ")
    return artist if sep else ""


def _result_title(result: Mapping) -> str:
    _artist, sep, title = str(result.get("title", "")).partition(" - ")
    return title if sep else str(result.get("title", ""))


def _names(artist: str) -> list[str]:
    """The whole field and each name in it: "Datassette/Datashat" is both."""
    names = [norm_artist(artist)]
    names += [norm_artist(p) for p in _ARTIST_SPLIT.split(artist) if p.strip()]
    return [n for n in dict.fromkeys(names) if n]


def _artist_score(query_artist: str, discogs_artist: str) -> float:
    """Several artists on either side ("A/B", "A, B", "A & B") match on any
    one of them."""
    ours, theirs = _names(query_artist), _names(discogs_artist)
    return max((similarity(a, b) for a in ours for b in theirs), default=0.0)


#: Which release of a record to prefer when several fit equally: the file a
#: DJ most likely bought, then the CD, then vinyl, then anything else.
_FORMAT_ORDER = {"file": 0, "cd": 1, "cdr": 1, "vinyl": 2}
_OTHER_FORMAT = 3


def format_rank(result: Mapping) -> int:
    """0 for a digital release, 1 CD, 2 vinyl, 3 anything else (search result)."""
    formats = [str(f).casefold() for f in result.get("format") or []]
    return min((_FORMAT_ORDER.get(f, _OTHER_FORMAT) for f in formats), default=_OTHER_FORMAT)


#: Preferred region for a record released per country: Discogs' country
#: names in each. "Worldwide" comes right after the chosen region.
REGIONS: dict[str, tuple[str, frozenset[str]]] = {
    "": ("Any", frozenset()),
    "europe": (
        "Europe",
        frozenset(
            {
                "europe", "uk & europe", "uk", "uk & ireland", "ireland", "germany",
                "france", "netherlands", "belgium", "benelux", "italy", "spain",
                "portugal", "sweden", "denmark", "norway", "finland", "scandinavia",
                "switzerland", "austria", "germany, austria, & switzerland", "poland",
                "czech republic", "greece", "hungary", "romania", "croatia",
                "slovenia", "slovakia", "estonia", "latvia", "lithuania", "iceland",
                "luxembourg", "bulgaria", "serbia", "ukraine", "russia",
            }
        ),
    ),
    "uk": ("UK", frozenset({"uk", "uk & europe", "uk & ireland", "uk & us"})),
    "north_america": (
        "North America",
        frozenset({"us", "usa & canada", "canada", "uk & us", "usa, canada & europe"}),
    ),
    "japan": ("Japan", frozenset({"japan"})),
    "asia": (
        "Asia",
        frozenset(
            {
                "japan", "south korea", "korea", "china", "taiwan", "hong kong",
                "singapore", "malaysia", "thailand", "indonesia", "philippines",
                "india", "vietnam", "asia",
            }
        ),
    ),
    "latin_america": (
        "Latin America",
        frozenset({"brazil", "argentina", "mexico", "chile", "colombia", "peru", "venezuela"}),
    ),
    "oceania": (
        "Oceania",
        frozenset({"australia", "new zealand", "australia & new zealand", "australasia"}),
    ),
}  # fmt: skip
DEFAULT_REGION = "europe"


def region_rank(result: Mapping, region: str) -> int:
    """0 in the preferred region, 1 worldwide, 2 elsewhere; 0 for "Any"."""
    countries = REGIONS.get(region, ("", frozenset()))[1]
    if not countries:
        return 0
    country = str(result.get("country") or "").casefold()
    if country in countries:
        return 0
    return 1 if country == "worldwide" else 2


def release_preference(result: Mapping, region: str = DEFAULT_REGION) -> tuple[int, int]:
    """Among equally good releases of a record: digital first, then CD, then
    vinyl; within a format, the preferred region, then worldwide."""
    return format_rank(result), region_rank(result, region)


def result_artist_score(query: Query, result: Mapping) -> float:
    """0–1: how well a search result's release artist is the track's artist.
    A compilation ("Various") never is."""
    artist = _result_artist(result)
    if norm_artist(artist) == "various":
        return 0.0
    return _artist_score(query.artist, artist)


def rank_candidates(
    query: Query, results: Sequence[Mapping], region: str = DEFAULT_REGION
) -> list[tuple[float, Mapping]]:
    """Search results, best first, with a 0–1 pre-score from the result alone.

    A compilation ("Various") is ranked below the artist's own release of the
    same track: its year and label describe the compilation, not the record.
    """
    ranked = []
    for r in results:
        artist = _result_artist(r)
        score = _artist_score(query.artist, artist)
        if norm_artist(artist) == "various":
            score = 0.5
        if query.album:
            score = 0.8 * score + 0.2 * similarity(
                norm_title(query.album), norm_title(_result_title(r))
            )
        ranked.append((score, r))
    # Pressings of one record score alike; among equals, the preferred one.
    return sorted(ranked, key=lambda sr: (-round(sr[0], 3), *release_preference(sr[1], region)))


def original_year(
    release_year: int | None, master_id: int | None, results: Sequence[Mapping]
) -> int | None:
    """The earliest year among the search results on the same master."""
    years = [release_year] if release_year else []
    if master_id:
        for r in results:
            if r.get("master_id") == master_id:
                try:
                    y = int(r.get("year") or 0)
                except (TypeError, ValueError):
                    continue
                if y > 0:
                    years.append(y)
    return min(years) if years else None


# ── the release ──────────────────────────────────────────────────────────────


def parse_duration(text: str) -> float | None:
    """Discogs track length "5:32" or "1:02:10" → seconds, or None."""
    parts = (text or "").strip().split(":")
    if len(parts) < 2 or not all(p.isdigit() for p in parts):
        return None
    seconds = 0
    for p in parts:
        seconds = seconds * 60 + int(p)
    return float(seconds)


def _duration_score(ours: float | None, theirs: float | None) -> float:
    """1 within 5 s, 0 beyond 30 s; 0.5 — no evidence — when either is unknown."""
    if not ours or not theirs:
        return 0.5
    off = abs(ours - theirs)
    return 1.0 if off <= 5 else max(0.0, 1.0 - (off - 5) / 25)


@dataclass(frozen=True)
class ReleaseMatch:
    confidence: float
    artist: float
    title: float
    duration: float
    position: str  # the track's place on the release, "A2"
    track_title: str

    @property
    def accepted(self) -> bool:
        return (
            self.confidence >= ACCEPT_AT and self.artist >= PART_FLOOR and self.title >= PART_FLOOR
        )


def _track_artists(track: Mapping, release: Mapping) -> str:
    names = track.get("artists") or release.get("artists") or []
    return ", ".join(str(a.get("name", "")) for a in names)


def score_release(query: Query, release: Mapping) -> ReleaseMatch:
    """Find the query's track on *release*: among the tracks whose title is
    close, the best-scoring decides. None close: no match (confidence 0)."""
    best = ReleaseMatch(0.0, 0.0, 0.0, 0.0, "", "")
    for track in release.get("tracklist") or []:
        if track.get("type_", "track") != "track":
            continue  # headings and index entries
        title = title_similarity(query.title, str(track.get("title", "")))
        if title < PART_FLOOR:
            # Title first: a track named otherwise is not this one, however
            # well its length agrees — a 90-track compilation always has a
            # track of the right length.
            continue
        artist = _artist_score(query.artist, _track_artists(track, release))
        duration = _duration_score(query.seconds, parse_duration(str(track.get("duration", ""))))
        confidence = 0.4 * artist + 0.45 * title + 0.15 * duration
        if confidence > best.confidence:
            best = ReleaseMatch(
                confidence, artist, title, duration,
                str(track.get("position", "")), str(track.get("title", "")),
            )  # fmt: skip
    return best
