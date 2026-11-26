"""Library feature statistics — distributions, spread, and correlations.

Qt-free so it can be unit-tested; the dashboard (``ui/stats.py``) renders these
results with QPainter. All computation is plain NumPy over the analysed tracks.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

import numpy as np

from core.db import get_conn
from core.features import BY_KEY, FEATURES, FeatureSpec

# Numeric, plottable features — excludes the categorical Key and the relational
# Similarity axis (which has no per-track scalar value).
NUMERIC_FEATURES: list[FeatureSpec] = [f for f in FEATURES if f.kind in ("scalar", "bpm")]

_UNIT = {
    "valence": "1–9",
    "arousal": "1–9",
    "bpm": "BPM",
    "rating": "0–5",
    "spectral_centroid": "0–1",
    "dynamic_complexity": "RMS σ",
    "danceability": "ACF",
    "year_effective": "year",
    "import_day": "date",
}
# IQR of a uniform distribution on [0,1] — the yardstick for "fully spread".
# Spread is measured as the interquartile range (robust to the long tails that
# inflate std on the skewed CLAP-probability features), scaled so a uniform
# library reads 100.
_UNIFORM_IQR = 0.5


def _unit_for(spec: FeatureSpec) -> str:
    return _UNIT.get(spec.column or "", "0–1")


@dataclass(frozen=True)
class FeatureStats:
    key: str
    label: str
    short: str
    hue: int
    unit: str
    n: int
    mean: float
    median: float
    std: float
    min: float
    max: float
    p25: float
    p75: float
    counts: list  # histogram bin counts
    edges: list  # histogram bin edges (len = counts+1)
    spread: float  # std of values normalised to the matcher's [0,1] space
    spread_score: float  # 0–100: discriminative power (100 = maximally spread)


@dataclass(frozen=True)
class Correlation:
    keys: list
    labels: list
    matrix: list  # NxN Pearson r
    n: int  # tracks used (complete rows)
    redundant: list  # (keyA, keyB, r) pairs with |r| >= threshold, |r| desc


#: Slots on the Camelot wheel — 12 numbers, major and minor.
_WHEEL = 24


#: The wheel in the order a DJ reads it: all the minor keys, then the major.
CAMELOT_SLOTS: tuple = tuple(f"{n}{ab}" for ab in ("A", "B") for n in range(1, 13))


def key_distribution(camelots: list) -> list:
    """``[(slot, count), …]`` for all 24 Camelot slots, in wheel order.

    Every slot is listed even when empty — the gaps are the interesting part,
    since they are the keys a set cannot move into.
    """
    counts = dict.fromkeys(CAMELOT_SLOTS, 0)
    for value in camelots:
        if not value:
            continue
        slot = str(value).strip().upper()
        if slot in counts:
            counts[slot] += 1
    return [(slot, counts[slot]) for slot in CAMELOT_SLOTS]


def key_spread(camelots: list) -> float:
    """0–100: how evenly a library uses the Camelot wheel.

    Key is categorical, so the interquartile range every other feature uses
    means nothing here — the distance from 12A to 1A is one step, not eleven.
    Normalised entropy answers the question the bar is actually asking: a
    crate that is all one key cannot be matched on key at all, one spread
    over the wheel can.
    """
    import math

    counts: dict[str, int] = {}
    for value in camelots:
        if value:
            counts[str(value)] = counts.get(str(value), 0) + 1
    total = sum(counts.values())
    if total == 0 or len(counts) < 2:
        return 0.0
    entropy = -sum((n / total) * math.log(n / total) for n in counts.values())
    return min(100.0, entropy / math.log(_WHEEL) * 100.0)


def _numeric_columns() -> list[str]:
    """The tracks-table column of every stored numeric feature, in feature
    order. Derived ones (the release year, the import day) are not columns of
    the table; :func:`_derived_values` fills them in."""
    columns: list[str] = []
    for f in NUMERIC_FEATURES:
        if f.column is None:
            raise ValueError(f"numeric feature {f.key!r} has no tracks column")
        if not f.derived:
            columns.append(f.column)
    return columns


def _select_tracks(columns: list[str], conn: sqlite3.Connection | None) -> tuple[list, bool]:
    """The rows, and whether each ends with the track's path (added when a
    derived feature needs lining up and the table has one)."""
    own = conn is None
    conn = conn or get_conn()
    try:
        have = {row[1] for row in conn.execute("PRAGMA table_info(tracks)")}
        # The path lines the derived values up with these rows.
        extra = ["path"] if "path" in have and any(f.derived for f in NUMERIC_FEATURES) else []
        rows = conn.execute(f"SELECT {', '.join([*columns, *extra])} FROM tracks").fetchall()
        return rows, bool(extra)
    finally:
        if own:
            conn.close()


def _derived_values(rows: list, path_at: int | None) -> dict:
    """``{feature key: array}`` for the derived numeric features, in the order
    of *rows* (whose column *path_at* holds the path); NaN where unknown.

    Worked out through the library snapshot — the rule the matcher applies,
    Discogs match included."""
    derived = [f for f in NUMERIC_FEATURES if f.derived]
    if not derived:
        return {}
    nan = {f.key: np.full(len(rows), np.nan) for f in derived}
    if path_at is None or not rows:
        return nan
    from core.library_snapshot import snapshot

    snap = snapshot(get_conn)
    for f in derived:
        raw = snap.raw(f.column or "")
        out = nan[f.key]
        for i, row in enumerate(rows):
            j = snap.index.get(row[path_at])
            if j is not None and raw[j] is not None:
                out[i] = float(raw[j])
    return nan


def _values_from_rows(rows: list, has_path: bool) -> dict:
    stored = [f for f in NUMERIC_FEATURES if not f.derived]
    out: dict = {}
    for i, f in enumerate(stored):
        out[f.key] = np.array(
            [(row[i] if row[i] is not None else np.nan) for row in rows], dtype=float
        )
    out.update(_derived_values(rows, len(stored) if has_path else None))
    return {f.key: out[f.key] for f in NUMERIC_FEATURES}


def load_feature_values(conn: sqlite3.Connection | None = None) -> dict:
    """Return {feature_key: np.ndarray} of raw values (NaN for missing)."""
    return _values_from_rows(*_select_tracks(_numeric_columns(), conn))


def load_library_values(conn: sqlite3.Connection | None = None) -> tuple[dict, list]:
    """``(feature values, camelot keys)`` in one pass over the tracks table.

    What the Stats page needs: the numeric features as
    :func:`load_feature_values` returns them, plus every track's Camelot key
    for the key wheel. It used to read the whole table three times for these.
    """
    columns = _numeric_columns()
    rows, has_path = _select_tracks([*columns, "camelot"], conn)
    camelots = [row[len(columns)] for row in rows]
    # Drop the camelot column so the path (if any) follows the features.
    trimmed = [(*row[: len(columns)], *row[len(columns) + 1 :]) for row in rows]
    return _values_from_rows(trimmed, has_path), camelots


def _normalized(spec: FeatureSpec, raw: np.ndarray) -> np.ndarray:
    """Map a feature's present values into [0,1] for comparable spread.

    Features on a *semantically meaningful* [0,1] scale (CLAP probabilities,
    DEAM, rating) use the matcher's own normaliser — so a library where every
    track sits at 0.9 reads as genuinely low spread.

    Features on an *arbitrary* unit (``absolute_scale=False``, e.g. the
    loosely-bounded signal features behind a hand-tuned gain) — and any feature
    with no normaliser (BPM) — are scaled to their observed *robust* range
    (2nd–98th percentile). This keeps a mis-calibrated gain that saturates every
    track to one end from hiding the feature's real spread.
    """
    v = raw[~np.isnan(raw)]
    if v.size == 0:
        return v
    if spec.normalize is not None and spec.absolute_scale:
        return np.array([spec.normalize(float(x)) for x in v])
    lo, hi = float(np.percentile(v, 2)), float(np.percentile(v, 98))
    if hi <= lo:
        return np.zeros_like(v)
    return np.clip((v - lo) / (hi - lo), 0.0, 1.0)


def feature_stats(values: dict | None = None) -> list:
    """Per-feature distribution + spread, ordered by the registry."""
    values = values if values is not None else load_feature_values()
    out: list = []
    for f in NUMERIC_FEATURES:
        raw = values.get(f.key)
        if raw is None:
            continue
        v = raw[~np.isnan(raw)]
        if v.size == 0:
            continue
        counts, edges = np.histogram(v, bins=24)
        norm = _normalized(f, raw)
        # Spread = interquartile range of the normalised values. IQR ignores the
        # long tails that make std over-rank skewed features (a CLAP probability
        # spiked at 0 with a few outliers), so the bar reflects how much the bulk
        # of the crate actually differs — i.e. how well the feature separates it.
        if norm.size:
            spread = float(np.percentile(norm, 75) - np.percentile(norm, 25))
        else:
            spread = 0.0
        out.append(
            FeatureStats(
                key=f.key,
                label=f.label,
                short=f.short_label,
                hue=f.hue,
                unit=_unit_for(f),
                n=int(v.size),
                mean=float(v.mean()),
                median=float(np.median(v)),
                std=float(v.std()),
                min=float(v.min()),
                max=float(v.max()),
                p25=float(np.percentile(v, 25)),
                p75=float(np.percentile(v, 75)),
                counts=counts.tolist(),
                edges=[float(e) for e in edges],
                spread=spread,
                spread_score=min(100.0, spread / _UNIFORM_IQR * 100.0),
            )
        )
    return out


def correlation(values: dict | None = None, threshold: float = 0.85) -> Correlation:
    """Pearson correlation across numeric features (complete rows only)."""
    values = values if values is not None else load_feature_values()
    keys = [f.key for f in NUMERIC_FEATURES if f.key in values]
    if len(keys) < 2:
        return Correlation(
            keys=keys,
            labels=[BY_KEY[k].short_label for k in keys],
            matrix=[[1.0]] * len(keys),
            n=0,
            redundant=[],
        )

    k = len(keys)
    cm = np.eye(k)
    redundant = []
    for i in range(k):
        for j in range(i + 1, k):
            a, b = values[keys[i]], values[keys[j]]
            # Pairwise-complete: use tracks where BOTH features are present.
            mask = ~np.isnan(a) & ~np.isnan(b)
            if mask.sum() >= 3:
                ai, bi = a[mask], b[mask]
                r = float(np.corrcoef(ai, bi)[0, 1]) if ai.std() > 0 and bi.std() > 0 else 0.0
            else:
                r = 0.0
            cm[i, j] = cm[j, i] = r
            if abs(r) >= threshold:
                redundant.append((keys[i], keys[j], r))
    redundant.sort(key=lambda t: -abs(t[2]))
    n = int(values[keys[0]].shape[0]) if values[keys[0]].size else 0
    return Correlation(
        keys=keys,
        labels=[BY_KEY[k2].short_label for k2 in keys],
        matrix=cm.tolist(),
        n=n,
        redundant=redundant,
    )
