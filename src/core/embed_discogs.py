"""Discogs-EffNet embedding backend (runtime, ONNX).

EffNet-Discogs is trained to tag tracks against the Discogs genre/style tree —
essentially the taxonomy DJs and crate-diggers already think in. Its embedding
is aligned to that vocabulary rather than CLAP's web-caption space, so it matches
"the subgenre a record store would file it under".

Unlike MERT/PANNs/OpenL3 (whose frontends are baked into the ONNX graph), the
Essentia EffNet graph expects a precomputed mel patch, so the runtime builds it
in librosa. Those mel parameters MUST match Essentia's
``TensorflowPredictEffnetDiscogs`` exactly — they are the most likely source of
a mismatch and MUST be validated against the Essentia reference (cosine ≥ 0.999)
when the artefact is first exported. The values below are the documented EffNet
defaults; treat them as VALIDATE-on-export.

Runtime contract:
  * input ``melspectrogram``: float32 ``[P, 128, 96]`` — ``P`` patches of 128
    frames × 96 mel bands.
  * output ``embedding``: float32 ``[P, 1280]`` — per-patch EffNet embedding.

Patch embeddings are mean-pooled then L2-normalised.
"""

from __future__ import annotations

import numpy as np

from core.onnx_embedding import l2_normalize, load_audio_mono, log_mel, make_singleton

_MODEL_FILE = "discogs_effnet_bs1.onnx"
# VALIDATE these against Essentia's EffNet-Discogs preprocessing on first export.
_SR = 16_000
_N_FFT = 512
_HOP = 256
_N_MELS = 96
_PATCH = 128  # frames per patch
_MAX_PATCHES = 32  # cap patches per track to bound inference time

_get = make_singleton(_MODEL_FILE, ["embedding"])


def _patches(mel: np.ndarray) -> np.ndarray | None:
    """Split a [frames, 96] mel into non-overlapping [P, 128, 96] patches."""
    n = mel.shape[0] // _PATCH
    if n == 0:
        return None
    n = min(n, _MAX_PATCHES)
    return mel[: n * _PATCH].reshape(n, _PATCH, _N_MELS).astype(np.float32)


def embed(path: str) -> np.ndarray | None:
    audio = load_audio_mono(path, _SR)
    if audio.size == 0:
        return None
    mel = log_mel(audio, _SR, _N_FFT, _HOP, _N_MELS)  # [frames, 96]
    patches = _patches(mel)
    if patches is None:
        return None
    out = np.asarray(_get().run({"melspectrogram": patches})[0], dtype=np.float32)
    return l2_normalize(out.mean(axis=0))
