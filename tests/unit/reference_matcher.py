"""The rankers as they were before they were vectorised, kept as an oracle.

Row by row, in plain Python: slow, and easy to read as a statement of what a
match score *is*. The rankers in :mod:`core.matcher` do the same sums as
arrays, and ``test_matcher_parity.py`` holds them to these, result for
result, on libraries full of the awkward cases — missing tempos, keys that
are not keys, tracks with no analysis at all.

Copied from ``core/matcher.py`` as it stood on 2026-09-29. It is a fixture,
not a second implementation to keep in step: if what a match score *means*
is changed on purpose, change it here too and say why in the commit.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable

from core.embeddings import EMBEDDING_MODELS, load_vectors, score_similarity_bulk
from core.features import BY_KEY, ActiveAxis
from core.genre_match import (
    document_frequencies,
    genre_tokens,
    idf_weights,
    score_style,
    score_tokens,
    style_vector,
)
from core.library_snapshot import DERIVED
from core.matcher import (
    _best_bpm_match,
    _candidates_query,
    _embedding_columns_needed,
    _harmonic_compatible,
    _key_distance,
    _rank_order,
    _row_text,
    _score_key,
    _score_rating,
    _score_scalar,
    _select_list,
    absolute_axes,
    best_transpose,
)


def _genre(row) -> str:
    """The effective genre (core.track_meta), the rule the ranker applies:
    Discogs' when the row carries a match, else the cleaned Traktor tag."""
    return DERIVED["genre_effective"](row) or ""


def rank_matches_axes(
    get_conn: Callable[[], sqlite3.Connection],
    source: sqlite3.Row,
    axes: list[ActiveAxis],
    max_bpm_pct: float = 0,
    src_bpm_override: float | None = None,
    harmonic_only: bool = False,
) -> list[dict]:
    """Rank matches from a list of user-configured axes (value + strength).

    Each axis contributes a 0–100 sub-score weighted by its strength; the
    composite is the strength-weighted average, gently blended with star rating.
    BPM and Key are always evaluated (for the results table and the optional
    hard filters) regardless of whether they're active axes.

    Axes with no data for a candidate are skipped for that candidate rather than
    scored as neutral, so a missing feature never drags a match up or down.

    harmonic_only: drop candidates outside the classic Camelot mixing set.
                   Off by default, so every axis — Key included — only *ranks*.
                   This used to be implied by the Key axis carrying any weight,
                   which conflated "weight key in the ranking" with "hide 18 of
                   the 24 keys" and, combined with ``max_bpm_pct``, could cut a
                   10k library to a couple of dozen rows.
    """
    active = {a.key: a for a in axes if a.strength > 0}
    src_bpm = src_bpm_override or source["bpm"] or 0
    src_path = source["path"]
    src_cam = source["camelot"]

    needed = _embedding_columns_needed([BY_KEY[k] for k in active])
    with get_conn() as c:
        sql, params = _candidates_query(src_bpm, max_bpm_pct, src_path, _select_list(c, needed))
        candidates = c.execute(sql, params).fetchall()
    has_rating = bool(candidates) and "rating" in candidates[0].keys()

    # Genre/style precompute — only paid for when the axis is active. The Traktor
    # genre axis needs library-wide IDF weights; building them from the candidate
    # set (already in memory) is one O(N) pass and avoids any persistent index.
    src_tokens = None
    idf: dict[str, float] = {}
    if "genre" in active:
        src_tokens = genre_tokens(_genre(source))
        token_sets = [genre_tokens(_genre(r)) for r in candidates]
        df = document_frequencies([*token_sets, src_tokens])
        idf = idf_weights(df, len(token_sets) + 1)
    src_style: tuple = ()
    if "style" in active:
        src_style = style_vector(_row_text(source, "genres"))

    # Similarity precompute — for each active similarity axis, load that
    # embedding model's vectors for the whole population (source + candidates)
    # in one pass. Derived models (the timbral fingerprint) z-score against the
    # population here; column models (CLAP) just decode their BLOBs. Only the
    # models actually referenced by an active axis are paid for.
    # Scoring is then done for the whole population up front (one matmul per
    # model) rather than per candidate inside the ranking loop, so relaxing the
    # filters — which multiplies the candidate count — costs almost nothing.
    sim_scores: dict[str, dict[str, float]] = {}
    for a in active.values():
        spec = BY_KEY[a.key]
        if spec.kind == "similarity" and spec.embedding:
            model = EMBEDDING_MODELS.get(spec.embedding)
            if model is not None and spec.embedding not in sim_scores:
                vectors = load_vectors(model, [source, *candidates])
                sim_scores[spec.embedding] = score_similarity_bulk(
                    model,
                    vectors.get(src_path),
                    vectors,
                )

    results: list[dict] = []
    for row in candidates:
        # Opt-in hard harmonic filter. Independent of the Key axis's weight:
        # narrowing the candidate set and weighting the ranking are different
        # decisions, so they are now different switches.
        cam = row["camelot"]
        if harmonic_only and not _harmonic_compatible(src_cam, cam):
            continue
        ks = _score_key(src_cam, cam)
        bs, effective_bpm, bpm_factor = _best_bpm_match(src_bpm, row["bpm"] or 0)
        if max_bpm_pct > 0 and src_bpm > 0:
            pct = abs(effective_bpm - src_bpm) / src_bpm * 100
            if pct > max_bpm_pct:
                continue
        # The key and BPM scorers return 0 when either side is missing, which
        # is right for the displayed score but wrong for ranking: an unkeyed
        # track is an unknown, not the worst clash in the library. So those
        # two axes are skipped for the candidate, like any other axis with
        # no data, instead of scoring 0.
        key_known = _key_distance(src_cam, cam) is not None
        bpm_known = bool(src_bpm) and bool(row["bpm"])

        transpose: int | None = None
        axis_scores: dict[str, float] = {}
        weighted_sum = 0.0
        total_w = 0.0
        vibe_sum = 0.0
        vibe_w = 0.0
        for a in active.values():
            spec = BY_KEY[a.key]
            if spec.kind == "bpm":
                sub: float | None = bs if bpm_known else None
            elif spec.kind == "key":
                if a.max_semitones:
                    # transpose stays 0 for an unknown key, as it always has.
                    transpose, sub = best_transpose(src_cam, cam, max_semitones=a.max_semitones)
                else:
                    sub = ks
                if not key_known:
                    sub = None
            elif spec.kind == "similarity":
                sub = sim_scores.get(spec.embedding, {}).get(row["path"])
            elif spec.kind == "genre":
                x = score_tokens(src_tokens, genre_tokens(_genre(row)), idf)
                sub = None if x is None else x * 100.0
            elif spec.kind == "style":
                x = score_style(src_style, style_vector(_row_text(row, "genres")))
                sub = None if x is None else x * 100.0
            else:
                sub = _score_scalar(spec, a.value, row)
            if sub is None:
                continue
            axis_scores[a.key] = sub
            weighted_sum += sub * a.strength
            total_w += a.strength
            if spec.kind not in ("bpm", "key"):
                vibe_sum += sub * a.strength
                vibe_w += a.strength

        # Rating is now a first-class axis (see core.features), so it's already
        # in the weighted average when active — no separate bonus blend here.
        rs = _score_rating(row["rating"] if has_rating else None)
        cs = (weighted_sum / total_w) if total_w > 0 else 50.0
        vibe_score = (vibe_sum / vibe_w) if vibe_w > 0 else 50.0

        results.append(
            {
                "row": row,
                "composite": cs,
                "bpm_score": bs,
                "key_score": ks,
                "vibe_score": vibe_score,
                "rating_score": rs,
                "effective_bpm": effective_bpm,
                "bpm_factor": bpm_factor,
                "axis_scores": axis_scores,
                # How far Traktor's Key knob has to move for this one
                # to fit. 0 when it already does; None when the
                # transpose axis is off.
                "transpose": transpose,
            }
        )
    results.sort(key=_rank_order)
    return results


def rank_matches_absolute(
    get_conn: Callable[[], sqlite3.Connection], axes: list[ActiveAxis]
) -> list[dict]:
    """Rank the whole library on absolute-target axes alone — no source track.

    Nothing loaded in Traktor used to mean no matching at all: the results fell
    back to a plain rating-sorted library browse. But the axes the user set to
    an absolute target (Dynamics at 90%, Rating at 5★, …) don't need a current
    track to say which tracks fit, so they keep working here.

    Returns ``[]`` when no axis can rank — the caller then falls back to the
    plain library browse. Result dicts carry the same keys as
    :func:`rank_matches_axes`, with the relational sub-scores zeroed since
    there is nothing to compare against.
    """
    active = absolute_axes(axes)
    if not active:
        return []

    # Only scalar axes reach here, so no embedding column is ever needed.
    with get_conn() as c:
        select = _select_list(c, frozenset())
        candidates = c.execute(f"SELECT {select} FROM tracks").fetchall()
    has_rating = bool(candidates) and "rating" in candidates[0].keys()

    results: list[dict] = []
    for row in candidates:
        axis_scores: dict[str, float] = {}
        weighted_sum = 0.0
        total_w = 0.0
        for a in active:
            sub = _score_scalar(BY_KEY[a.key], a.value, row)
            if sub is None:
                continue
            axis_scores[a.key] = sub
            weighted_sum += sub * a.strength
            total_w += a.strength
        # A track with no data on any active axis isn't a bad match, it's an
        # unknown one — same neutral 50 that rank_matches_axes gives it.
        cs = (weighted_sum / total_w) if total_w > 0 else 50.0
        results.append(
            {
                "row": row,
                "composite": cs,
                "bpm_score": 0.0,
                "key_score": 0.0,
                "vibe_score": cs,
                "rating_score": _score_rating(row["rating"] if has_rating else None),
                "effective_bpm": row["bpm"] or 0.0,
                "bpm_factor": 1,
                "axis_scores": axis_scores,
            }
        )
    results.sort(key=_rank_order)
    return results
