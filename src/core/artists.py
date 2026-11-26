"""Who a track is by, read the same way whatever wrote it — for matching and
searching only; nothing here writes a tag.

Three kinds of source, measured on the user's data (2026-10-04):

- Spotify gives a list, the remixer included: "Aligned - Populous Remix"
  is [Modis, Populous].
- Discogs gives main artists and, apart, a "Remix" credit; a name may carry
  a disambiguation number, "Mala (4)", or an alias star, "Âme*".
- A file's ID3 (what Traktor shows) and a file's path are free
  text in any convention: ", " (Beatport), "/" (ID3v2.3 taggers), ";"
  (Picard), " x ", " feat. ". "Modis/Populous" and "Modis, Populous" are
  the same pair.

So a credit is read as a set of names, each normalised (case, accents,
punctuation, Discogs' "(4)" and "*"). Unambiguous separators always split.
"&" and " and " are ambiguous — "JKS & Lacchesi" is two artists, "Barker &
Baumecker" and "Sailor & I" are one — and split only when both halves are
artists of their own elsewhere (*known*). "(feat. X)" in a title adds X.
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from functools import lru_cache

#: Always two artists, whatever the source.
_SPLIT = re.compile(
    r"\s*(?:/|;|\x00|,|\bfeat\.?\s|\bft\.?\s|\bfeaturing\s|\s+x\s+|\bvs\.?\s|\bwith\s)\s*",
    re.IGNORECASE,
)
#: Two artists only when each half is one on its own.
_AMBIGUOUS = re.compile(r"\s+(?:&|and)\s+", re.IGNORECASE)
#: "(feat. X)" / "[ft. X]" in a title.
_FEAT_IN_TITLE = re.compile(r"[(\[]\s*(?:feat\.?|ft\.?|featuring)\s+([^)\]]+)[)\]]", re.IGNORECASE)
#: Words a file name may leave out of an artist's name ("Koze" for "DJ Koze").
_GENERIC = {"dj", "mc", "the", "and", "feat", "ft"}
#: Names this close are one name with a typo ("Lacchesi"/"Lacchessi").
SAME_NAME = 0.9


def key(name: str) -> str:
    """One artist name as compared: no Discogs number or star, no accents,
    case or punctuation, no leading "The", "DJ" or "MC"."""
    # Discogs' "Mala (4)", Spotify's "Carvalho(BR)", Discogs' alias star.
    name = re.sub(r"\s*\([^()]*\)\s*$|\*$", "", name.strip())
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    name = re.sub(r"[^a-z0-9]+", " ", name).strip()
    # "DJ Rolando" is Spotify's "Rolando" (Knights Of The Jaguar, 2026-10-04).
    return re.sub(r"^(?:the|dj|mc)\s+", "", name)


def names(
    credit: str, title: str = "", known: frozenset[str] | None = None, loose: bool = False
) -> tuple[str, ...]:
    """The artist keys in *credit* (and featured in *title*), in order.
    *loose* (for matching): an "&" name kept whole is also tried in halves —
    "Ragie Ban & Carvalho" must meet Spotify's [Ragie Ban, Carvalho(BR)]
    though Carvalho is known nowhere alone; the title must match as well."""
    known = known_artists() if known is None else known
    out: list[str] = []
    for part in _SPLIT.split(credit or ""):
        pieces = _AMBIGUOUS.split(part)
        keys = [key(p) for p in pieces]
        if len(keys) > 1 and all(k in known for k in keys):
            out += keys  # "JKS & Lacchesi": both stand alone elsewhere
        else:
            out.append(key(part))  # "Barker & Baumecker": one act
            if loose and len(keys) > 1:
                out += keys
    for featured in _FEAT_IN_TITLE.findall(title or ""):
        out += [key(p) for p in _SPLIT.split(featured)]
    return tuple(dict.fromkeys(k for k in out if k))


def split_credit(credit: str) -> list[str]:
    """A credit's names as written, split on the unambiguous separators
    only (for search queries; :func:`names` is for comparing)."""
    return [p.strip() for p in _SPLIT.split(credit or "") if p.strip()]


def same_name(a: str, b: str) -> bool:
    """Two keys for one artist: equal, or a typo apart in a long name."""
    return a == b or (
        min(len(a), len(b)) >= 6 and difflib.SequenceMatcher(None, a, b).ratio() >= SAME_NAME
    )


def share(
    credit_a: str,
    credit_b: str,
    title_a: str = "",
    title_b: str = "",
    known: frozenset[str] | None = None,
) -> bool:
    """At least one artist in common between two credits."""
    a = names(credit_a, title_a, known, loose=True)
    b = names(credit_b, title_b, known, loose=True)
    return any(same_name(x, y) for x in a for y in b)


def in_text(credit: str, text: str, known: frozenset[str] | None = None) -> bool:
    """Some artist of *credit* is named in free *text* (a file path):
    every word of that artist's name is there."""
    words = set(re.sub(r"[^a-z0-9]+", " ", key(text)).split())
    for k in names(credit, known=known, loose=True):
        parts = [w for w in k.split() if (len(w) > 1 or w.isdigit()) and w not in _GENERIC]
        if parts and all(w in words for w in parts):
            return True
    return False


def main_artist(credit: str, known: frozenset[str] | None = None) -> str:
    """The first artist's name as written in *credit* (for a search query)."""
    part = _SPLIT.split(credit or "")[0].strip()
    pieces = _AMBIGUOUS.split(part)
    if len(pieces) > 1 and all(
        key(p) in (known_artists() if known is None else known) for p in pieces
    ):
        return pieces[0].strip()
    return part


@lru_cache(maxsize=1)
def known_artists() -> frozenset[str]:
    """Names known to stand alone: every single-artist credit in the
    Traktor collection and every name an add-on knows (core.extensions).
    Read once per run (~0.1 s); empty when neither is there."""
    found: set[str] = set()
    try:
        from core.collection_source import collection_metadata
        from core.traktor import find_traktor_nml

        for m in collection_metadata(find_traktor_nml()).values():
            parts = _SPLIT.split(m.get("artist", "") or "")
            found.update(key(p) for p in parts if p and not _AMBIGUOUS.search(p))
    except OSError:
        pass  # no collection here: the playlists still teach some
    from core.extensions import artist_sources

    for source in artist_sources():  # e.g. an add-on's playlists' artists
        found.update(key(name) for name in source())
    found.discard("")
    return frozenset(found)
