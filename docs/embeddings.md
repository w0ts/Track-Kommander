# Similarity embeddings

The matcher's similarity axes are pluggable. Each is one **embedding model** —
a way to turn a track into a vector — registered once in
[`core/embeddings.py`](../src/core/embeddings.py) and surfaced as a
`kind="similarity"` slider in [`core/features.py`](../src/core/features.py).

| Axis | Model | Storage | What it matches |
|------|-------|---------|-----------------|
| Similarity   | CLAP        | `genres_vector` column | timbre + genre + mood (learned, describable) |
| Fingerprint  | —           | derived from signal columns | timbral texture (interpretable, GPU-free) |
| MERT         | MERT-v1-95M | side table | musical identity below the genre label |
| Discogs      | EffNet-Discogs | side table | the subgenre a record store would file it under |
| PANNs        | CNN14       | side table | sonic palette / instrumentation present |
| OpenL3       | OpenL3 music| side table | learned timbral texture (lighter than MERT) |

**Fingerprint works today** — it's computed from features already on every
track, no download or re-scan. Enable it in the axis settings.

> **Parked for the current release.** All axes except CLAP `Similarity` and
> `Style` (i.e. Fingerprint, MERT, Discogs, PANNs, OpenL3) are hidden and not
> computed. Their code, side table, backends, and export scripts are kept
> intact. To bring one back: set `released=True` on its `FeatureSpec` in
> [`core/features.py`](../src/core/features.py) and, for a `table` model,
> `default_compute=True` on its entry in [`core/embeddings.py`](../src/core/embeddings.py).

## Storage

- **column** — a float32 BLOB on `tracks` (CLAP's `genres_vector`).
- **derived** — built on the fly from existing scalar columns, z-scored across
  the candidate population. No storage, no extra analysis.
- **table** — a row in `track_embeddings (path, model, vector, dim)`. A new
  model is then a registry entry, not a schema change.

## Adding / enabling a `table` model (MERT, PANNs, OpenL3, Discogs)

The runtime stays torch/TF-free: inference is `onnxruntime`, feature extraction
is `librosa`. The heavy frameworks are only used **once, offline**, to export an
ONNX artefact — exactly like CLAP.

1. **Install the export deps** (dev machine only):
   ```bash
   uv sync --extra build
   ```
2. **Export the artefact** into the app's `embed_models/` dir:
   ```bash
   .venv/bin/python scripts/export_mert_onnx.py
   .venv/bin/python scripts/export_panns_onnx.py     # needs Cnn14_mAP=0.431.pth
   .venv/bin/python scripts/export_discogs_onnx.py <essentia-onnx-url>
   ```
   **OpenL3 is special** — TensorFlow-based and incompatible with Python 3.13
   (openl3 0.4.2 imports the removed `imp` module). Export it from a separate
   throwaway Python 3.10/3.11 env, then it lands in the same `embed_models/`:
   ```bash
   python3.11 -m venv .venv-openl3
   .venv-openl3/bin/pip install "openl3>=0.4.2" "tf2onnx>=1.16.0"
   .venv-openl3/bin/python scripts/export_openl3_onnx.py
   ```
   `embed_models/` resolves to `<repo>/embed_models/` in dev and the bundled
   dir in the frozen app (see `core.embeddings.embed_models_dir`).
3. **Validate** the ONNX numerically against the reference framework
   (cosine ≥ 0.999 on a few real tracks) — each backend's preprocessing must
   match the model's. Discogs/OpenL3 are the likeliest to need a tweak; their
   backends carry `VALIDATE` notes on the mel params / I/O names.
4. **Re-scan.** A scan computes a `table` model only once its artefact is
   present (`EmbeddingModel.ready`), and the incremental backfill
   (`get_up_to_date_paths(..., required_models)`) re-analyses only the tracks
   missing that embedding — not the whole library.
5. **Enable the axis** in the matching settings and push its slider.

## How the matcher uses them

`rank_matches_axes` loads each active similarity model's vectors for the whole
candidate population in one pass (`load_vectors`), then scores cosine closeness
mapped through the model's `sim_floor`. Adding model #7 needs **zero** matcher
or UI changes — only a registry entry, a backend module, and an export script.

## Files

- `core/embeddings.py` — model registry, loaders, cosine, readiness.
- `core/onnx_embedding.py` — shared ONNX session + preprocessing helpers.
- `core/embed_<model>.py` — per-model backend (`embed(path) -> np.ndarray`).
- `scripts/export_<model>_onnx.py` — one-time offline ONNX export.
