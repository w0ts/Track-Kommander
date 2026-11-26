"""Unit tests for core.embeddings — the pluggable similarity-vector registry."""

from __future__ import annotations

import numpy as np
import pytest

from core import embeddings as emb
from core.embeddings import EMBEDDING_MODELS, EmbeddingModel
from core.features import FEATURES

# ── cosine / score_similarity ─────────────────────────────────────────────────


def test_cosine_identical_orthogonal_and_none():
    a = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    b = np.array([2.0, 0.0, 0.0], dtype=np.float32)  # parallel, different length
    c = np.array([0.0, 1.0, 0.0], dtype=np.float32)
    assert emb.cosine(a, b) == pytest.approx(1.0)
    assert emb.cosine(a, c) == pytest.approx(0.0)
    assert emb.cosine(a, None) is None
    assert emb.cosine(a, np.array([], dtype=np.float32)) is None
    assert emb.cosine(a, np.array([1.0, 0.0], dtype=np.float32)) is None  # shape mismatch


def test_score_similarity_applies_floor():
    model = EmbeddingModel(key="t", label="t", storage="column", sim_floor=0.20)
    a = np.array([1.0, 0.0], dtype=np.float32)
    # cosine 1.0 → 100; cosine at floor → 0; below floor clamps to 0.
    assert emb.score_similarity(model, a, a) == pytest.approx(100.0)
    at_floor = np.array([0.20, np.sqrt(1 - 0.04)], dtype=np.float32)
    assert emb.score_similarity(model, a, at_floor) == pytest.approx(0.0, abs=1e-4)
    below = np.array([-1.0, 0.0], dtype=np.float32)
    assert emb.score_similarity(model, a, below) == 0.0
    assert emb.score_similarity(model, a, None) is None


# ── score_similarity_bulk ─────────────────────────────────────────────────────


def test_bulk_matches_pairwise_scores_exactly():
    """The vectorised path must be a pure speed-up: same numbers, same skips."""
    model = EmbeddingModel(key="t", label="t", storage="column", sim_floor=0.20)
    rng = np.random.default_rng(7)
    src = rng.standard_normal(16).astype(np.float32)
    vectors = {f"p{i}": rng.standard_normal(16).astype(np.float32) for i in range(50)}
    vectors["self"] = src
    bulk = emb.score_similarity_bulk(model, src, vectors)
    for path, v in vectors.items():
        assert bulk[path] == pytest.approx(emb.score_similarity(model, src, v), abs=1e-4)


def test_bulk_skips_unusable_vectors():
    model = EmbeddingModel(key="t", label="t", storage="column", sim_floor=0.0)
    src = np.array([1.0, 0.0], dtype=np.float32)
    vectors = {
        "ok": np.array([0.0, 1.0], dtype=np.float32),
        "wrongdim": np.array([1.0, 0.0, 0.0], dtype=np.float32),  # shape mismatch
        "empty": np.array([], dtype=np.float32),
        "zero": np.array([0.0, 0.0], dtype=np.float32),  # zero norm
    }
    bulk = emb.score_similarity_bulk(model, src, vectors)
    # Absent, exactly as score_similarity returning None means "axis not scored".
    assert set(bulk) == {"ok"}
    for path in ("wrongdim", "empty", "zero"):
        assert emb.score_similarity(model, src, vectors[path]) is None


def test_bulk_with_no_source_vector_is_empty():
    model = EmbeddingModel(key="t", label="t", storage="column", sim_floor=0.0)
    v = {"a": np.array([1.0, 0.0], dtype=np.float32)}
    assert emb.score_similarity_bulk(model, None, v) == {}
    assert emb.score_similarity_bulk(model, np.array([], dtype=np.float32), v) == {}


# ── column storage ────────────────────────────────────────────────────────────


def _row(path, **cols):
    return {"path": path, **cols}


def test_load_column_decodes_blobs_and_skips_missing():
    model = EMBEDDING_MODELS["clap"]
    v = np.array([0.1, 0.2, 0.3], dtype=np.float32)
    rows = [
        _row("a", genres_vector=v.tobytes()),
        _row("b", genres_vector=None),  # null → skipped
        _row("c"),  # column absent → skipped
    ]
    out = emb.load_vectors(model, rows)
    assert set(out) == {"a"}
    np.testing.assert_allclose(out["a"], v)


# ── derived (fingerprint) storage ─────────────────────────────────────────────


def test_fingerprint_zscores_and_excludes_partial_rows():
    model = EMBEDDING_MODELS["fingerprint"]
    cols = model.components
    # Three complete rows + one missing a single component.
    full_a = {c: float(i + 1) for i, c in enumerate(cols)}
    full_b = {c: float(2 * (i + 1)) for i, c in enumerate(cols)}
    full_c = {c: float(3 * (i + 1)) for i, c in enumerate(cols)}
    partial = dict(full_a)
    partial.pop(cols[0])  # drop one component
    rows = [
        _row("a", **full_a),
        _row("b", **full_b),
        _row("c", **full_c),
        _row("d", **partial),  # excluded — incomplete
    ]
    out = emb.load_vectors(model, rows)
    assert set(out) == {"a", "b", "c"}  # partial row dropped
    # Each returned vector has one entry per component.
    assert out["a"].shape == (len(cols),)
    # z-scored across the population → per-column mean ~0.
    mat = np.stack([out["a"], out["b"], out["c"]])
    np.testing.assert_allclose(mat.mean(axis=0), 0.0, atol=1e-5)


def test_fingerprint_empty_population():
    model = EMBEDDING_MODELS["fingerprint"]
    assert emb.load_vectors(model, []) == {}


# ── registry sanity ───────────────────────────────────────────────────────────


def test_clap_model_points_at_genres_vector():
    assert EMBEDDING_MODELS["clap"].column == "genres_vector"
    assert EMBEDDING_MODELS["clap"].storage == "column"


def test_every_similarity_axis_maps_to_a_model():
    sim = [f for f in FEATURES if f.kind == "similarity"]
    assert sim, "expected at least the CLAP + fingerprint similarity axes"
    for f in sim:
        assert f.embedding in EMBEDDING_MODELS, f"{f.key} -> {f.embedding}"


def test_table_model_readiness_gates_on_artifact(tmp_path, monkeypatch):
    monkeypatch.setattr(emb, "embed_models_dir", lambda: tmp_path)
    model = EmbeddingModel(
        key="mert",
        label="MERT",
        storage="table",
        artifact_file="mert_v1_95m.onnx",
        default_compute=True,
    )
    assert model.ready is False  # no artefact yet
    (tmp_path / "mert_v1_95m.onnx").write_bytes(b"x")
    assert model.ready is True  # artefact present
