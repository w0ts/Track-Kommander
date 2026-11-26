"""One-time PANNs CNN14 → ONNX export (offline, dev-time).

Produces ``panns_cnn14.onnx`` in the app's embed_models dir. CNN14's log-mel
frontend (torchlibrosa) is kept in-graph, so the runtime
(:mod:`core.embed_panns`) feeds raw 32 kHz waveform and does no mel maths.

Runtime contract:
  * input  ``waveform``  : float32 [1, samples], 32 kHz mono
  * output ``embedding`` : float32 [1, 2048], CNN14 penultimate embedding

Deps (uv sync --extra build):  torch, panns-inference  (or torchlibrosa + the
Cnn14 checkpoint ``Cnn14_mAP=0.431.pth`` from the PANNs release).

Usage:
    .venv/bin/python scripts/export_panns_onnx.py
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-8s %(name)s  %(message)s")
log = logging.getLogger("export-panns")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


_SR = 32_000
_OUT = "panns_cnn14.onnx"
_CKPT = "Cnn14_mAP=0.431.pth"  # place next to this script, or pass --ckpt


def main() -> None:
    import torch
    from panns_inference.models import Cnn14
    from torch import nn

    from core.embeddings import embed_models_dir  # src/ is on sys.path by now

    log.info("building CNN14 …")
    model = Cnn14(
        sample_rate=_SR,
        window_size=1024,
        hop_size=320,
        mel_bins=64,
        fmin=50,
        fmax=14000,
        classes_num=527,
    )
    ckpt = Path(__file__).resolve().parent / _CKPT
    if ckpt.exists():
        state = torch.load(ckpt, map_location="cpu")
        model.load_state_dict(state["model"])
        log.info("loaded weights from %s", ckpt)
    else:
        log.warning(
            "checkpoint %s not found — exporting RANDOM weights "
            "(download Cnn14_mAP=0.431.pth from the PANNs release)",
            ckpt,
        )
    model.eval()

    class EmbOnly(nn.Module):
        """CNN14 returns a dict; expose just the 'embedding' tensor for ONNX."""

        def __init__(self, m: nn.Module) -> None:
            super().__init__()
            self.m = m

        def forward(self, waveform: torch.Tensor) -> torch.Tensor:
            return self.m(waveform)["embedding"]  # [1, 2048]

    wrapper = EmbOnly(model).eval()
    dummy = torch.zeros(1, _SR * 5, dtype=torch.float32)

    out_path = embed_models_dir() / _OUT
    log.info("exporting → %s", out_path)
    with torch.no_grad():
        torch.onnx.export(
            wrapper,
            (dummy,),
            str(out_path),
            input_names=["waveform"],
            output_names=["embedding"],
            dynamic_axes={"waveform": {1: "samples"}},
            opset_version=17,
        )
    log.info("done. Validate cosine ≥ 0.999 vs the torch reference before use.")


if __name__ == "__main__":
    main()
