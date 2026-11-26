"""Ranking a library against a track, a whole column at a time.

The rules are those of :mod:`core.match_scores`, which says what each score
means for one candidate. Here they are applied to every track at once, over
the columns of a :class:`~core.library_snapshot.Snapshot`.

It used to be a Python loop over rows fetched afresh each time. On a
20,000-track library a re-rank took 120–365 ms, on the GUI thread, on every
slider tick. The work splits three ways, and each part is done the cheapest
way that gives the same answer:

* **Few distinct inputs** — a key is one of two dozen codes, a genre tag one
  of a few hundred. The rule is called once per distinct value, through the
  very function the row-by-row version called, and looked up for each track.
* **Continuous inputs** — tempo, a feature against its target, a rating. The
  arithmetic is repeated on arrays, operation for operation.
* **Vectors** — similarity and detected style are a matrix product.

``tests/unit/test_matcher_parity.py`` holds the result to the row-by-row
version kept in ``tests/unit/reference_matcher.py``.

Only what is looked at is built. A ranking of 20,000 tracks is shown fifty
at a time, so :class:`Ranked` keeps the scores as arrays and makes a result's
dict when it is first asked for.
"""

from __future__ import annotations

import math
import sqlite3
from collections.abc import Iterator, Sequence
from dataclasses import dataclass

import numpy as np

from core.constants import BPM_PLATEAU, BPM_ZERO
from core.embeddings import EMBEDDING_MODELS, EmbeddingModel, load_vectors
from core.features import BY_KEY, ActiveAxis, FeatureSpec
from core.genre_match import genre_tokens, idf_weights, score_tokens, style_vector
from core.library_snapshot import DERIVED, Snapshot
from core.match_scores import (
    VIBE_SIGMA,
    _harmonic_compatible,
    _key_distance,
    _row_text,
    _score_key,
    best_transpose,
)

#: A score for every track, and where there is one: an axis with no data for
#: a candidate is left out of that candidate's average, not counted as zero.
_Scores = tuple[np.ndarray, np.ndarray]


def _nothing(n: int) -> _Scores:
    return np.zeros(n, dtype=np.float64), np.zeros(n, dtype=bool)


# ── the rules, on arrays ────────────────────────────────────────────────────


def _bpm_score(src: float, bpm: np.ndarray) -> np.ndarray:
    """:func:`core.match_scores._score_bpm`, for every candidate tempo."""
    pct = np.abs(bpm - src) / src * 100.0
    falling = (1.0 - (pct - BPM_PLATEAU) / (BPM_ZERO - BPM_PLATEAU)) * 100.0
    score = np.where(pct <= BPM_PLATEAU, 100.0, np.maximum(0.0, falling))
    score[bpm == 0.0] = 0.0
    return score


def _best_bpm(src: float, bpm: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """``(score, effective tempo, factor)`` — the best of playing each
    candidate as it is, at double speed, or at half.

    Ties go to the first of those, as ``max`` over them in that order does.
    """
    n = len(bpm)
    if not src:
        return np.zeros(n), bpm.copy(), np.ones(n, dtype=np.int64)
    played = np.stack([bpm, bpm * 2.0, bpm / 2.0])
    scores = np.stack([_bpm_score(src, tempo) for tempo in played])
    choice = np.argmax(scores, axis=0)
    rows = np.arange(n)
    score = scores[choice, rows]
    effective = played[choice, rows]
    factor = np.asarray([1, 2, -2], dtype=np.int64)[choice]
    # No tempo: nothing to compare, and nothing to double or halve.
    none = bpm == 0.0
    score[none] = 0.0
    effective[none] = 0.0
    factor[none] = 1
    return score, effective, factor


def _per_value(snap: Snapshot, column: str, rule) -> list:
    """*rule* applied once to each distinct value of *column*."""
    _codes, distinct = snap.coded(column)
    return [rule(value) for value in distinct]


def _spread(snap: Snapshot, column: str, per_value: list, dtype) -> np.ndarray:
    codes, _distinct = snap.coded(column)
    return np.asarray(per_value, dtype=dtype)[codes]


def _scalar(snap: Snapshot, spec: FeatureSpec, target: float) -> _Scores:
    """How close each track's feature is to *target*: a Gaussian, 0–100."""
    values, has = snap.normalised(spec)
    diff = values - target
    sigma = spec.sigma or VIBE_SIGMA
    score = np.exp(-(diff * diff) / (2.0 * sigma * sigma)) * 100.0
    return score, has


def _rating_score(snap: Snapshot) -> np.ndarray:
    """:func:`core.match_scores._score_rating`, for every track."""
    if "rating" not in snap.columns:
        return np.full(len(snap), 50.0)
    values, has = snap.numbers("rating")
    rated = has & (values != 0.0)
    return np.where(rated, values / 5 * 100.0, 50.0)


def _source_value(snap: Snapshot, source, column: str):
    """The source track's value of a derived column (core.library_snapshot.
    DERIVED): read from the snapshot when it is in the library, else worked
    out from the row given — which then has no Discogs match joined on."""
    i = snap.index.get(source["path"]) if source["path"] is not None else None
    if i is not None:
        return snap.raw(column)[i]
    return DERIVED[column](source)


def _genre(snap: Snapshot, source, candidates: np.ndarray) -> _Scores:
    """Shared genre words, weighted by how rare each is among *candidates*.

    The genre is the effective one (core.track_meta): Discogs' genres and
    styles for a matched track, else the Traktor tag with URLs and "Other"
    taken out — junk would otherwise be the rarest, strongest match there is.
    """
    src_tokens = genre_tokens(_source_value(snap, source, "genre_effective") or "")
    codes, distinct = snap.coded("genre_effective")
    tokens = [genre_tokens(v if isinstance(v, str) else "") for v in distinct]
    counts = np.bincount(codes[candidates], minlength=len(distinct))
    df: dict[str, int] = {}
    for words, count in zip(tokens, counts.tolist(), strict=True):
        for word in words if count else ():
            df[word] = df.get(word, 0) + count
    for word in src_tokens:
        df[word] = df.get(word, 0) + 1
    idf = idf_weights(df, len(candidates) + 1)

    scored = [score_tokens(src_tokens, words, idf) for words in tokens]
    score = np.asarray([0.0 if x is None else x * 100.0 for x in scored])
    has = np.asarray([x is not None for x in scored], dtype=bool)
    return score[codes], has[codes]


def _label(snap: Snapshot, source) -> _Scores:
    """100 on the same label, 0 on another; no score when either has none.

    Labels are compared whole, after core.track_meta.canonical_label: sharing
    the word "rytm" is not sharing a label.
    """
    src = _source_value(snap, source, "label_effective")
    if not src:
        return _nothing(len(snap))
    codes, distinct = snap.coded("label_effective")
    same = np.asarray([v == src for v in distinct], dtype=bool)
    known = np.asarray([bool(v) for v in distinct], dtype=bool)
    return np.where(same, 100.0, 0.0)[codes], known[codes]


def _style(snap: Snapshot, source) -> _Scores:
    """Cosine between detected-genre vectors, 0–100."""
    src = style_vector(_row_text(source, "genres"))
    if not src:
        return _nothing(len(snap))
    matrix, norms, labels = snap.styles()
    src_norm = math.sqrt(sum(w * w for _name, w in src))
    dense = np.zeros(len(labels), dtype=np.float64)
    for name, weight in src:
        if name in labels:
            dense[labels[name]] = weight
    dot = matrix @ dense if len(labels) else np.zeros(len(snap))
    has = norms > 0.0
    if src_norm == 0.0:
        # No length to divide by — but a candidate sharing nothing still
        # scores a plain zero, as it does one at a time.
        return np.zeros(len(snap)), has & (dot <= 0.0)
    with np.errstate(divide="ignore", invalid="ignore"):
        cosine = np.clip(dot / (src_norm * norms), 0.0, 1.0)
    score = np.where(dot <= 0.0, 0.0, cosine) * 100.0
    return np.where(has, score, 0.0), has


def _similarity(snap: Snapshot, model: EmbeddingModel, source, candidates: np.ndarray) -> _Scores:
    """Cosine to the source's embedding, through the model's floor, 0–100."""
    if model.storage == "derived":
        return _derived_similarity(snap, model, source, candidates)
    src = load_vectors(model, [source]).get(source["path"])
    if src is None or src.size == 0:
        return _nothing(len(snap))
    src_norm = float(np.linalg.norm(src))
    if src_norm == 0.0:
        return _nothing(len(snap))
    where, matrix, norms = snap.stacked(model, src.shape)
    return _cosine_scores(len(snap), where, matrix, norms, src, src_norm, model.sim_floor)


def _cosine_scores(
    n: int,
    where: np.ndarray,
    matrix: np.ndarray,
    norms: np.ndarray,
    src: np.ndarray,
    src_norm: float,
    floor: float,
) -> _Scores:
    score, has = _nothing(n)
    if not len(where):
        return score, has
    usable = norms > 0.0
    cos = np.zeros(len(where), dtype=np.float32)
    cos[usable] = (matrix[usable] @ (src / src_norm)) / norms[usable]
    scaled = np.maximum(0.0, (cos - floor) / (1.0 - floor)) * 100.0
    score[where[usable]] = scaled[usable]
    has[where[usable]] = True
    return score, has


def _derived_similarity(
    snap: Snapshot, model: EmbeddingModel, source, candidates: np.ndarray
) -> _Scores:
    """Similarity on a vector made of the track's own features, each scaled
    against the source and the candidates together — so it has to be worked
    out for the set being ranked, and cannot be kept."""
    columns = model.components
    if not columns:
        return _nothing(len(snap))
    src_values = []
    for column in columns:
        try:
            value = source[column]
        except (IndexError, KeyError):
            value = None
        if value is None:
            return _nothing(len(snap))
        try:
            src_values.append(float(value))
        except (TypeError, ValueError):
            return _nothing(len(snap))

    parts = [snap.numbers(column) for column in columns]
    whole = np.ones(len(snap), dtype=bool)
    for _values, has in parts:
        whole &= has
    where = candidates[whole[candidates]]
    population = np.vstack(
        [
            np.asarray(src_values, dtype=np.float64),
            np.column_stack([values[where] for values, _has in parts])
            if len(where)
            else np.zeros((0, len(columns))),
        ]
    )
    mean = population.mean(axis=0)
    std = population.std(axis=0)
    std[std == 0.0] = 1.0
    z = ((population - mean) / std).astype(np.float32)
    src = z[0]
    src_norm = float(np.linalg.norm(src))
    if src_norm == 0.0:
        return _nothing(len(snap))
    matrix = z[1:]
    return _cosine_scores(
        len(snap), where, matrix, np.linalg.norm(matrix, axis=1), src, src_norm, model.sim_floor
    )


# ── the result ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class _Axis:
    key: str
    score: np.ndarray
    has: np.ndarray


class Ranked(Sequence):
    """A ranking, best first, that reads as the list of dicts it used to be.

    Length, indexing, slicing and iteration all work as on a list. Each
    result's dict is made the first time it is asked for and kept, so the
    same position always gives the same object.
    """

    def __init__(
        self,
        rows: list[sqlite3.Row],
        order: np.ndarray,
        *,
        composite: np.ndarray,
        bpm_score: np.ndarray,
        key_score: np.ndarray,
        vibe_score: np.ndarray,
        rating_score: np.ndarray,
        effective_bpm: np.ndarray,
        bpm_factor: np.ndarray,
        axes: list[_Axis],
        transpose: np.ndarray | None,
        with_transpose: bool,
    ) -> None:
        self._rows = rows
        self._order = order
        self._composite = composite
        self._bpm_score = bpm_score
        self._key_score = key_score
        self._vibe_score = vibe_score
        self._rating_score = rating_score
        self._effective_bpm = effective_bpm
        self._bpm_factor = bpm_factor
        self._axes = axes
        self._transpose = transpose
        self._with_transpose = with_transpose
        self._made: dict[int, dict] = {}

    def __len__(self) -> int:
        return len(self._order)

    def rank_of(self, row: int) -> int | None:
        """Where the snapshot's row *row* placed, 0 first; None when it was
        filtered out (or is the source itself)."""
        hits = np.flatnonzero(self._order == row)
        return int(hits[0]) if len(hits) else None

    def _make(self, position: int) -> dict:
        made = self._made.get(position)
        if made is not None:
            return made
        i = int(self._order[position])
        result = {
            "row": self._rows[i],
            "composite": float(self._composite[i]),
            "bpm_score": float(self._bpm_score[i]),
            "key_score": float(self._key_score[i]),
            "vibe_score": float(self._vibe_score[i]),
            "rating_score": float(self._rating_score[i]),
            "effective_bpm": float(self._effective_bpm[i]),
            "bpm_factor": int(self._bpm_factor[i]),
            "axis_scores": {a.key: float(a.score[i]) for a in self._axes if a.has[i]},
        }
        if self._with_transpose:
            # How far Traktor's Key knob has to move for this one to fit.
            # 0 when it already does; None when the transpose axis is off.
            result["transpose"] = None if self._transpose is None else int(self._transpose[i])
        self._made[position] = result
        return result

    def __getitem__(self, item):
        if isinstance(item, slice):
            return [self._make(p) for p in range(*item.indices(len(self)))]
        position = item + len(self) if item < 0 else item
        if not 0 <= position < len(self):
            raise IndexError("ranking index out of range")
        return self._make(position)

    def __iter__(self) -> Iterator[dict]:
        return (self._make(p) for p in range(len(self)))

    def __eq__(self, other: object) -> bool:
        if isinstance(other, (list, Ranked)):
            return len(self) == len(other) and all(
                mine == theirs for mine, theirs in zip(self, other, strict=True)
            )
        return NotImplemented

    # Defining __eq__ leaves instances unhashable, as a list is.

    def __repr__(self) -> str:
        return f"<Ranked: {len(self)} result(s)>"


def _in_order(snap: Snapshot, keep: np.ndarray, composite: np.ndarray) -> np.ndarray:
    """*keep*, best composite first; ties by path.

    Ties are common (rating-only ranking gives five distinct scores across a
    whole library), and without a tiebreak their order was whatever order
    SQLite happened to scan the table in.
    """
    place = snap.path_order()[keep]
    return keep[np.lexsort((place, -composite[keep]))]


# ── ranking ─────────────────────────────────────────────────────────────────


def _candidates(snap: Snapshot, src_path, src_bpm: float, max_bpm_pct: float) -> np.ndarray:
    """Every track but the source — and, with a tempo window, only those in
    it as they are, doubled or halved."""
    mask = np.asarray([p is not None and p != src_path for p in snap.paths], dtype=bool)
    if max_bpm_pct > 0 and src_bpm > 0:
        bpm, has = snap.numbers("bpm")
        f = max_bpm_pct / 100.0
        lo, hi = src_bpm * (1 - f), src_bpm * (1 + f)
        mask &= has & (
            ((bpm >= lo) & (bpm <= hi))
            | ((bpm >= lo / 2) & (bpm <= hi / 2))
            | ((bpm >= lo * 2) & (bpm <= hi * 2))
        )
    return np.flatnonzero(mask)


def rank_axes(
    snap: Snapshot,
    source,
    axes: list[ActiveAxis],
    *,
    max_bpm_pct: float = 0,
    src_bpm_override: float | None = None,
    harmonic_only: bool = False,
) -> Ranked:
    """See :func:`core.matcher.rank_matches_axes`."""
    n = len(snap)
    active = {a.key: a for a in axes if a.strength > 0}
    src_bpm = src_bpm_override or source["bpm"] or 0
    src_path = source["path"]
    src_cam = source["camelot"]

    candidates = _candidates(snap, src_path, src_bpm, max_bpm_pct)

    raw_bpm, has_bpm = snap.numbers("bpm")
    bpm = np.where(has_bpm, raw_bpm, 0.0)
    bs, effective, factor = _best_bpm(float(src_bpm), bpm)
    # The key and BPM scorers return 0 when either side is missing, which is
    # right for the displayed score but wrong for ranking: an unkeyed track
    # is an unknown, not the worst clash in the library. So those two axes
    # are skipped for the candidate, like any other axis with no data.
    bpm_known = (bpm != 0.0) if src_bpm else np.zeros(n, dtype=bool)
    ks = _spread(
        snap,
        "camelot",
        _per_value(snap, "camelot", lambda cam: _score_key(src_cam, cam)),
        np.float64,
    )
    key_known = _spread(
        snap,
        "camelot",
        _per_value(snap, "camelot", lambda cam: _key_distance(src_cam, cam) is not None),
        bool,
    )

    keep = np.zeros(n, dtype=bool)
    keep[candidates] = True
    if harmonic_only:
        # Opt-in hard harmonic filter. Independent of the Key axis's weight:
        # narrowing the candidate set and weighting the ranking are
        # different decisions, so they are different switches.
        keep &= _spread(
            snap,
            "camelot",
            _per_value(snap, "camelot", lambda cam: _harmonic_compatible(src_cam, cam)),
            bool,
        )
    if max_bpm_pct > 0 and src_bpm > 0:
        keep &= np.abs(effective - src_bpm) / src_bpm * 100 <= max_bpm_pct

    transpose: np.ndarray | None = None
    scored: list[_Axis] = []
    similar: dict[str, _Scores] = {}
    weighted, weight = np.zeros(n), np.zeros(n)
    vibe, vibe_weight = np.zeros(n), np.zeros(n)
    for a in active.values():
        spec = BY_KEY[a.key]
        if spec.kind == "bpm":
            score, has = bs, bpm_known
        elif spec.kind == "key":
            if a.max_semitones:
                limit = a.max_semitones
                best = _per_value(
                    snap,
                    "camelot",
                    lambda cam, limit=limit: best_transpose(src_cam, cam, max_semitones=limit),
                )
                transpose = _spread(snap, "camelot", [shift for shift, _s in best], np.int64)
                score = _spread(snap, "camelot", [s for _shift, s in best], np.float64)
            else:
                score = ks
            has = key_known
        elif spec.kind == "similarity":
            embedding = spec.embedding
            model = EMBEDDING_MODELS.get(embedding) if embedding else None
            if embedding is None or model is None:
                score, has = _nothing(n)
            else:
                if embedding not in similar:
                    similar[embedding] = _similarity(snap, model, source, candidates)
                score, has = similar[embedding]
        elif spec.kind == "genre":
            score, has = _genre(snap, source, candidates)
        elif spec.kind == "style":
            score, has = _style(snap, source)
        elif spec.kind == "label":
            score, has = _label(snap, source)
        else:
            score, has = _scalar(snap, spec, a.value)

        scored.append(_Axis(a.key, score, has))
        weighted += np.where(has, score * a.strength, 0.0)
        weight += np.where(has, a.strength, 0.0)
        if spec.kind not in ("bpm", "key"):
            vibe += np.where(has, score * a.strength, 0.0)
            vibe_weight += np.where(has, a.strength, 0.0)

    with np.errstate(divide="ignore", invalid="ignore"):
        composite = np.where(weight > 0, weighted / weight, 50.0)
        vibe_score = np.where(vibe_weight > 0, vibe / vibe_weight, 50.0)

    return Ranked(
        snap.rows,
        _in_order(snap, np.flatnonzero(keep), composite),
        composite=composite,
        bpm_score=bs,
        key_score=ks,
        vibe_score=vibe_score,
        rating_score=_rating_score(snap),
        effective_bpm=effective,
        bpm_factor=factor,
        axes=scored,
        transpose=transpose,
        with_transpose=True,
    )


def rank_absolute(snap: Snapshot, active: list[ActiveAxis]) -> Ranked:
    """See :func:`core.matcher.rank_matches_absolute`. *active* is the axes
    that carry a target of their own."""
    n = len(snap)
    scored: list[_Axis] = []
    weighted, weight = np.zeros(n), np.zeros(n)
    for a in active:
        score, has = _scalar(snap, BY_KEY[a.key], a.value)
        scored.append(_Axis(a.key, score, has))
        weighted += np.where(has, score * a.strength, 0.0)
        weight += np.where(has, a.strength, 0.0)
    # A track with no data on any active axis isn't a bad match, it's an
    # unknown one — the same neutral 50 that rank_axes gives it.
    with np.errstate(divide="ignore", invalid="ignore"):
        composite = np.where(weight > 0, weighted / weight, 50.0)

    raw_bpm, has_bpm = snap.numbers("bpm")
    return Ranked(
        snap.rows,
        _in_order(snap, np.arange(n), composite),
        composite=composite,
        bpm_score=np.zeros(n),
        key_score=np.zeros(n),
        vibe_score=composite,
        rating_score=_rating_score(snap),
        effective_bpm=np.where(has_bpm, raw_bpm, 0.0),
        bpm_factor=np.ones(n, dtype=np.int64),
        axes=scored,
        transpose=None,
        with_transpose=False,
    )
