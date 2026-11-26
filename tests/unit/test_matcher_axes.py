"""Unit tests for axis-driven matching (value + strength model)."""

from __future__ import annotations

import sqlite3

import numpy as np
import pytest

import core.matcher as matcher
from core.features import ActiveAxis

# Columns the matcher reads / SELECT * returns.
_COLS = (
    "path",
    "filename",
    "bpm",
    "camelot",
    "rating",
    "artist",
    "album",
    "title",
    "genre",
    "genres",
    "mood_party",
    "valence",
    "melodic",
    "genres_vector",
)

_TEXT_COLS = ("path", "filename", "camelot", "artist", "album", "title", "genre", "genres")


def _emb(*vals: float) -> bytes:
    return np.array(vals, dtype=np.float32).tobytes()


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """An in-memory-ish temp DB wired into the matcher via get_conn()."""
    path = str(tmp_path / "t.db")

    def _conn() -> sqlite3.Connection:
        c = sqlite3.connect(path)
        c.row_factory = sqlite3.Row
        return c

    with _conn() as c:
        cols = ", ".join(
            f"{name} {'BLOB' if name == 'genres_vector' else 'TEXT' if name in _TEXT_COLS else 'REAL'}"
            for name in _COLS
        )
        c.execute(f"CREATE TABLE tracks ({cols})")
        c.commit()

    monkeypatch.setattr(matcher, "get_conn", _conn)
    return _conn


def _insert(conn, **kw):
    row = {name: kw.get(name) for name in _COLS}
    with conn() as c:
        c.execute(
            f"INSERT INTO tracks ({','.join(_COLS)}) VALUES ({','.join('?' for _ in _COLS)})",
            [row[name] for name in _COLS],
        )
        c.commit()


def _source(conn, path):
    with conn() as c:
        return c.execute("SELECT * FROM tracks WHERE path=?", (path,)).fetchone()


# ── pure scoring helpers ────────────────────────────────────────────────────


def test_cosine_identical_and_orthogonal():
    a = np.array([1.0, 0.0], dtype=np.float32)
    b = np.array([1.0, 0.0], dtype=np.float32)
    c = np.array([0.0, 1.0], dtype=np.float32)
    assert matcher._cosine(a, b) == pytest.approx(1.0)
    assert matcher._cosine(a, c) == pytest.approx(0.0)
    assert matcher._cosine(a, None) is None


def test_score_scalar_peaks_at_target():
    spec = matcher.BY_KEY["party"]
    # Build a real Row via a throwaway connection.
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE t (mood_party REAL)")
    conn.execute("INSERT INTO t VALUES (0.8)")
    r = conn.execute("SELECT * FROM t").fetchone()
    on_target = matcher._score_scalar(spec, 0.8, r)
    off_target = matcher._score_scalar(spec, 0.2, r)
    assert on_target == pytest.approx(100.0)
    assert off_target < on_target


def test_score_scalar_monotonic_at_extreme_target():
    """At an extreme target where every candidate is far, the closer candidate
    must still outscore the farther one (no tie-at-zero from clamping)."""
    spec = matcher.BY_KEY["party"]
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE t (mood_party REAL)")
    conn.execute("INSERT INTO t VALUES (0.6), (0.9)")
    near, far = conn.execute("SELECT * FROM t ORDER BY mood_party").fetchall()
    # Target 0.0 (low extreme): 0.6 is closer than 0.9, so it must score higher.
    s_near = matcher._score_scalar(spec, 0.0, near)
    s_far = matcher._score_scalar(spec, 0.0, far)
    assert s_near > s_far > 0.0


def test_score_key_preserves_the_classic_camelot_values():
    """The four harmonic-mixing scores must not move — relaxing the filter
    should not re-rank keys that were already compatible."""
    assert matcher._score_key("8A", "8A") == 100.0  # same key
    assert matcher._score_key("8A", "9A") == 90.0  # +1 on the wheel
    assert matcher._score_key("8A", "8B") == 75.0  # relative major/minor
    assert matcher._score_key("8A", "9B") == 50.0  # +1, mode change


def test_score_key_grades_distant_keys_monotonically():
    """Beyond +-1 the old scorer collapsed everything to 0, so with the hard
    filter off the axis could not discriminate 18 of the 24 slots."""
    same = [matcher._score_key("8A", f"{(8 + d - 1) % 12 + 1}A") for d in range(7)]
    assert same == sorted(same, reverse=True)
    assert all(x > 0.0 for x in same)
    cross = [matcher._score_key("8A", f"{(8 + d - 1) % 12 + 1}B") for d in range(7)]
    assert cross == sorted(cross, reverse=True)
    assert all(x > 0.0 for x in cross)
    # A distant key still ranks below every classically-compatible one.
    assert matcher._score_key("8A", "2A") < matcher._score_key("8A", "9B")


def test_score_key_unknown_camelot_is_zero():
    assert matcher._score_key("", "8A") == 0.0
    assert matcher._score_key("8A", None) == 0.0
    assert matcher._score_key("8A", "nonsense") == 0.0


def test_harmonic_compatible_matches_the_classic_set():
    assert matcher._harmonic_compatible("8A", "8A")
    assert matcher._harmonic_compatible("8A", "9B")
    assert not matcher._harmonic_compatible("8A", "2B")
    assert not matcher._harmonic_compatible("8A", "")


def test_score_scalar_missing_data_returns_none():
    spec = matcher.BY_KEY["party"]
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE t (mood_party REAL)")
    conn.execute("INSERT INTO t VALUES (NULL)")
    r = conn.execute("SELECT * FROM t").fetchone()
    assert matcher._score_scalar(spec, 0.5, r) is None


# ── rank_matches_axes ───────────────────────────────────────────────────────


def test_party_target_ranks_closest_first(db):
    _insert(db, path="src", filename="s", bpm=124, camelot="8A", mood_party=0.5)
    _insert(db, path="hi", filename="h", bpm=124, camelot="8A", mood_party=0.95)
    _insert(db, path="lo", filename="l", bpm=124, camelot="8A", mood_party=0.10)
    src = _source(db, "src")

    res = matcher.rank_matches_axes(src, [ActiveAxis("party", value=0.95, strength=1.0)])
    assert [r["row"]["path"] for r in res] == ["hi", "lo"]
    assert res[0]["axis_scores"]["party"] == pytest.approx(100.0)


def test_strength_zero_axis_is_ignored(db):
    _insert(db, path="src", filename="s", bpm=124, camelot="8A", mood_party=0.5)
    _insert(db, path="a", filename="a", bpm=124, camelot="8A", mood_party=0.95)
    src = _source(db, "src")
    res = matcher.rank_matches_axes(src, [ActiveAxis("party", value=0.95, strength=0.0)])
    # No active axes → composite falls back to rating (neutral) and party isn't scored.
    assert "party" not in res[0]["axis_scores"]


def test_similarity_orders_by_embedding(db):
    e_src = _emb(1.0, 0.0, 0.0)
    _insert(db, path="src", filename="s", bpm=124, camelot="8A", genres_vector=e_src)
    _insert(
        db, path="near", filename="n", bpm=124, camelot="8A", genres_vector=_emb(0.96, 0.28, 0.0)
    )
    _insert(db, path="far", filename="f", bpm=124, camelot="8A", genres_vector=_emb(0.0, 1.0, 0.0))
    src = _source(db, "src")
    res = matcher.rank_matches_axes(src, [ActiveAxis("similarity", strength=1.0)])
    assert [r["row"]["path"] for r in res] == ["near", "far"]


def test_key_axis_alone_no_longer_excludes(db):
    """The Key axis is a ranking weight, not a gate — relaxing it is the whole
    point of ``harmonic_only`` being opt-in. A distant key must still appear,
    just ranked below a compatible one."""
    _insert(db, path="src", filename="s", bpm=124, camelot="8A")
    _insert(db, path="compat", filename="c", bpm=124, camelot="8A")
    _insert(db, path="clash", filename="x", bpm=124, camelot="2B")
    src = _source(db, "src")
    res = matcher.rank_matches_axes(src, [ActiveAxis("key", strength=1.0)])
    paths = [r["row"]["path"] for r in res]
    assert paths == ["compat", "clash"]


def test_harmonic_only_excludes_incompatible(db):
    _insert(db, path="src", filename="s", bpm=124, camelot="8A")
    _insert(db, path="compat", filename="c", bpm=124, camelot="8A")
    _insert(db, path="clash", filename="x", bpm=124, camelot="2B")
    src = _source(db, "src")
    res = matcher.rank_matches_axes(
        src,
        [ActiveAxis("key", strength=1.0)],
        harmonic_only=True,
    )
    paths = [r["row"]["path"] for r in res]
    assert "compat" in paths and "clash" not in paths


def test_harmonic_only_applies_without_a_key_axis(db):
    """The filter is now independent of the axis: it must bite even when Key
    carries no weight (previously the two were the same switch)."""
    _insert(db, path="src", filename="s", bpm=124, camelot="8A", mood_party=0.5)
    _insert(db, path="clash", filename="x", bpm=124, camelot="2B", mood_party=0.5)
    src = _source(db, "src")
    res = matcher.rank_matches_axes(
        src,
        [ActiveAxis("party", value=0.5, strength=1.0)],
        harmonic_only=True,
    )
    assert [r["row"]["path"] for r in res] == []


def test_harmonic_only_keeps_exactly_the_camelot_six(db):
    _insert(db, path="src", filename="s", bpm=124, camelot="8A")
    for n in range(1, 13):
        for mode in ("A", "B"):
            cam = f"{n}{mode}"
            if cam == "8A":
                continue
            _insert(db, path=cam, filename=cam, bpm=124, camelot=cam)
    src = _source(db, "src")
    res = matcher.rank_matches_axes(
        src,
        [ActiveAxis("key", strength=1.0)],
        harmonic_only=True,
    )
    assert sorted(r["row"]["path"] for r in res) == sorted(["7A", "9A", "7B", "8B", "9B"])


def test_no_key_axis_keeps_all_keys(db):
    _insert(db, path="src", filename="s", bpm=124, camelot="8A", mood_party=0.5)
    _insert(db, path="clash", filename="x", bpm=124, camelot="2B", mood_party=0.5)
    src = _source(db, "src")
    res = matcher.rank_matches_axes(src, [ActiveAxis("party", value=0.5, strength=1.0)])
    assert "clash" in [r["row"]["path"] for r in res]


def test_genre_axis_ranks_same_genre_first(db):
    _insert(db, path="src", filename="s", bpm=124, camelot="8A", genre="Deep House")
    _insert(db, path="same", filename="a", bpm=124, camelot="8A", genre="Deep House")
    _insert(db, path="partial", filename="b", bpm=124, camelot="8A", genre="Tech House")
    _insert(db, path="other", filename="c", bpm=124, camelot="8A", genre="Drum & Bass")
    src = _source(db, "src")
    res = matcher.rank_matches_axes(src, [ActiveAxis("genre", strength=1.0)])
    paths = [r["row"]["path"] for r in res]
    assert paths[0] == "same"
    assert paths.index("partial") < paths.index("other")
    assert res[0]["axis_scores"]["genre"] == pytest.approx(100.0)


def test_genre_axis_skips_candidate_without_genre(db):
    _insert(db, path="src", filename="s", bpm=124, camelot="8A", genre="Techno")
    _insert(db, path="nogenre", filename="a", bpm=124, camelot="8A", genre=None)
    src = _source(db, "src")
    res = matcher.rank_matches_axes(src, [ActiveAxis("genre", strength=1.0)])
    nogenre = next(r for r in res if r["row"]["path"] == "nogenre")
    assert "genre" not in nogenre["axis_scores"]


def test_style_axis_orders_by_detected_genre_vector(db):
    import json

    def g(*p):
        return json.dumps([{"name": n, "prob": v} for n, v in p])

    _insert(
        db,
        path="src",
        filename="s",
        bpm=124,
        camelot="8A",
        genres=g(("techno", 0.7), ("acid", 0.3)),
    )
    _insert(
        db,
        path="near",
        filename="a",
        bpm=124,
        camelot="8A",
        genres=g(("techno", 0.6), ("acid", 0.4)),
    )
    _insert(db, path="far", filename="b", bpm=124, camelot="8A", genres=g(("funk", 0.9)))
    src = _source(db, "src")
    res = matcher.rank_matches_axes(src, [ActiveAxis("style", strength=1.0)])
    assert [r["row"]["path"] for r in res] == ["near", "far"]


def test_results_carry_table_keys(db):
    _insert(db, path="src", filename="s", bpm=124, camelot="8A", mood_party=0.5, rating=4)
    _insert(db, path="a", filename="a", bpm=124, camelot="8A", mood_party=0.6, rating=5)
    src = _source(db, "src")
    res = matcher.rank_matches_axes(src, [ActiveAxis("party", value=0.5, strength=1.0)])
    r = res[0]
    for key in (
        "row",
        "composite",
        "bpm_score",
        "key_score",
        "vibe_score",
        "rating_score",
        "effective_bpm",
        "bpm_factor",
        "axis_scores",
    ):
        assert key in r


# ── rank_matches_absolute (no source track) ─────────────────────────────────


def test_absolute_ranks_by_scalar_target_without_a_source(db):
    _insert(db, path="hi", filename="h", bpm=124, camelot="8A", mood_party=0.95)
    _insert(db, path="mid", filename="m", bpm=90, camelot="2B", mood_party=0.55)
    _insert(db, path="lo", filename="l", bpm=124, camelot="8A", mood_party=0.10)

    res = matcher.rank_matches_absolute([ActiveAxis("party", value=0.95, strength=1.0)])
    assert [r["row"]["path"] for r in res] == ["hi", "mid", "lo"]
    assert res[0]["axis_scores"]["party"] == pytest.approx(100.0)
    assert res[0]["composite"] == pytest.approx(100.0)


def test_absolute_ignores_relational_axes(db):
    """BPM / Key / Similarity / Genre / Style compare against the current track,
    so with no current track they have nothing to say."""
    _insert(
        db,
        path="a",
        filename="a",
        bpm=124,
        camelot="8A",
        genre="Techno",
        genres_vector=_emb(1.0, 0.0),
    )
    _insert(
        db, path="b", filename="b", bpm=90, camelot="2B", genre="Funk", genres_vector=_emb(0.0, 1.0)
    )

    for key in ("bpm", "key", "similarity", "genre"):
        assert matcher.rank_matches_absolute([ActiveAxis(key, strength=1.0)]) == []


def test_absolute_ignores_axes_synced_to_the_current_track(db):
    """A synced axis's value is a copy of the (now absent) current track's —
    stale, so it must not steer the ranking."""
    _insert(db, path="a", filename="a", mood_party=0.9)
    _insert(db, path="b", filename="b", mood_party=0.1)

    synced = ActiveAxis("party", value=0.9, strength=1.0, sync=True)
    assert matcher.rank_matches_absolute([synced]) == []
    # …while the same axis un-synced does rank.
    assert matcher.rank_matches_absolute([ActiveAxis("party", value=0.9, strength=1.0)])


def test_absolute_returns_empty_when_no_absolute_axis_is_active(db):
    _insert(db, path="a", filename="a", mood_party=0.9)
    assert matcher.rank_matches_absolute([]) == []
    assert matcher.rank_matches_absolute([ActiveAxis("party", value=0.9, strength=0.0)]) == []


def test_absolute_weights_axes_by_strength(db):
    _insert(db, path="party", filename="p", mood_party=1.0, rating=1)
    _insert(db, path="rated", filename="r", mood_party=0.0, rating=5)
    axes = [
        ActiveAxis("party", value=1.0, strength=1.0),
        ActiveAxis("rating", value=1.0, strength=0.1),
    ]
    assert [r["row"]["path"] for r in matcher.rank_matches_absolute(axes)][0] == "party"
    axes = [
        ActiveAxis("party", value=1.0, strength=0.1),
        ActiveAxis("rating", value=1.0, strength=1.0),
    ]
    assert [r["row"]["path"] for r in matcher.rank_matches_absolute(axes)][0] == "rated"


def test_absolute_keeps_tracks_without_data_at_neutral(db):
    _insert(db, path="known", filename="k", mood_party=0.95)
    _insert(db, path="unknown", filename="u", mood_party=None)
    res = matcher.rank_matches_absolute([ActiveAxis("party", value=0.95, strength=1.0)])
    paths = [r["row"]["path"] for r in res]
    assert paths == ["known", "unknown"]
    unknown = res[1]
    assert unknown["axis_scores"] == {}
    assert unknown["composite"] == pytest.approx(50.0)


def test_absolute_results_carry_table_keys(db):
    _insert(db, path="a", filename="a", bpm=124, camelot="8A", mood_party=0.6, rating=5)
    res = matcher.rank_matches_absolute([ActiveAxis("party", value=0.5, strength=1.0)])
    for key in (
        "row",
        "composite",
        "bpm_score",
        "key_score",
        "vibe_score",
        "rating_score",
        "effective_bpm",
        "bpm_factor",
        "axis_scores",
    ):
        assert key in res[0]


# ── unknown key / BPM is skipped, not scored as the worst match ─────────────

_BPM_KEY = [ActiveAxis("bpm", strength=1.0), ActiveAxis("key", strength=1.0)]


def test_candidate_without_a_key_ranks_on_the_other_axes(db):
    """A track nobody has keyed yet is an unknown, not a clash. Scoring its
    key as 0 put it below even the most distant real key."""
    _insert(db, path="src", filename="s", bpm=124, camelot="8A")
    _insert(db, path="nokey", filename="n", bpm=124, camelot=None)
    _insert(db, path="far", filename="f", bpm=124, camelot="2B")
    src = _source(db, "src")
    res = matcher.rank_matches_axes(src, _BPM_KEY)
    assert [r["row"]["path"] for r in res] == ["nokey", "far"]
    nokey = res[0]
    assert "key" not in nokey["axis_scores"]
    assert nokey["composite"] == pytest.approx(100.0)
    # The displayed Key score keeps reading 0 for "unknown", as before.
    assert nokey["key_score"] == 0.0


def test_candidate_with_an_unreadable_key_is_skipped_on_the_transpose_axis(db):
    _insert(db, path="src", filename="s", bpm=124, camelot="8A")
    _insert(db, path="junk", filename="j", bpm=124, camelot="nonsense")
    src = _source(db, "src")
    res = matcher.rank_matches_axes(
        src, [ActiveAxis("bpm", strength=1.0), ActiveAxis("key", strength=1.0, max_semitones=3)]
    )
    assert "key" not in res[0]["axis_scores"]
    assert res[0]["composite"] == pytest.approx(100.0)


def test_source_without_a_key_does_not_cap_every_composite_at_half(db):
    """With no key on the source, every candidate used to score 0 on Key, so
    with BPM and Key both active no composite could exceed 50."""
    _insert(db, path="src", filename="s", bpm=124, camelot=None)
    _insert(db, path="same", filename="a", bpm=124, camelot="8A")
    _insert(db, path="slow", filename="b", bpm=100, camelot="8A")
    src = _source(db, "src")
    res = matcher.rank_matches_axes(src, _BPM_KEY)
    assert [r["row"]["path"] for r in res] == ["same", "slow"]
    assert res[0]["composite"] == pytest.approx(100.0)
    assert all("key" not in r["axis_scores"] for r in res)


def test_candidate_without_a_bpm_ranks_on_the_other_axes(db):
    _insert(db, path="src", filename="s", bpm=124, camelot="8A")
    _insert(db, path="nobpm", filename="n", bpm=None, camelot="8A")
    _insert(db, path="slow", filename="x", bpm=90, camelot="8A")
    src = _source(db, "src")
    res = matcher.rank_matches_axes(src, _BPM_KEY)
    assert [r["row"]["path"] for r in res] == ["nobpm", "slow"]
    assert "bpm" not in res[0]["axis_scores"]
    assert res[0]["composite"] == pytest.approx(100.0)
    assert res[0]["bpm_score"] == 0.0


def test_source_without_a_bpm_skips_the_bpm_axis(db):
    _insert(db, path="src", filename="s", bpm=None, camelot="8A")
    _insert(db, path="a", filename="a", bpm=124, camelot="8A")
    src = _source(db, "src")
    res = matcher.rank_matches_axes(src, _BPM_KEY)
    assert "bpm" not in res[0]["axis_scores"]
    assert res[0]["composite"] == pytest.approx(100.0)


def test_bpm_filter_still_drops_candidates_without_a_bpm(db):
    """Skipping the axis is about ranking; the hard BPM window is unchanged
    and still has nothing to measure a BPM-less track against."""
    _insert(db, path="src", filename="s", bpm=124, camelot="8A")
    _insert(db, path="nobpm", filename="n", bpm=None, camelot="8A")
    _insert(db, path="near", filename="x", bpm=125, camelot="8A")
    src = _source(db, "src")
    res = matcher.rank_matches_axes(src, _BPM_KEY, max_bpm_pct=8)
    assert [r["row"]["path"] for r in res] == ["near"]


def test_harmonic_only_still_drops_candidates_without_a_key(db):
    _insert(db, path="src", filename="s", bpm=124, camelot="8A")
    _insert(db, path="nokey", filename="n", bpm=124, camelot=None)
    src = _source(db, "src")
    res = matcher.rank_matches_axes(src, _BPM_KEY, harmonic_only=True)
    assert res == []


# ── the CLAP blob is never part of a result's row ───────────────────────────
# It used to ride along in every row whenever the similarity axis was on,
# because that axis read it from there. The vectors are read once per library
# now (core.library_snapshot), so a row never needs to carry one.


def test_rows_leave_out_the_clap_blob_when_no_similarity_axis_is_on(db):
    """The vector is ~2 KB a track and nothing but the similarity axis reads
    it, yet every re-rank used to pull it for the whole library."""
    _insert(
        db,
        path="src",
        filename="s",
        bpm=124,
        camelot="8A",
        artist="A",
        genres_vector=_emb(1.0, 0.0),
    )
    _insert(
        db,
        path="a",
        filename="a",
        bpm=124,
        camelot="8A",
        artist="B",
        genre="Techno",
        rating=4,
        mood_party=0.5,
        genres_vector=_emb(0.0, 1.0),
    )
    src = _source(db, "src")
    res = matcher.rank_matches_axes(src, [ActiveAxis("party", value=0.5, strength=1.0)])
    row = res[0]["row"]
    assert "genres_vector" not in row.keys()
    # Everything the results table shows is still there.
    assert set(row.keys()) == set(_COLS) - {"genres_vector"}
    assert row["artist"] == "B" and row["genre"] == "Techno" and row["rating"] == 4


def test_similarity_scores_without_the_rows_carrying_the_blob(db):
    _insert(db, path="src", filename="s", bpm=124, camelot="8A", genres_vector=_emb(1.0, 0.0))
    _insert(db, path="a", filename="a", bpm=124, camelot="8A", genres_vector=_emb(1.0, 0.1))
    src = _source(db, "src")
    res = matcher.rank_matches_axes(src, [ActiveAxis("similarity", strength=1.0)])
    assert "similarity" in res[0]["axis_scores"]
    assert "genres_vector" not in res[0]["row"].keys()


def test_absolute_rows_leave_out_the_clap_blob(db):
    _insert(db, path="a", filename="a", mood_party=0.9, genres_vector=_emb(1.0, 0.0))
    res = matcher.rank_matches_absolute([ActiveAxis("party", value=0.9, strength=1.0)])
    assert set(res[0]["row"].keys()) == set(_COLS) - {"genres_vector"}


# ── ties break on path, not on table scan order ─────────────────────────────


def test_axes_ties_break_on_path(db):
    """Rating-only ranking gives a handful of distinct scores across a whole
    library, so most rows tie; their order must not depend on insert order."""
    _insert(db, path="src", filename="s", bpm=124, camelot="8A")
    for p in ("c", "a", "b"):
        _insert(db, path=p, filename=p, bpm=124, camelot="8A", rating=4)
    _insert(db, path="top", filename="t", bpm=124, camelot="8A", rating=5)
    src = _source(db, "src")
    res = matcher.rank_matches_axes(src, [ActiveAxis("rating", value=1.0, strength=1.0)])
    assert [r["row"]["path"] for r in res] == ["top", "a", "b", "c"]


def test_absolute_ties_break_on_path(db):
    for p in ("c", "a", "b"):
        _insert(db, path=p, filename=p, rating=4)
    _insert(db, path="top", filename="t", rating=5)
    res = matcher.rank_matches_absolute([ActiveAxis("rating", value=1.0, strength=1.0)])
    assert [r["row"]["path"] for r in res] == ["top", "a", "b", "c"]
