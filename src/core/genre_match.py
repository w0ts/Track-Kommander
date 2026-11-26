"""Genre/style matching — two independent, Qt-free notions of "genre".

  * **Traktor genre** — the free-text ``genre`` metadata field. Matched by
    splitting into word tokens and scoring shared-token overlap, weighted by
    inverse document frequency (IDF) so rare tags (e.g. "drone") count for more
    than ubiquitous ones (e.g. "house"). The library-wide document frequencies
    are the inverted index the weighting needs; they're cheap to build in one
    pass over the candidate set the matcher already holds in memory.

  * **Detected style** — our CLAP zero-shot top-k genres (the ``genres`` column,
    a JSON list of ``{"name", "prob"}``). Encoded as a sparse weight vector
    keyed by label and scored by cosine similarity.

Both are *relational*: a candidate is scored against the current source track.
The parse functions are ``lru_cache``-d on their raw string input — genre
strings repeat heavily across a library and across successive queries, so the
cache keeps matching fast at 10k+ tracks.

Dependency-light (stdlib only) on purpose, so :mod:`core.matcher` and the unit
tests import it without pulling in numpy/PySide6.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Iterable
from functools import lru_cache

# Word tokens: lowercase alphanumeric runs of length >= 2, minus a few
# meaningless connectors. Splitting multi-word genres ("Deep House") into words
# lets "Deep House" and "Tech House" share the "house" token for partial credit.
# Any script, not only a–z: "Электронная музыка" is two words, where the
# ASCII pattern found none and the track silently dropped off the axis.
_TOKEN_RE = re.compile(r"[^\W_]+")
_STOPWORDS = frozenset({"and", "the", "of", "feat"})

# Bounded so a pathological library can't grow the caches without limit; far
# above the distinct-genre-string count of any real collection.
_CACHE = 8192

# The detected genres are another matter: three names *with their
# probabilities*, so the text differs for every track and the cache needs
# room for a whole library. At the size above, a library larger than it
# missed on every lookup of every re-rank — 20,000 parses each time. About
# half a kilobyte an entry, so this is some 60 MB at the very most.
_STYLE_CACHE = 131_072


@lru_cache(maxsize=_CACHE)
def genre_tokens(genre: str) -> frozenset[str]:
    """Split a free-text genre string into a set of word tokens."""
    if not genre:
        return frozenset()
    return frozenset(
        t for t in _TOKEN_RE.findall(genre.lower()) if len(t) >= 2 and t not in _STOPWORDS
    )


@lru_cache(maxsize=_STYLE_CACHE)
def style_vector(genres_json: str) -> tuple[tuple[str, float], ...]:
    """Parse CLAP top-k genres JSON into a sparse vector (label → prob).

    Returned as a sorted tuple of pairs so the result is hashable and cacheable.
    Empty tuple when there's no usable data.
    """
    if not genres_json:
        return ()
    try:
        data = json.loads(genres_json)
    except (ValueError, TypeError):
        return ()
    if not isinstance(data, list):
        return ()
    vec: dict[str, float] = {}
    for g in data:
        if not isinstance(g, dict):
            continue
        try:
            name = str(g["name"]).strip().lower()
            prob = float(g.get("prob", 0.0))
        except (TypeError, KeyError, ValueError):
            continue
        if name and prob > 0.0:
            vec[name] = max(vec.get(name, 0.0), prob)
    return tuple(sorted(vec.items()))


def document_frequencies(token_sets: Iterable[frozenset[str]]) -> dict[str, int]:
    """Count, per token, how many token-sets contain it (the inverted index)."""
    df: dict[str, int] = {}
    for ts in token_sets:
        for t in ts:
            df[t] = df.get(t, 0) + 1
    return df


def idf_weights(df: dict[str, int], n: int) -> dict[str, float]:
    """Smoothed inverse document frequency for a corpus of ``n`` documents.

    ``log((n + 1) / (df + 1)) + 1`` — always positive, and a token present in
    every document still carries a small (≈1.0) weight rather than vanishing.
    """
    if n <= 0:
        return {}
    return {t: math.log((n + 1.0) / (c + 1.0)) + 1.0 for t, c in df.items()}


def score_tokens(
    src: frozenset[str] | None,
    cand: frozenset[str] | None,
    idf: dict[str, float],
) -> float | None:
    """IDF-weighted overlap of candidate tokens against the source's, in [0, 1].

    ``None`` when either side has no genre tokens (no data → don't score). A
    candidate that *has* a genre but shares nothing with the source scores 0.0
    (a real "different genre"), not ``None``.
    """
    if not src or not cand:
        return None
    src_w = sum(idf.get(t, 1.0) for t in src)
    if src_w <= 0.0:
        return None
    shared = sum(idf.get(t, 1.0) for t in (src & cand))
    return max(0.0, min(1.0, shared / src_w))


def score_style(
    src: tuple[tuple[str, float], ...],
    cand: tuple[tuple[str, float], ...],
) -> float | None:
    """Cosine similarity of two sparse style vectors, in [0, 1].

    ``None`` when either side has no detected style; 0.0 when they share no
    labels.
    """
    if not src or not cand:
        return None
    sd = dict(src)
    cd = dict(cand)
    dot = sum(w * cd[k] for k, w in sd.items() if k in cd)
    if dot <= 0.0:
        return 0.0
    ns = math.sqrt(sum(w * w for w in sd.values()))
    nc = math.sqrt(sum(w * w for w in cd.values()))
    if ns == 0.0 or nc == 0.0:
        return None
    return max(0.0, min(1.0, dot / (ns * nc)))
