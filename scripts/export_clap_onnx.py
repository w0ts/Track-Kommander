"""One-time CLAP → ONNX export.

Runs offline (dev-time) using torch + transformers to:

  1. Export CLAP's audio encoder to a single .onnx file. Runtime then needs
     only ``onnxruntime`` (~30 MB) instead of torch + transformers (~500 MB
     of compiled code in the Nuitka bundle).
  2. Pre-compute text embeddings for every prompt our analysis pipeline
     uses (binary prompts, arousal/valence anchors, genre labels) and store
     them in a single .npz. The runtime no longer needs the text encoder
     at all — embeddings are read from the .npz directly.

Output (in ``clap_model/``):

  * ``clap_audio.onnx``               — audio encoder, fp32
  * ``clap_text_embeddings.npz``      — keyed numpy float32 arrays:
        binary_<key>_pos / binary_<key>_neg     [D]
        arousal_<i>                              [D]
        valence_<i>                              [D]
        genre_<idx>                              [D]
        genre_labels                             [N] str
        binary_keys                              [K] str

Usage:

    .venv/bin/python scripts/export_clap_onnx.py

The script is idempotent — re-running overwrites the artefacts.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import numpy as np

# ── Configure logging up front so we see torch/transformers warnings ─────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s %(name)s  %(message)s",
)
log = logging.getLogger("export-clap")

# ── Project imports — match the prompts in the runtime clap_analysis.py ─────
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

# Mirror the prompt definitions from clap_analysis.py exactly. If you add a
# new prompt category to the runtime module, mirror it here and re-run.
_BINARY_PROMPTS: dict[str, tuple[str, str]] = {
    "mood_party": (
        "energetic party music, club banger, crowd dancing, festival",
        "calm ambient quiet background music, relaxing, slow",
    ),
    "mood_aggressive": (
        "aggressive hard intense music, heavy powerful loud, industrial",
        "soft gentle peaceful music, mellow, calm, tender",
    ),
    "mood_happy": (
        "happy upbeat cheerful joyful music, positive bright uplifting",
        "sad melancholic dark somber music, gloomy, depressing",
    ),
    "mood_relaxed": (
        "calm relaxed peaceful chill music, slow tempo, soothing",
        "intense energetic loud driving music, pumping, rushing",
    ),
    "is_vocal": (
        "music with prominent lead vocals and singing voice",
        "instrumental music only, no vocals, no singing, no voice",
    ),
    "is_electronic": (
        "electronic synthesized digital music, drum machines, synthesizers",
        "acoustic live instruments, organic natural sound, unplugged",
    ),
}

_AROUSAL_ANCHORS = [
    "extremely calm serene barely audible ambient music",
    "very calm peaceful slow gentle quiet music",
    "calm gentle soft laid-back music",
    "moderately calm easy listening music",
    "balanced moderate energy music",
    "moderately energetic driving music",
    "energetic upbeat driving intense music",
    "very energetic high intensity loud powerful music",
    "maximum energy extremely intense aggressive hard pounding music",
]

_VALENCE_ANCHORS = [
    "extremely sad depressing dark gloomy tragic music",
    "very sad melancholic dark sorrowful music",
    "sad somber melancholic music",
    "slightly sad neutral melancholic music",
    "neutral balanced music, neither happy nor sad",
    "slightly positive optimistic uplifting music",
    "happy positive cheerful bright music",
    "very happy joyful exuberant upbeat music",
    "extremely happy euphoric ecstatic celebratory triumphant music",
]


# ──────────────────────────────────────────────────────────────────────────────


def main() -> None:
    # After the sys.path line above, which is why it is not at the top.
    import torch
    from transformers import ClapModel, ClapProcessor

    from core.clap_models import clap_dir

    src_dir = clap_dir()
    src_dir.mkdir(parents=True, exist_ok=True)
    # First run: pull the HF snapshot into clap_model/ so subsequent loads
    # are local-only. The `preprocessor_config.json` presence is the cheapest
    # signal that the snapshot is complete.
    if not (src_dir / "preprocessor_config.json").exists():
        log.info("CLAP not present locally — downloading laion/clap-htsat-fused → %s", src_dir)
        from huggingface_hub import snapshot_download

        snapshot_download(
            repo_id="laion/clap-htsat-fused",
            local_dir=str(src_dir),
            local_dir_use_symlinks=False,
        )
    log.info("Loading CLAP from %s", src_dir)
    processor = ClapProcessor.from_pretrained(str(src_dir))
    model = ClapModel.from_pretrained(str(src_dir)).eval()
    # Stay on CPU during export — keeps ONNX simple, the cost is one-off.

    # ── Genre labels ─────────────────────────────────────────────────────────
    labels_path = ROOT / "src" / "core" / "clap_genre_labels.json"
    with labels_path.open() as f:
        genres: list[str] = json.load(f)
    log.info("Genre labels: %d", len(genres))

    # ── Pre-compute text embeddings ──────────────────────────────────────────
    def text_emb(text: str) -> np.ndarray:
        with torch.inference_mode():
            inputs = processor(text=[text], return_tensors="pt", padding=True)
            out = model.get_text_features(**inputs)
            emb = out.pooler_output if hasattr(out, "pooler_output") else out
            if emb.dim() == 2:
                emb = emb[0]
            emb = emb / emb.norm()
            return emb.cpu().numpy().astype(np.float32)

    out: dict[str, np.ndarray] = {}

    log.info("Embedding %d binary prompts (×2)…", len(_BINARY_PROMPTS))
    for key, (pos, neg) in _BINARY_PROMPTS.items():
        out[f"binary_{key}_pos"] = text_emb(pos)
        out[f"binary_{key}_neg"] = text_emb(neg)

    log.info("Embedding %d arousal anchors…", len(_AROUSAL_ANCHORS))
    for i, prompt in enumerate(_AROUSAL_ANCHORS):
        out[f"arousal_{i}"] = text_emb(prompt)

    log.info("Embedding %d valence anchors…", len(_VALENCE_ANCHORS))
    for i, prompt in enumerate(_VALENCE_ANCHORS):
        out[f"valence_{i}"] = text_emb(prompt)

    log.info("Embedding %d genre labels…", len(genres))
    for i, g in enumerate(genres):
        out[f"genre_{i}"] = text_emb(f"{g} music")

    # Index helpers — saved alongside so the runtime knows what's where.
    # As text, not objects: an object array can only be read back with
    # pickle, and the runtime opens this file without it.
    out["genre_labels"] = np.array(genres)
    out["binary_keys"] = np.array(list(_BINARY_PROMPTS))

    npz_path = src_dir / "clap_text_embeddings.npz"
    np.savez_compressed(npz_path, **out)
    log.info("Wrote %s (%.1f KB)", npz_path, npz_path.stat().st_size / 1024)

    # ── Export the audio encoder to ONNX ─────────────────────────────────────
    # CLAP "fusion" mode: ClapProcessor emits ``input_features`` of shape
    # [B, 4, n_frames, n_mels] (four chunks averaged) plus an ``is_longer``
    # boolean flagging clips longer than the chunk length. Both feed
    # ``get_audio_features``. We wrap the path to also unit-normalise the
    # output so runtime gets cosine-ready vectors with no extra step.
    class AudioEncoder(torch.nn.Module):
        def __init__(self, m: ClapModel) -> None:
            super().__init__()
            self.m = m

        def forward(
            self,
            input_features: torch.Tensor,
            is_longer: torch.Tensor,
        ) -> torch.Tensor:
            out = self.m.get_audio_features(
                input_features=input_features,
                is_longer=is_longer,
            )
            emb = out.pooler_output if hasattr(out, "pooler_output") else out
            return emb / emb.norm(dim=-1, keepdim=True)

    encoder = AudioEncoder(model).eval()

    # Dummy inputs match ClapProcessor's output: shape [1, 4, 1001, 64] for
    # the mel-feature stack, [1, 1] bool for is_longer.
    # is_longer=True so the trace captures the fusion branch (the False path
    # constant-folds the input out and the runtime model loses the parameter).
    # Runtime must pad short audio to ≥ 10 s before mel extraction so we
    # always hit the fusion path — easy: ClapFeatureExtractor's repeatpad
    # mode does this anyway for shorter clips.
    dummy_features = torch.randn(1, 4, 1001, 64, dtype=torch.float32) * 0.1
    dummy_islonger = torch.ones(1, 1, dtype=torch.bool)

    onnx_path = src_dir / "clap_audio.onnx"
    log.info("Exporting audio encoder → %s", onnx_path)
    torch.onnx.export(
        encoder,
        (dummy_features, dummy_islonger),
        str(onnx_path),
        input_names=["input_features", "is_longer"],
        output_names=["audio_embedding"],
        dynamic_axes={
            "input_features": {0: "batch", 2: "frames"},
            "is_longer": {0: "batch"},
            "audio_embedding": {0: "batch"},
        },
        opset_version=17,
        do_constant_folding=True,
        # Use the legacy TorchScript exporter — TorchDynamo trips on CLAP's
        # data-dependent fusion branch (modeling_clap.py:311) with
        # GuardOnDataDependentSymNode. Legacy traces fine.
        dynamo=False,
    )
    size_mb = onnx_path.stat().st_size / (1024 * 1024)
    log.info("ONNX export complete: %.1f MB", size_mb)


def _cli() -> None:
    import argparse

    from core.clap_models import CLAP_EMBEDS_FILE, CLAP_ONNX_FILE, clap_dir, clap_model_ready

    parser = argparse.ArgumentParser(
        description="Export CLAP → ONNX + precomputed text embeddings.",
    )
    parser.add_argument(
        "--ensure",
        action="store_true",
        help=(
            "Skip the (heavy) export when both artefacts already exist. "
            "Used by the build to download + transform the model only on a "
            "fresh checkout."
        ),
    )
    args = parser.parse_args()

    if args.ensure and clap_model_ready():
        log.info(
            "CLAP artefacts already present in %s (%s, %s) — skipping export",
            clap_dir(),
            CLAP_ONNX_FILE,
            CLAP_EMBEDS_FILE,
        )
        return

    main()


if __name__ == "__main__":
    _cli()
