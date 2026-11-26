"""Unit tests for library feature statistics."""

from __future__ import annotations

import dataclasses
import sqlite3

import numpy as np
import pytest

import core.stats as stats


def _conn_with(rows: list[dict]):
    """In-memory DB with a tracks table holding the given feature rows."""
    cols = [f.column for f in stats.NUMERIC_FEATURES]
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute(f"CREATE TABLE tracks ({', '.join(col + ' REAL' for col in cols)})")
    for r in rows:
        c.execute(
            f"INSERT INTO tracks ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
            [r.get(col) for col in cols],
        )
    c.commit()
    return c


def test_load_and_stats_basic(monkeypatch):
    rows = [{"mood_party": 0.2}, {"mood_party": 0.8}, {"mood_party": 0.5}]
    monkeypatch.setattr(stats, "get_conn", lambda: _conn_with(rows))
    vals = stats.load_feature_values()
    assert vals["party"].shape == (3,)
    st = {s.key: s for s in stats.feature_stats(vals)}
    assert st["party"].n == 3
    assert st["party"].mean == pytest.approx(0.5)
    assert st["party"].min == pytest.approx(0.2)
    assert st["party"].max == pytest.approx(0.8)
    assert len(st["party"].counts) == 24
    assert len(st["party"].edges) == 25


def test_constant_feature_has_zero_spread(monkeypatch):
    rows = [{"mood_party": 0.7} for _ in range(20)]
    monkeypatch.setattr(stats, "get_conn", lambda: _conn_with(rows))
    st = {s.key: s for s in stats.feature_stats()}
    assert st["party"].spread == pytest.approx(0.0)
    assert st["party"].spread_score == pytest.approx(0.0)


def test_spread_score_higher_for_more_varied(monkeypatch):
    spread_rows = [{"mood_party": v} for v in np.linspace(0, 1, 50)]
    tight_rows = [{"mood_party": v} for v in np.linspace(0.45, 0.55, 50)]
    monkeypatch.setattr(stats, "get_conn", lambda: _conn_with(spread_rows))
    wide = {s.key: s for s in stats.feature_stats()}["party"].spread_score
    monkeypatch.setattr(stats, "get_conn", lambda: _conn_with(tight_rows))
    narrow = {s.key: s for s in stats.feature_stats()}["party"].spread_score
    assert wide > narrow
    assert wide > 80  # ~uniform spread approaches 100


def test_arbitrary_scale_feature_measured_on_observed_range(monkeypatch):
    """A signal feature behind a saturating gain (dynamic_complexity ×10) must
    still show its real spread — measured on the observed range, not the gain's
    clamped output where every track collapses to 1.0."""
    rows = [{"dynamic_complexity": v} for v in np.linspace(0.05, 0.25, 50)]
    monkeypatch.setattr(stats, "get_conn", lambda: _conn_with(rows))
    st = {s.key: s for s in stats.feature_stats()}["dynamics"]
    # ×10 gain would clamp all of these to 1.0 → spread 0; observed-range sees
    # the full spread instead.
    assert st.spread_score > 50


def test_saturated_probability_reads_low_spread(monkeypatch):
    """A CLAP probability where nearly every track sits at one extreme is a weak
    discriminator — its absolute [0,1] scale is meaningful, so it stays low."""
    rows = [{"mood_aggressive": 0.99} for _ in range(48)] + [
        {"mood_aggressive": 0.6},
        {"mood_aggressive": 0.7},
    ]
    monkeypatch.setattr(stats, "get_conn", lambda: _conn_with(rows))
    st = {s.key: s for s in stats.feature_stats()}["aggressive"]
    assert st.spread_score < 15


def test_missing_values_are_ignored(monkeypatch):
    rows = [{"mood_party": 0.3}, {"mood_party": None}, {"mood_party": 0.9}]
    monkeypatch.setattr(stats, "get_conn", lambda: _conn_with(rows))
    st = {s.key: s for s in stats.feature_stats()}
    assert st["party"].n == 2


def test_correlation_detects_redundant_pair(monkeypatch):
    # mood_party and mood_happy perfectly correlated; valence anti-correlated.
    base = np.linspace(0.1, 0.9, 40)
    rows = [{"mood_party": p, "mood_happy": p, "valence": 9 - 8 * p} for p in base]
    monkeypatch.setattr(stats, "get_conn", lambda: _conn_with(rows))
    corr = stats.correlation(threshold=0.85)
    assert corr.n == 40
    pairs = {frozenset((a, b)): r for a, b, r in corr.redundant}
    assert frozenset(("party", "happy")) in pairs
    assert pairs[frozenset(("party", "happy"))] == pytest.approx(1.0, abs=1e-6)
    # party vs valence is a strong negative correlation → also flagged
    assert pairs[frozenset(("party", "valence"))] < -0.95


def test_correlation_matrix_shape(monkeypatch):
    rows = [{"mood_party": p, "valence": 1 + 8 * p} for p in np.linspace(0, 1, 10)]
    monkeypatch.setattr(stats, "get_conn", lambda: _conn_with(rows))
    corr = stats.correlation()
    k = len(corr.keys)
    assert len(corr.matrix) == k and all(len(row) == k for row in corr.matrix)
    # diagonal is 1
    for i in range(k):
        assert corr.matrix[i][i] == pytest.approx(1.0)


def test_a_numeric_feature_without_a_column_is_refused(monkeypatch):
    columnless = dataclasses.replace(stats.NUMERIC_FEATURES[0], column=None)
    monkeypatch.setattr(stats, "NUMERIC_FEATURES", [columnless])
    with pytest.raises(ValueError, match="has no tracks column"):
        stats.load_feature_values()
