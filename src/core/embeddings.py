"""Embedding-vector registry — pluggable similarity backends.

A track can be represented by many different vectors. Today that's CLAP (a
learned audio embedding) and a derived *timbral fingerprint*; once their ONNX
backends land, also MERT / PANNs / OpenL3 / Discogs-EffNet. Each is registered
here once and surfaced as a separate ``kind="similarity"`` axis in
:mod:`core.features`. The matcher asks this module for the vectors of a
population of rows and scores cosine closeness — it never needs to know *where*
a vector comes from.

Storage kinds
-------------
``column``   — a float32 BLOB on the tracks row (CLAP's ``genres_vector``).
``table``    — a row in the ``track_embeddings`` side table (Phase 1).
``derived``  — computed on the fly from existing scalar columns (the timbral
               fingerprint), z-scored across the supplied population so every
               component contributes on an equal footing.

This module is deliberately Qt-free so the matcher (and tests) can import it
without pulling in PySide6.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import numpy as np

StorageKind = Literal["column", "table", "derived"]


def embed_models_dir() -> Path:
    """Directory holding the exported ONNX artefacts for ``table`` models.

    Sibling of the CLAP ``clap_model`` dir. Created in dev; read-only (bundled)
    in the frozen app.
    """
    from core.runtime import bundle_data_dir, is_frozen

    p = bundle_data_dir() / "embed_models"
    if not is_frozen():
        p.mkdir(parents=True, exist_ok=True)
    return p


@dataclass(frozen=True)
class EmbeddingModel:
    """One way to turn a track into a vector, declared once."""

    key: str  # stable id — referenced by FeatureSpec.embedding
    label: str
    storage: StorageKind
    # Cosine at/below this floor maps to 0 similarity; the range (floor, 1]
    # is rescaled to (0, 100]. Unit-norm learned embeddings (CLAP) never go
    # truly orthogonal in practice, so a positive floor stretches the useful
    # band; a z-scored fingerprint genuinely spans [-1, 1], so its floor is 0.
    sim_floor: float = 0.0
    column: str | None = None  # storage="column": the BLOB column
    components: tuple[str, ...] = ()  # storage="derived": z-scored columns
    # storage="table": dotted module path of the ONNX backend. The module must
    # expose ``embed(path: str) -> np.ndarray``. Imported lazily at scan time so
    # the heavy backend (onnxruntime + model file) is only loaded when computed.
    backend_module: str | None = None
    # storage="table": the exported ONNX filename in embed_models_dir(). A scan
    # only computes (and requires) the model once this artefact is present, so
    # listing a model before its export exists is harmless.
    artifact_file: str | None = None
    # Whether a library scan computes this embedding by default, *if its artefact
    # is present*. Heavy table models are gated on readiness; see workers.analysis.
    default_compute: bool = False

    @property
    def ready(self) -> bool:
        """True when this model can actually be computed right now."""
        if self.storage != "table":
            return True
        if not self.artifact_file:
            return False
        return (embed_models_dir() / self.artifact_file).exists()


# ── the registry ─────────────────────────────────────────────────────────────
EMBEDDING_MODELS: dict[str, EmbeddingModel] = {
    # CLAP audio encoder — the existing learned embedding, unit-norm float32.
    "clap": EmbeddingModel(
        key="clap",
        label="CLAP",
        storage="column",
        column="genres_vector",
        sim_floor=0.20,
    ),
    # Timbral fingerprint — built for free from the librosa signal features
    # already stored on every track, z-scored across the candidate population.
    # Fully interpretable, GPU-free, and independent of CLAP's genre bias.
    "fingerprint": EmbeddingModel(
        key="fingerprint",
        label="Timbral fingerprint",
        storage="derived",
        sim_floor=0.0,
        components=(
            "spectral_centroid",
            "hfc",
            "low_energy",
            "mid_energy",
            "bassiness",
            "low_dynamics",
            "mid_dynamics",
            "high_dynamics",
            "danceability",
            "dynamic_complexity",
            "loudness",
        ),
    ),
    # ── ONNX backends (vectors in the track_embeddings side table) ───────────
    # Each is computed during a scan once its artefact is present in
    # embed_models_dir() (produced offline by scripts/export_<model>_onnx.py).
    # sim_floor for unit-norm learned embeddings: a positive floor stretches the
    # useful cosine band the same way CLAP's does.
    # default_compute is False while these axes are parked out of the release
    # (see the released=False specs in core.features). Even with an artefact
    # present a scan won't compute them; flip back to True when re-exposing.
    "mert": EmbeddingModel(
        key="mert",
        label="MERT",
        storage="table",
        backend_module="core.embed_mert",
        artifact_file="mert_v1_95m.onnx",
        default_compute=False,
        sim_floor=0.20,
    ),
    "panns": EmbeddingModel(
        key="panns",
        label="PANNs",
        storage="table",
        backend_module="core.embed_panns",
        artifact_file="panns_cnn14.onnx",
        default_compute=False,
        sim_floor=0.20,
    ),
    "openl3": EmbeddingModel(
        key="openl3",
        label="OpenL3",
        storage="table",
        backend_module="core.embed_openl3",
        artifact_file="openl3_music_mel256_512.onnx",
        default_compute=False,
        sim_floor=0.10,
    ),
    "discogs": EmbeddingModel(
        key="discogs",
        label="Discogs-EffNet",
        storage="table",
        backend_module="core.embed_discogs",
        artifact_file="discogs_effnet_bs1.onnx",
        default_compute=False,
        sim_floor=0.20,
    ),
}


def _cell(row, col: str):
    """Safe read of a (possibly absent / null) column on a sqlite3.Row or dict."""
    try:
        return row[col]
    except Exception:
        return None


def cosine(a: np.ndarray | None, b: np.ndarray | None) -> float | None:
    """Cosine similarity of two vectors, or None when either is unusable."""
    if a is None or b is None or a.size == 0 or b.size == 0 or a.shape != b.shape:
        return None
    na = float(np.linalg.norm(a))
    nb = float(np.linalg.norm(b))
    if na == 0.0 or nb == 0.0:
        return None
    return float(np.dot(a, b) / (na * nb))


def score_similarity(
    model: EmbeddingModel,
    a: np.ndarray | None,
    b: np.ndarray | None,
) -> float | None:
    """Cosine between two vectors mapped through the model's floor → 0–100, or None."""
    c = cosine(a, b)
    if c is None:
        return None
    floor = model.sim_floor
    return max(0.0, (c - floor) / (1.0 - floor)) * 100.0


def score_similarity_bulk(
    model: EmbeddingModel,
    src: np.ndarray | None,
    vectors: dict[str, np.ndarray],
) -> dict[str, float]:
    """``{path: 0–100}`` for every vector comparable to ``src``, in one matmul.

    Semantically identical to calling :func:`score_similarity` per path, but a
    single normalise-and-dot over the stacked population instead of a Python
    loop — the difference is ~10x on a 10k library, and a re-rank runs on every
    slider tick. Paths whose vector is unusable (wrong shape, empty, zero norm)
    are simply absent from the result, mirroring ``score_similarity`` returning
    None so the caller skips that axis for that candidate.
    """
    if src is None or src.size == 0:
        return {}
    src_norm = float(np.linalg.norm(src))
    if src_norm == 0.0:
        return {}
    paths = [p for p, v in vectors.items() if v is not None and v.shape == src.shape]
    if not paths:
        return {}
    matrix = np.stack([vectors[p] for p in paths]).astype(np.float32)
    norms = np.linalg.norm(matrix, axis=1)
    usable = norms > 0.0
    cos = np.zeros(len(paths), dtype=np.float32)
    cos[usable] = (matrix[usable] @ (src / src_norm)) / norms[usable]
    floor = model.sim_floor
    scores = np.maximum(0.0, (cos - floor) / (1.0 - floor)) * 100.0
    return {p: float(scores[i]) for i, p in enumerate(paths) if usable[i]}


# ── population loaders ─────────────────────────────────────────────────────────


def _load_column(model: EmbeddingModel, rows: Sequence) -> dict[str, np.ndarray]:
    out: dict[str, np.ndarray] = {}
    if model.column is None:
        return out
    for r in rows:
        path = _cell(r, "path")
        if path is None:
            continue
        blob = _cell(r, model.column)
        if not blob:
            continue
        try:
            v = np.frombuffer(blob, dtype=np.float32)
        except Exception:
            continue
        if v.size:
            out[path] = v
    return out


def _load_derived(model: EmbeddingModel, rows: Sequence) -> dict[str, np.ndarray]:
    """Build z-scored fingerprint vectors from the population's component columns.

    A track is included only when *every* component is present — the signal
    features are computed together in one pass, so this is all-or-nothing per
    track and never yields a partial, misleading vector. Each component is
    centred and scaled by the population mean/std so loudly-scaled features
    (e.g. raw RMS) don't dominate the cosine over subtle ones.
    """
    cols = model.components
    if not cols:
        return {}
    paths: list[str] = []
    raw: list[list[float]] = []
    for r in rows:
        path = _cell(r, "path")
        if path is None:
            continue
        vals: list[float] = []
        ok = True
        for c in cols:
            x = _cell(r, c)
            if x is None:
                ok = False
                break
            try:
                vals.append(float(x))
            except (TypeError, ValueError):
                ok = False
                break
        if ok:
            paths.append(path)
            raw.append(vals)
    if not raw:
        return {}
    matrix = np.asarray(raw, dtype=np.float64)  # [N, D]
    mean = matrix.mean(axis=0)
    std = matrix.std(axis=0)
    std[std == 0.0] = 1.0  # constant column → 0 after centering
    z = (matrix - mean) / std
    return {path: z[i].astype(np.float32) for i, path in enumerate(paths)}


def _load_table(model: EmbeddingModel, rows: Sequence) -> dict[str, np.ndarray]:
    from core.db import get_embeddings

    paths = [p for r in rows if (p := _cell(r, "path")) is not None]
    return get_embeddings(model.key, paths)


def load_vectors(model: EmbeddingModel, rows: Sequence) -> dict[str, np.ndarray]:
    """Return ``{path: vector}`` for every row that has this model's embedding.

    ``rows`` is the full population to be compared (source + candidates) so that
    ``derived`` models can normalise against the whole set in one pass.
    """
    if model.storage == "column":
        return _load_column(model, rows)
    if model.storage == "derived":
        return _load_derived(model, rows)
    if model.storage == "table":
        return _load_table(model, rows)
    return {}


# ── scan-time computation ──────────────────────────────────────────────────────


def table_models() -> list[EmbeddingModel]:
    """All registered models whose vectors are produced by an ONNX backend."""
    return [m for m in EMBEDDING_MODELS.values() if m.storage == "table"]


def ready_table_models() -> list[EmbeddingModel]:
    """Table models a scan should compute now: default-on *and* artefact present.

    Gating on ``ready`` is what keeps a default-on model from forcing an endless
    re-scan before its ONNX file has been exported — until then it's simply not
    in the required set, so every track stays "up to date" without it.
    """
    return [m for m in table_models() if m.default_compute and m.ready]


def compute_vector(model: EmbeddingModel, path: str) -> np.ndarray | None:
    """Run a ``table`` model's backend on one file → float32 vector, or None.

    Lazily imports ``model.backend_module`` (which must expose
    ``embed(path) -> np.ndarray``). Returns None — never raises — when the model
    has no backend or the backend fails for this file, so one bad model or file
    can't abort a scan.
    """
    if model.storage != "table" or not model.backend_module:
        return None
    try:
        import importlib

        backend = importlib.import_module(model.backend_module)
        vec = backend.embed(path)
    except Exception:
        return None
    if vec is None:
        return None
    arr = np.asarray(vec, dtype=np.float32).ravel()
    return arr if arr.size else None
