"""Shared ONNX-Runtime plumbing for pluggable embedding backends.

Extracts the provider-selection and thread-safe singleton-session machinery the
CLAP backend (:mod:`core.clap_analysis`) proved out in production, so each new
embedding model — MERT, PANNs, OpenL3, Discogs-EffNet — is a thin module that
only supplies three things:

  1. its ONNX filename (resolved in :func:`core.embeddings.embed_models_dir`),
  2. preprocessing: a file path → input tensor(s),
  3. pooling: the model's raw output → a 1-D float32 vector.

Like the CLAP path, the runtime stays torch/TF-free: inference is ``onnxruntime``
and feature extraction is ``librosa``. Each backend's preprocessing MUST be
validated against the reference framework when its artefact is first exported
(cosine ≥ 0.999 on a spot-check), exactly as ``scripts/export_clap_onnx.py``
documents for CLAP.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
from pathlib import Path

import numpy as np

from core.embeddings import embed_models_dir

log = logging.getLogger("analysis")


def select_providers() -> list[str]:
    """ONNX execution providers, mirroring the CLAP backend's policy.

    CPU everywhere by default; DirectML on Windows (any DX12 GPU) unless
    ``TK_EMBED_DML=0``; CoreML on macOS only when ``TK_EMBED_CORE_ML=1`` (off by
    default — CoreML rejects some dynamic-shape graphs). Always falls back to CPU.
    """
    import onnxruntime as ort

    available = set(ort.get_available_providers())
    if os.environ.get("TK_EMBED_CORE_ML") == "1" and "CoreMLExecutionProvider" in available:
        return ["CoreMLExecutionProvider", "CPUExecutionProvider"]
    if (
        sys.platform == "win32"
        and os.environ.get("TK_EMBED_DML", "1") != "0"
        and "DmlExecutionProvider" in available
    ):
        return ["DmlExecutionProvider", "CPUExecutionProvider"]
    return ["CPUExecutionProvider"]


class OnnxEmbedder:
    """A loaded ONNX session with thread-safe Run() (serialised on GPU).

    onnxruntime's CPU EP releases the GIL and is safe to call concurrently — that
    is the analysis pool's parallelism. GPU EPs (DirectML/CoreML) can hard-crash
    under concurrent Run() on one session, so those are serialised with a lock,
    matching the CLAP backend.
    """

    def __init__(self, onnx_path: Path, output_names: list[str]) -> None:
        import onnxruntime as ort

        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 1
        self._session = ort.InferenceSession(
            str(onnx_path),
            sess_options=opts,
            providers=select_providers(),
        )
        active = self._session.get_providers()
        self._gpu = bool(active) and active[0] != "CPUExecutionProvider"
        self._lock: threading.Lock | None = threading.Lock() if self._gpu else None
        self._outputs = output_names
        log.info("embedder %s: providers=%s", onnx_path.name, active)

    def run(self, feeds: dict) -> list:
        if self._lock is not None:
            with self._lock:
                return list(self._session.run(self._outputs, feeds))
        return list(self._session.run(self._outputs, feeds))


def make_singleton(model_file: str, output_names: list[str]):
    """Return a ``get()`` accessor for a process-shared, lazily-loaded embedder.

    Each backend module calls this once at import to get its own double-checked
    singleton — the heavy session (and onnxruntime) loads only on first use.
    """
    cache: dict[str, OnnxEmbedder] = {}
    lock = threading.Lock()

    def get() -> OnnxEmbedder:
        if "be" not in cache:
            with lock:
                if "be" not in cache:
                    cache["be"] = OnnxEmbedder(embed_models_dir() / model_file, output_names)
        return cache["be"]

    return get


# ── shared preprocessing helpers ───────────────────────────────────────────────


def load_audio_mono(path: str, sr: int) -> np.ndarray:
    """Decode a file to mono float32 at the given sample rate."""
    from core.audio_io import safe_sf_read

    audio, _sr = safe_sf_read(path, dtype="float32", always_2d=True, samplerate=sr)
    return audio.mean(axis=1).astype(np.float32)


def log_mel(
    audio: np.ndarray,
    sr: int,
    n_fft: int,
    hop: int,
    n_mels: int,
    fmin: float = 0.0,
    fmax: float | None = None,
    htk: bool = False,
    power: float = 2.0,
) -> np.ndarray:
    """Log-mel spectrogram → shape [frames, n_mels], float32 (dB scale)."""
    import librosa

    stft = librosa.stft(
        audio, n_fft=n_fft, hop_length=hop, window="hann", center=True, pad_mode="reflect"
    )
    spec = (np.abs(stft) ** power).astype(np.float32)
    basis = librosa.filters.mel(
        sr=sr, n_fft=n_fft, n_mels=n_mels, fmin=fmin, fmax=fmax, htk=htk
    ).astype(np.float32)
    mel = basis @ spec
    return librosa.power_to_db(mel, top_db=None).astype(np.float32).T


def l2_normalize(v: np.ndarray) -> np.ndarray:
    """Return ``v`` scaled to unit L2 norm (unchanged if the norm is ~0)."""
    n = float(np.linalg.norm(v))
    return (v / n).astype(np.float32) if n > 1e-12 else v.astype(np.float32)


def center_window(audio: np.ndarray, n: int) -> np.ndarray:
    """A length-``n`` slice centred on the track midpoint (zero-padded if short).

    Bounds per-track compute for whole-track models (MERT, PANNs) the same way
    the melodic feature samples a central window rather than the full file.
    """
    if audio.size < n:
        return np.pad(audio, (0, n - audio.size))
    if audio.size == n:
        return audio
    start = (audio.size - n) // 2
    return audio[start : start + n]


def frames(audio: np.ndarray, win: int, hop: int, max_frames: int = 64) -> list[np.ndarray]:
    """Split audio into ``win``-length frames every ``hop`` samples.

    For frame-pooled models (OpenL3). Caps the frame count by evenly subsampling
    so a long track doesn't blow up inference time; a short track yields one
    zero-padded frame.
    """
    if audio.size < win:
        audio = np.pad(audio, (0, win - audio.size))
    starts = list(range(0, audio.size - win + 1, hop)) or [0]
    if len(starts) > max_frames:
        idx = np.linspace(0, len(starts) - 1, max_frames).astype(int)
        starts = [starts[i] for i in idx]
    return [audio[s : s + win] for s in starts]
