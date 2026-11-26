"""PANNs CNN14 embedding backend (runtime, ONNX).

PANNs CNN14 is trained on AudioSet (general sound events). Its 2048-d embedding
captures the *sonic palette* — instrumentation and the sounds present — rather
than musical structure or genre labels. Complements MERT and CLAP.

Runtime contract (the export script must honour this):
  * input ``waveform``: float32 ``[1, samples]`` raw 32 kHz mono waveform. The
    exported graph includes CNN14's own log-mel frontend (torchlibrosa), so the
    runtime does no mel computation — only decode + resample. This avoids any
    mel-parameter mismatch.
  * output ``embedding``: float32 ``[1, 2048]`` — CNN14's penultimate embedding.

L2-normalised here. Validate against the torch reference when first exported.
"""

from __future__ import annotations

import numpy as np

from core.onnx_embedding import center_window, l2_normalize, load_audio_mono, make_singleton

_MODEL_FILE = "panns_cnn14.onnx"
_SR = 32_000
_WINDOW = 30 * _SR  # central 30 s

_get = make_singleton(_MODEL_FILE, ["embedding"])


def embed(path: str) -> np.ndarray | None:
    audio = load_audio_mono(path, _SR)
    if audio.size == 0:
        return None
    audio = center_window(audio, _WINDOW)
    out = _get().run({"waveform": audio[None, :]})[0]
    return l2_normalize(np.asarray(out, dtype=np.float32).ravel())
