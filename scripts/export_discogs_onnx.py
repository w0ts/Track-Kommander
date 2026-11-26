"""One-time Discogs-EffNet ONNX fetch + verify (offline, dev-time).

Essentia distributes EffNet-Discogs as an ONNX file directly, so this script
*downloads* the official artefact rather than exporting one, places it as
``discogs_effnet_bs1.onnx`` in the app's embed_models dir, and prints its I/O
so the runtime contract can be confirmed.

Runtime contract (:mod:`core.embed_discogs`):
  * input  ``melspectrogram`` : float32 [P, 128, 96]  (P patches of 128 frames)
  * output ``embedding``      : float32 [P, 1280]

⚠ The mel parameters in ``core.embed_discogs`` (sr 16k, n_fft 512, hop 256,
96 bands) MUST match Essentia's ``TensorflowPredictEffnetDiscogs`` preprocessing.
This is the most likely source of mismatch — validate cosine ≥ 0.999 against an
Essentia reference embedding on a few tracks before trusting the axis. If the
official graph's I/O names or the embedding dim differ, update the backend.

Model index:  https://essentia.upf.edu/models/  (feature-extractors/discogs-effnet)

Usage:
    .venv/bin/python scripts/export_discogs_onnx.py [URL]
"""

from __future__ import annotations

import logging
import sys
import urllib.request
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-8s %(name)s  %(message)s")
log = logging.getLogger("export-discogs")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


_OUT = "discogs_effnet_bs1.onnx"
# Fill in the exact artefact URL from the Essentia models index (the
# discogs-effnet embeddings ONNX). Passed as argv[1] to override.
_DEFAULT_URL = ""


def main() -> None:
    from core.embeddings import embed_models_dir  # src/ is on sys.path by now

    url = sys.argv[1] if len(sys.argv) > 1 else _DEFAULT_URL
    if not url:
        log.error(
            "No URL. Pass the EffNet-Discogs ONNX URL from "
            "https://essentia.upf.edu/models/ as the first argument."
        )
        sys.exit(2)

    out_path = embed_models_dir() / _OUT
    log.info("downloading %s → %s", url, out_path)
    urllib.request.urlretrieve(url, out_path)

    import onnx

    m = onnx.load(str(out_path))
    log.info(
        "ONNX inputs:  %s",
        [(i.name, [d.dim_value for d in i.type.tensor_type.shape.dim]) for i in m.graph.input],
    )
    log.info(
        "ONNX outputs: %s",
        [(o.name, [d.dim_value for d in o.type.tensor_type.shape.dim]) for o in m.graph.output],
    )
    log.info(
        "If the input is not 'melspectrogram' [P,128,96] or the output is "
        "not 'embedding' [P,1280], update core.embed_discogs to match."
    )


if __name__ == "__main__":
    main()
