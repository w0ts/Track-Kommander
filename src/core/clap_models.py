"""CLAP ONNX artefact location + readiness check.

The runtime no longer ships ``torch`` or ``transformers``. Instead we use a
pre-exported ONNX audio encoder and a numpy ``.npz`` of pre-computed text
embeddings, both produced once on a dev machine by
``scripts/export_clap_onnx.py``. See that script's header for what it does.

Artefacts (~110 MB total):

  * ``clap_audio.onnx``                — audio encoder, fp32
  * ``clap_text_embeddings.npz``       — text embeddings for every prompt

Cached location:

  * Frozen app : ~/Library/Application Support/Track Kommander/clap_model/
  * Development: <project_root>/clap_model/
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

log = logging.getLogger("models")

# Filenames the runtime expects to find in clap_dir().
CLAP_ONNX_FILE = "clap_audio.onnx"
CLAP_EMBEDS_FILE = "clap_text_embeddings.npz"

ProgressCb = Callable[[str, int, int], None]  # (label, current, total)


def clap_dir() -> Path:
    """Directory holding ``clap_audio.onnx`` and the precomputed embeddings.

    Resolved via :func:`core.runtime.bundle_data_dir`:
    * Frozen .app: ``…/Contents/MacOS/clap_model/`` (bundled read-only).
    * Dev: ``<repo>/clap_model/`` (created if missing).
    """
    from core.runtime import bundle_data_dir, is_frozen

    p = bundle_data_dir() / "clap_model"
    if not is_frozen():
        p.mkdir(parents=True, exist_ok=True)
    return p


def clap_model_ready() -> bool:
    """True if the ONNX artefacts are present."""
    d = clap_dir()
    return (d / CLAP_ONNX_FILE).exists() and (d / CLAP_EMBEDS_FILE).exists()


def ensure_clap_downloaded(progress_cb: ProgressCb | None = None) -> None:
    """Verify the ONNX artefacts exist; otherwise raise with a clear message.

    The runtime can't produce these — they require the (heavy) torch +
    transformers stack used by ``scripts/export_clap_onnx.py``. We expect
    them to be bundled with the app or copied in by the user once.
    """
    if clap_model_ready():
        return
    raise RuntimeError(
        f"CLAP artefacts missing in {clap_dir()}. Expected:\n"
        f"  - {CLAP_ONNX_FILE}\n"
        f"  - {CLAP_EMBEDS_FILE}\n"
        "Run `python scripts/export_clap_onnx.py` once on a dev machine that "
        "has torch + transformers installed."
    )
