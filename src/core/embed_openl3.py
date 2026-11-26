"""OpenL3 (music) embedding backend (runtime, ONNX).

OpenL3 is a self-supervised audio-visual-correspondence embedding with a
music-trained variant — a solid timbral/texture representation, lighter than
MERT and with no harmonic awareness. A middle ground between PANNs and MERT.

OpenL3 embeds fixed 1-second windows; a track embedding is the mean over its
windows. The exported graph keeps OpenL3's kapre mel frontend in-graph, so the
runtime only frames the audio.

Runtime contract (the export script must honour this):
  * input ``frames``: float32 ``[F, 48000, 1]`` — ``F`` one-second 48 kHz mono
    windows. (Confirm the trailing channel axis matches the exported graph;
    some tf2onnx exports want ``[F, 1, 48000]`` — adjust ``_BATCH_AXIS`` then.)
  * output ``embedding``: float32 ``[F, 512]`` (mel256, 512-d music model).

The per-window embeddings are mean-pooled then L2-normalised. Validate the
frame layout + output dim against the openl3 reference when first exported.
"""

from __future__ import annotations

import numpy as np

from core.onnx_embedding import frames, l2_normalize, load_audio_mono, make_singleton

_MODEL_FILE = "openl3_music_mel256_512.onnx"
_SR = 48_000
_WIN = _SR  # 1 s windows
_HOP = _SR // 2  # 0.5 s hop
_MAX_FRAMES = 48  # cap windows per track to bound inference time

_get = make_singleton(_MODEL_FILE, ["embedding"])


def embed(path: str) -> np.ndarray | None:
    audio = load_audio_mono(path, _SR)
    if audio.size == 0:
        return None
    windows = frames(audio, _WIN, _HOP, max_frames=_MAX_FRAMES)
    if not windows:
        return None
    batch = np.stack(windows).astype(np.float32)[..., None]  # [F, 48000, 1]
    out = np.asarray(_get().run({"frames": batch})[0], dtype=np.float32)  # [F, 512]
    return l2_normalize(out.mean(axis=0))
