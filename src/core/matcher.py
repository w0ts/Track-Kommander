"""Harmonic matching — finding a track, and ranking the library against it.

The scoring rules are in :mod:`core.match_scores`; applying them to a whole
library is :mod:`core.ranking`, over the columns kept by
:mod:`core.library_snapshot`. This module is what the rest of the
application calls, and every name that used to live here still does.
"""

from __future__ import annotations

import sqlite3

from core import ranking
from core.db import get_conn
from core.embeddings import EMBEDDING_MODELS
from core.features import BY_KEY, ActiveAxis, FeatureSpec
from core.library_snapshot import snapshot
from core.match_scores import (
    _KEY_CROSS,
    _KEY_SAME,
    KEY_SCORE_LEVELS,
    MAX_TRANSPOSE,
    RATING_BONUS,
    TRANSPOSE_COST,
    VIBE_SIGMA,
    _best_bpm_match,
    _best_transpose_cached,
    _cosine,
    _harmonic_compatible,
    _key_distance,
    _norm_value,
    _parse_camelot,
    _row_text,
    _score_bpm,
    _score_key,
    _score_rating,
    _score_scalar,
    absolute_axes,
    best_transpose,
    key_distance,
    key_gap,
    transpose_camelot,
)

__all__ = [
    "BY_KEY",
    "KEY_SCORE_LEVELS",
    "MAX_TRANSPOSE",
    "RATING_BONUS",
    "TRANSPOSE_COST",
    "VIBE_SIGMA",
    "_KEY_CROSS",
    "_KEY_SAME",
    "_best_bpm_match",
    "_best_transpose_cached",
    "_cosine",
    "_harmonic_compatible",
    "_key_distance",
    "_norm_value",
    "_parse_camelot",
    "_row_text",
    "_score_bpm",
    "_score_key",
    "_score_rating",
    "_score_scalar",
    "absolute_axes",
    "best_transpose",
    "find_track",
    "find_track_by_path",
    "key_distance",
    "key_gap",
    "rank_matches_absolute",
    "rank_matches_axes",
    "transpose_camelot",
]


def find_track_by_path(path: str) -> sqlite3.Row | None:
    """Look up a track by exact filesystem path."""
    with get_conn() as c:
        return c.execute("SELECT * FROM tracks WHERE path = ?", (path,)).fetchone()


def find_track(query: str) -> sqlite3.Row | None:
    """Three-level fallback search to handle 'Artist - Title' strings."""

    def _like(term: str) -> sqlite3.Row | None:
        with get_conn() as c:
            return c.execute(
                "SELECT * FROM tracks WHERE filename LIKE ? OR path LIKE ? LIMIT 1",
                (f"%{term}%", f"%{term}%"),
            ).fetchone()

    if r := _like(query):
        return r
    title = query.rsplit(" - ", 1)[-1].strip() if " - " in query else ""
    if title and title != query:
        if r := _like(title):
            return r
    first = (title or query).split()[0] if (title or query).split() else ""
    if len(first) > 3:
        return _like(first)
    return None


#: Columns holding a whole embedding per track (CLAP's vector is ~2 KB).
#: Only a similarity axis on that model looks at them, so a ranking query
#: leaves them out unless such an axis is active. Measured on an 872-track
#: library this trims the fetch from 3.6 to 3.3 ms; most of what is left is
#: building the 55-column rows the results table needs.
_EMBEDDING_COLUMNS: frozenset[str] = frozenset(
    m.column for m in EMBEDDING_MODELS.values() if m.storage == "column" and m.column
)


def _embedding_columns_needed(specs: list[FeatureSpec]) -> frozenset[str]:
    """The embedding columns the given axes score on."""
    needed = set()
    for spec in specs:
        model = EMBEDDING_MODELS.get(spec.embedding) if spec.embedding else None
        if spec.kind == "similarity" and model is not None and model.column:
            needed.add(model.column)
    return frozenset(needed)


def _select_list(c: sqlite3.Connection, needed: frozenset[str]) -> str:
    """Column list for a ranking query: every column but unused embeddings.

    Every other column stays, because the result rows go straight to the
    results table, which shows most of them. The list is read from the live
    schema on each call (one PRAGMA, a few microseconds) rather than cached,
    so it can never go stale against a migrated or test database.
    """
    skip = _EMBEDDING_COLUMNS - needed
    if not skip:
        return "*"
    names = [r[1] for r in c.execute("PRAGMA table_info(tracks)") if r[1] not in skip]
    return ", ".join('"' + n.replace('"', '""') + '"' for n in names)


def _rank_order(result: dict) -> tuple[float, str]:
    """Best composite first; ties by path.

    Ties are common (rating-only ranking gives five distinct scores across a
    whole library), and without a tiebreak their order was whatever order
    SQLite happened to scan the table in.
    """
    return -result["composite"], result["row"]["path"] or ""


def _candidates_query(
    src_bpm: float, max_bpm_pct: float, src_path: str, select: str = "*"
) -> tuple[str, list]:
    """Build a SQL query pre-filtered by BPM range (when restricted) only.
    Key scoring is intentionally left to Python so all tracks are ranked.
    """
    clauses: list[str] = ["path != ?"]
    params: list = [src_path]

    # BPM pre-filter (optional): cover normal, ×2, and /2 octave variants.
    if max_bpm_pct > 0 and src_bpm > 0:
        f = max_bpm_pct / 100.0
        lo, hi = src_bpm * (1 - f), src_bpm * (1 + f)
        clauses.append(
            "(bpm BETWEEN ? AND ?"  # normal
            " OR bpm BETWEEN ? AND ?"  # ×2: match plays at double speed
            " OR bpm BETWEEN ? AND ?)"  # /2: match plays at half speed
        )
        params.extend([lo, hi, lo / 2, hi / 2, lo * 2, hi * 2])

    sql = f"SELECT {select} FROM tracks WHERE " + " AND ".join(clauses)
    return sql, params


# ── Ranking ─────────────────────────────────────────────────────────────────


def rank_matches_axes(
    source: sqlite3.Row,
    axes: list[ActiveAxis],
    max_bpm_pct: float = 0,
    src_bpm_override: float | None = None,
    harmonic_only: bool = False,
) -> ranking.Ranked:
    """Rank matches from a list of user-configured axes (value + strength).

    Each axis contributes a 0–100 sub-score weighted by its strength; the
    composite is the strength-weighted average. BPM and Key are always
    evaluated (for the results table and the optional hard filters)
    regardless of whether they're active axes.

    Axes with no data for a candidate are skipped for that candidate rather than
    scored as neutral, so a missing feature never drags a match up or down.

    harmonic_only: drop candidates outside the classic Camelot mixing set.
                   Off by default, so every axis — Key included — only *ranks*.
                   This used to be implied by the Key axis carrying any weight,
                   which conflated "weight key in the ranking" with "hide 18 of
                   the 24 keys" and, combined with ``max_bpm_pct``, could cut a
                   10k library to a couple of dozen rows.

    The result behaves as the list of dicts it used to be — length, indexing,
    slicing, iteration — and builds each dict when it is first asked for.
    """
    return ranking.rank_axes(
        snapshot(get_conn),
        source,
        axes,
        max_bpm_pct=max_bpm_pct,
        src_bpm_override=src_bpm_override,
        harmonic_only=harmonic_only,
    )


def rank_matches_absolute(axes: list[ActiveAxis]) -> ranking.Ranked | list:
    """Rank the whole library on absolute-target axes alone — no source track.

    Nothing loaded in Traktor used to mean no matching at all: the results fell
    back to a plain rating-sorted library browse. But the axes the user set to
    an absolute target (Dynamics at 90%, Rating at 5★, …) don't need a current
    track to say which tracks fit, so they keep working here.

    Returns ``[]`` when no axis can rank — the caller then falls back to the
    plain library browse. Results carry the same keys as
    :func:`rank_matches_axes`, with the relational sub-scores zeroed since
    there is nothing to compare against.
    """
    active = absolute_axes(axes)
    if not active:
        return []
    return ranking.rank_absolute(snapshot(get_conn), active)
