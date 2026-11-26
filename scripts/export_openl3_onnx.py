"""One-time OpenL3 (music, mel256, 512-d) → ONNX export (offline, dev-time).

Produces ``openl3_music_mel256_512.onnx``. OpenL3's kapre mel frontend is kept
in-graph, so the runtime (:mod:`core.embed_openl3`) only frames the audio into
1-second 48 kHz windows.

Runtime contract:
  * input  ``frames``    : float32 [F, 48000, 1]  (F one-second windows)
  * output ``embedding`` : float32 [F, 512]

⚠ tf2onnx names inputs/outputs after the Keras layers. After export, print the
ONNX I/O (``onnx.load`` → graph.input/output) and, if they differ from
``frames``/``embedding`` or the channel axis is ``[F, 1, 48000]``, rename here
and update ``core.embed_openl3`` to match.

Deps (uv sync --extra build):  tensorflow, openl3, tf2onnx, onnx.
This is the one model that needs TensorFlow — keep its export env separate from
the torch one if they conflict.

Usage:
    .venv/bin/python scripts/export_openl3_onnx.py
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-8s %(name)s  %(message)s")
log = logging.getLogger("export-openl3")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


_OUT = "openl3_music_mel256_512.onnx"


def main() -> None:
    import openl3
    import tf2onnx

    from core.embeddings import embed_models_dir  # src/ is on sys.path by now

    log.info("loading OpenL3 keras model (mel256 · music · 512) …")
    model = openl3.models.load_audio_embedding_model(
        input_repr="mel256",
        content_type="music",
        embedding_size=512,
    )

    out_path = embed_models_dir() / _OUT
    log.info("converting → %s", out_path)
    # spec[0] is the model's audio input; tf2onnx infers the rest. Adjust the
    # input signature if the model expects a different rank (see header note).
    import tensorflow as tf

    spec = (tf.TensorSpec(model.inputs[0].shape, tf.float32, name="frames"),)
    tf2onnx.convert.from_keras(
        model,
        input_signature=spec,
        opset=17,
        output_path=str(out_path),
    )
    log.info("done. Inspect ONNX I/O names + validate vs the openl3 reference.")


if __name__ == "__main__":
    main()
