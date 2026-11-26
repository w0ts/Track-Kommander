"""MERT-v1-95M embedding backend (runtime, ONNX).

MERT is a music-specialised self-supervised transformer. Its embedding captures
musical structure (pitch, harmony, timbre) far below the level of a genre label
— "sounds like the same kind of record" rather than "same genre tag".

Runtime contract (the export script must honour this):
  * input ``input_values``: float32 ``[1, samples]`` raw 24 kHz mono waveform,
    per-clip normalised to zero mean / unit variance (the HF
    ``Wav2Vec2FeatureExtractor`` ``do_normalize`` behaviour).
  * output ``embedding``: float32 ``[1, 768]`` — the time-mean-pooled last
    hidden state. (Layer choice is fixed at export; the last layer is the most
    semantic. Lower layers — production texture — are a future second axis.)

The vector is L2-normalised here so cosine in the matcher is well-scaled.
Validate against the torch reference (cosine ≥ 0.999) when first exported.
"""

from __future__ import annotations

import numpy as np

from core.onnx_embedding import center_window, l2_normalize, load_audio_mono, make_singleton

_MODEL_FILE = "mert_v1_95m.onnx"
_SR = 24_000
_WINDOW = 30 * _SR  # central 30 s — bounds compute, deterministic

_get = make_singleton(_MODEL_FILE, ["embedding"])


def _normalize(audio: np.ndarray) -> np.ndarray:
    mean = float(audio.mean())
    var = float(audio.var())
    return ((audio - mean) / np.sqrt(var + 1e-7)).astype(np.float32)


def embed(path: str) -> np.ndarray | None:
    audio = load_audio_mono(path, _SR)
    if audio.size == 0:
        return None
    audio = _normalize(center_window(audio, _WINDOW))
    out = _get().run({"input_values": audio[None, :]})[0]
    return l2_normalize(np.asarray(out, dtype=np.float32).ravel())
