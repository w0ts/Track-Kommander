"""One-time MERT-v1-95M → ONNX export (offline, dev-time).

Produces ``mert_v1_95m.onnx`` in the app's embed_models dir. The runtime
(:mod:`core.embed_mert`) then needs only ``onnxruntime`` — no torch /
transformers.

Runtime contract this export MUST satisfy:
  * input  ``input_values`` : float32 [1, samples], 24 kHz mono, per-clip
                              zero-mean/unit-var normalised (done in the backend)
  * output ``embedding``    : float32 [1, 768], time-mean-pooled last hidden state

Deps (uv sync --extra build):  torch, transformers  (+ nnAudio if the MERT
remote code requires it for its feature encoder).

Usage:
    .venv/bin/python scripts/export_mert_onnx.py

After export, validate numerically against the torch reference (cosine ≥ 0.999)
on a few real tracks before trusting the axis.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-8s %(name)s  %(message)s")
log = logging.getLogger("export-mert")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


_HF_ID = "m-a-p/MERT-v1-95M"
_SR = 24_000
_OUT = "mert_v1_95m.onnx"


def main() -> None:
    import torch
    from torch import nn
    from transformers import AutoModel

    from core.embeddings import embed_models_dir  # src/ is on sys.path by now

    log.info("loading %s …", _HF_ID)
    model = AutoModel.from_pretrained(_HF_ID, trust_remote_code=True).eval()

    class MeanPooled(nn.Module):
        """Wrap MERT so the graph outputs the time-mean-pooled last hidden state."""

        def __init__(self, m: nn.Module) -> None:
            super().__init__()
            self.m = m

        def forward(self, input_values: torch.Tensor) -> torch.Tensor:
            hidden = self.m(input_values).last_hidden_state  # [1, T, 768]
            return hidden.mean(dim=1)  # [1, 768]

    wrapper = MeanPooled(model).eval()
    dummy = torch.zeros(1, _SR * 5, dtype=torch.float32)  # 5 s dummy

    out_path = embed_models_dir() / _OUT
    log.info("exporting → %s", out_path)
    with torch.no_grad():
        torch.onnx.export(
            wrapper,
            (dummy,),
            str(out_path),
            input_names=["input_values"],
            output_names=["embedding"],
            dynamic_axes={"input_values": {1: "samples"}},
            opset_version=17,
        )
    log.info("done. Validate cosine ≥ 0.999 vs the torch reference before use.")


if __name__ == "__main__":
    main()
