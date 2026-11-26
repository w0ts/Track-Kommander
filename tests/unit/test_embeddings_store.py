"""Unit tests for the track_embeddings side table and scan-time wiring."""

from __future__ import annotations

import sys
import types
from pathlib import Path

import numpy as np
import pytest

import core.db as db
from core import embeddings as emb
from core.embeddings import EmbeddingModel


@pytest.fixture()
def fresh_db(tmp_path, monkeypatch):
    dbfile = tmp_path / "t.db"
    monkeypatch.setattr(db, "_db_path", lambda: dbfile)
    db.init_db()
    return dbfile


def _add(path: str) -> None:
    db.upsert_track(path, Path(path).name, 120.0, 1, True, "8A", 200.0)


def _vec(*xs) -> bytes:
    return np.array(xs, dtype=np.float32).tobytes()


# ── storage round-trip ─────────────────────────────────────────────────────────


def test_upsert_and_get_embeddings_roundtrip(fresh_db):
    _add("/lib/a.mp3")
    _add("/lib/b.mp3")
    db.upsert_embedding("/lib/a.mp3", "mert", _vec(0.1, 0.2, 0.3), 3)
    db.upsert_embedding("/lib/b.mp3", "mert", _vec(0.4, 0.5, 0.6), 3)
    out = db.get_embeddings("mert", ["/lib/a.mp3", "/lib/b.mp3", "/lib/missing.mp3"])
    assert set(out) == {"/lib/a.mp3", "/lib/b.mp3"}
    np.testing.assert_allclose(out["/lib/a.mp3"], [0.1, 0.2, 0.3], rtol=1e-6)


def test_upsert_embedding_replaces(fresh_db):
    _add("/lib/a.mp3")
    db.upsert_embedding("/lib/a.mp3", "mert", _vec(1.0), 1)
    db.upsert_embedding("/lib/a.mp3", "mert", _vec(2.0), 1)
    out = db.get_embeddings("mert", ["/lib/a.mp3"])
    np.testing.assert_allclose(out["/lib/a.mp3"], [2.0])


def test_embeddings_are_namespaced_by_model(fresh_db):
    _add("/lib/a.mp3")
    db.upsert_embedding("/lib/a.mp3", "mert", _vec(1.0), 1)
    db.upsert_embedding("/lib/a.mp3", "panns", _vec(9.0), 1)
    assert db.get_embeddings("mert", ["/lib/a.mp3"])["/lib/a.mp3"][0] == pytest.approx(1.0)
    assert db.get_embeddings("panns", ["/lib/a.mp3"])["/lib/a.mp3"][0] == pytest.approx(9.0)


def test_get_embeddings_empty_paths(fresh_db):
    assert db.get_embeddings("mert", []) == {}


# ── prune cascade ───────────────────────────────────────────────────────────────


def test_remove_track_drops_its_embeddings(fresh_db):
    _add("/lib/a.mp3")
    db.upsert_embedding("/lib/a.mp3", "mert", _vec(1.0), 1)
    db.remove_track("/lib/a.mp3")
    assert db.get_embeddings("mert", ["/lib/a.mp3"]) == {}


def test_remove_tracks_not_in_drops_orphan_embeddings(fresh_db):
    _add("/lib/a.mp3")
    _add("/old/c.mp3")
    db.upsert_embedding("/old/c.mp3", "mert", _vec(1.0), 1)
    db.remove_tracks_not_in(["/lib/a.mp3"])
    assert db.get_embeddings("mert", ["/old/c.mp3"]) == {}


# ── backfill: up-to-date requires the embedding row ─────────────────────────────


def test_up_to_date_requires_embedding_when_model_required(fresh_db):
    _add("/lib/a.mp3")
    _add("/lib/b.mp3")
    db.upsert_embedding("/lib/a.mp3", "mert", _vec(1.0), 1)
    # No required model → both are up to date (column-only check).
    assert db.get_up_to_date_paths([]) == {"/lib/a.mp3", "/lib/b.mp3"}
    # Requiring "mert" excludes the track lacking that embedding → it backfills.
    assert db.get_up_to_date_paths([], ["mert"]) == {"/lib/a.mp3"}


# ── table-storage loader + compute dispatch ─────────────────────────────────────


def test_load_vectors_table_storage(fresh_db):
    _add("/lib/a.mp3")
    db.upsert_embedding("/lib/a.mp3", "mert", _vec(0.1, 0.2), 2)
    model = EmbeddingModel(key="mert", label="MERT", storage="table")
    rows = [{"path": "/lib/a.mp3"}, {"path": "/lib/b.mp3"}]
    out = emb.load_vectors(model, rows)
    assert set(out) == {"/lib/a.mp3"}
    np.testing.assert_allclose(out["/lib/a.mp3"], [0.1, 0.2], rtol=1e-6)


def test_compute_vector_no_backend_returns_none():
    model = EmbeddingModel(key="x", label="x", storage="table", backend_module=None)
    assert emb.compute_vector(model, "/any.mp3") is None


def test_compute_vector_uses_backend(monkeypatch):
    fake = types.ModuleType("core._fake_backend")
    fake.embed = lambda path: np.array([1.0, 2.0, 3.0], dtype=np.float32)
    monkeypatch.setitem(sys.modules, "core._fake_backend", fake)
    model = EmbeddingModel(key="x", label="x", storage="table", backend_module="core._fake_backend")
    vec = emb.compute_vector(model, "/any.mp3")
    np.testing.assert_allclose(vec, [1.0, 2.0, 3.0])


def test_compute_vector_swallows_backend_errors(monkeypatch):
    fake = types.ModuleType("core._boom_backend")

    def _boom(path):
        raise RuntimeError("model file missing")

    fake.embed = _boom
    monkeypatch.setitem(sys.modules, "core._boom_backend", fake)
    model = EmbeddingModel(key="x", label="x", storage="table", backend_module="core._boom_backend")
    assert emb.compute_vector(model, "/any.mp3") is None
