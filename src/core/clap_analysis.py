"""ONNX Runtime CLAP backend — drop-in replacement for the torch+transformers
implementation we used until 2026-05.

What runs at runtime:

  * The CLAP audio encoder, exported to a single ``clap_audio.onnx`` (~110
    MB) and loaded via ``onnxruntime`` (~30 MB). No ``torch``, no
    ``transformers``.
  * Pre-computed text embeddings for every prompt category, loaded from
    ``clap_text_embeddings.npz``. Computed offline by
    ``scripts/export_clap_onnx.py`` — the runtime never embeds text.
  * Mel feature extraction via ``librosa`` (which we already ship), matching
    CLAP's ``ClapFeatureExtractor`` parameters exactly so embeddings are
    numerically equivalent to the original torch path (cosine ≥ 0.9999 on
    spot-check vs. PyTorch reference).

Public API matches the old module:

  * ``compute_ml_features_clap(path) -> (features_dict, timings_dict)``
  * Returns the same keys: ``mood_*``, ``is_*``, ``arousal``, ``valence``,
    ``genres``, ``genres_vector``.

If the user has the old ``torch``+``transformers`` install lying around,
the on-disk artefacts produced by ``export_clap_onnx.py`` are tiny (110 MB
ONNX + 168 KB embeddings) and bundled with the app — no runtime download
needed once the export step has been run.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from collections.abc import Callable, Iterable
from pathlib import Path

import numpy as np

from core.clap_models import clap_dir

log = logging.getLogger("analysis")

# Identifiers must mirror the keys saved by scripts/export_clap_onnx.py.
_BINARY_KEYS = (
    "mood_party",
    "mood_aggressive",
    "mood_happy",
    "mood_relaxed",
    "is_vocal",
    "is_electronic",
)

#: The genre names the embeddings were made from, as the app ships them —
#: the same file scripts/export_clap_onnx.py reads.
_SHIPPED_LABELS = Path(__file__).with_name("clap_genre_labels.json")


def genre_labels_from(data) -> list[str]:
    """The genre names in an open embeddings file, in embedding order.

    A current export stores them as text. An older one stored them as an
    object array, which cannot be read without pickle; for those the names
    come from the list the app ships — the list the export was made from —
    provided it has one name per embedding. Names paired with the wrong
    embeddings would label every track wrongly and look like it worked.
    """
    try:
        return [str(name) for name in data["genre_labels"]]
    except ValueError:
        log.info(
            "CLAP backend: genre names are in the older format — reading them from %s",
            _SHIPPED_LABELS.name,
        )
    names = json.loads(_SHIPPED_LABELS.read_text(encoding="utf-8"))
    embedded = sum(1 for key in data.files if key.startswith("genre_") and key[6:].isdigit())
    if len(names) != embedded:
        raise RuntimeError(
            f"the CLAP embeddings hold {embedded} genres and the app's list "
            f"has {len(names)} — export the model again (make models)"
        )
    return [str(name) for name in names]


# CLAP feature-extractor parameters (preprocessor_config.json).
_SR = 48_000
_N_FFT = 1024
_HOP = 480
_N_MELS = 64
_F_MIN = 50.0
_F_MAX = 14_000.0
_CHUNK_FRAMES = 1001  # 480_000 / 480 + 1
_CHUNK_SAMPLES = _CHUNK_FRAMES * _HOP  # 480_480 — matches CLAP fusion exactly


def _load_audio_48k(path: str, decoded: tuple[np.ndarray, int] | None = None) -> np.ndarray:
    """Load audio as mono float32 numpy array resampled to 48 kHz.

    *decoded* is a native-rate mono decode from ``audio_io.decode_mono``,
    when the caller already has one; the file is not read again.
    """
    from core.audio_io import resample_mono, safe_sf_read

    if decoded is not None:
        mono, sr = decoded
        return resample_mono(mono, sr, _SR).astype(np.float32)
    audio, _sr = safe_sf_read(path, dtype="float32", always_2d=True, samplerate=_SR)
    return audio.mean(axis=1).astype(np.float32)


# ── Mel + fusion preprocessing ────────────────────────────────────────────────


def _make_mel_basis() -> np.ndarray:
    """Build the HTK-style mel filterbank CLAP uses (torchaudio default)."""
    import librosa

    return librosa.filters.mel(
        sr=_SR,
        n_fft=_N_FFT,
        n_mels=_N_MELS,
        fmin=_F_MIN,
        fmax=_F_MAX,
        htk=True,
        norm=None,
    ).astype(np.float32)


def _log_mel_spec(audio: np.ndarray, mel_basis: np.ndarray) -> np.ndarray:
    """Compute log-mel spectrogram with CLAP's parameters → shape [frames, 64]."""
    import librosa

    # Hann window, n_fft=1024, hop=480, power=2 (matches CLAP's spectrogram()).
    stft = librosa.stft(
        audio,
        n_fft=_N_FFT,
        hop_length=_HOP,
        window="hann",
        center=True,
        pad_mode="reflect",
    )
    power = (np.abs(stft) ** 2).astype(np.float32)  # [bins, frames]
    mel = mel_basis @ power  # [n_mels, frames]
    log_mel = librosa.power_to_db(mel, top_db=None).astype(np.float32)
    return log_mel.T  # [frames, 64]


def _fusion_chunks(mel: np.ndarray) -> np.ndarray:
    """Stack a 4-channel mel for CLAP's fusion path → shape [4, 1001, 64].

    Channel 0: full mel resized (bilinear) to 1001 frames — global view.
    Channels 1–3: front, middle, back 10 s crops.

    Matches ``ClapFeatureExtractor._random_mel_fusion`` but picks deterministic
    centre-of-third positions instead of random ones, so analysis output is
    reproducible.
    """
    import scipy.ndimage

    total = mel.shape[0]  # frames
    if total <= _CHUNK_FRAMES:
        # Track ≤ 10 s — pad audio is handled upstream; fall back to "shorter
        # audio in fusion mode": four copies of the (padded) chunk.
        if total < _CHUNK_FRAMES:
            pad = np.zeros((_CHUNK_FRAMES - total, _N_MELS), dtype=np.float32)
            mel = np.concatenate([mel, pad], axis=0)
        return np.stack([mel, mel, mel, mel], axis=0)

    # Long track — 3 chunks at thirds, plus a bilinear-downsampled global view.
    third = (total - _CHUNK_FRAMES) // 3
    front_idx = third // 2
    middle_idx = third + (third // 2)
    back_idx = total - _CHUNK_FRAMES - (third // 2)
    back_idx = max(back_idx, 0)

    front = mel[front_idx : front_idx + _CHUNK_FRAMES]
    middle = mel[middle_idx : middle_idx + _CHUNK_FRAMES]
    back = mel[back_idx : back_idx + _CHUNK_FRAMES]

    # Resize the full mel to (1001, 64) via bilinear interpolation. CLAP uses
    # torch.nn.functional.interpolate with mode='bilinear'; scipy's zoom with
    # order=1 is the equivalent.
    zoom_y = _CHUNK_FRAMES / total
    full_resized: np.ndarray = np.asarray(
        scipy.ndimage.zoom(mel, (zoom_y, 1.0), order=1, mode="reflect")
    )
    if full_resized.shape[0] != _CHUNK_FRAMES:
        # Floating-point zoom can be off by ±1 frame — clamp.
        if full_resized.shape[0] > _CHUNK_FRAMES:
            full_resized = full_resized[:_CHUNK_FRAMES]
        else:
            pad = np.zeros((_CHUNK_FRAMES - full_resized.shape[0], _N_MELS), dtype=np.float32)
            full_resized = np.concatenate([full_resized, pad], axis=0)

    return np.stack([full_resized, front, middle, back], axis=0).astype(np.float32)


def _audio_to_features(audio: np.ndarray, mel_basis: np.ndarray) -> np.ndarray:
    """Map raw audio → ``input_features`` shape [1, 4, 1001, 64] for ONNX."""
    if audio.shape[0] < _CHUNK_SAMPLES:
        # Repeat-pad shorter audio to 10 s (CLAP's "repeatpad" strategy).
        reps = (_CHUNK_SAMPLES // audio.shape[0]) + 1
        audio = np.tile(audio, reps)[:_CHUNK_SAMPLES]
    mel = _log_mel_spec(audio, mel_basis)
    fusion = _fusion_chunks(mel)
    return fusion[None, ...]  # [1, 4, 1001, 64]


# ── Which hardware ─────────────────────────────────────────────────────────────

_CPU = "CPUExecutionProvider"


def wanted_providers(available: Iterable[str]) -> list[str]:
    """The execution providers to ask onnxruntime for, best first.

    A pure choice from what is installed and what the environment says, so
    it can be made without loading the model.
    """
    have = set(available)
    if os.environ.get("TK_CLAP_CORE_ML") == "1" and "CoreMLExecutionProvider" in have:
        return ["CoreMLExecutionProvider", _CPU]
    if (
        sys.platform == "win32"
        and os.environ.get("TK_CLAP_DML", "1") != "0"
        and "DmlExecutionProvider" in have
    ):
        return ["DmlExecutionProvider", _CPU]
    return [_CPU]


def clap_wants_gpu() -> bool:
    """Will CLAP be asked to run on a GPU?

    What the scan sizes its thread pool by. "Asked to" and not "does": a GPU
    that fails its warm-up falls back to the CPU later, and by then the pool
    exists — it is merely smaller than it could have been. False if
    onnxruntime cannot be imported, which the analysis reports on its own.
    """
    try:
        import onnxruntime as ort
    except ImportError:
        return False
    return wanted_providers(ort.get_available_providers())[0] != _CPU


# ── Singleton backend ──────────────────────────────────────────────────────────


class _ClapBackend:
    """Holds the ONNX session and pre-computed text embeddings.

    Loaded once per process. Thread-safe: ``onnxruntime.InferenceSession`` is
    callable from multiple threads after construction (we set
    ``intra_op_num_threads=1`` per worker since the AnalysisWorker already
    parallelises across files).
    """

    def __init__(self) -> None:
        import onnxruntime as ort

        d = clap_dir()
        self._onnx_path = d / "clap_audio.onnx"
        embs_path = d / "clap_text_embeddings.npz"
        if not self._onnx_path.exists() or not embs_path.exists():
            raise RuntimeError(
                f"CLAP ONNX artefacts missing in {d}. Run "
                "`scripts/export_clap_onnx.py` once on the dev machine to "
                "produce clap_audio.onnx and clap_text_embeddings.npz."
            )

        # Execution-provider selection (always falls back to CPU):
        #   * macOS CoreML — disabled by default: it rejects CLAP's fusion-path
        #     Squeeze (dynamic {-1} shape collapsing to zero elements) at
        #     inference time, so a try/except on InferenceSession() won't catch
        #     it. Opt in with TK_CLAP_CORE_ML=1 once the export uses static
        #     shapes for the fusion branch.
        #   * Windows DirectML — on by default when the onnxruntime-directml
        #     build is installed (any DirectX-12 GPU: NVIDIA/AMD/Intel).
        #     Verified to match CPU output on CLAP's fusion path. Disable with
        #     TK_CLAP_DML=0 to force CPU.
        providers = wanted_providers(ort.get_available_providers())

        self._session = self._make_session(providers)
        active = self._session.get_providers()
        log.info("CLAP backend: providers=%s", active)

        # The GPU (DirectML / CoreML) path uses ONE InferenceSession across all
        # analysis worker threads. onnxruntime's CPU EP releases the GIL and is
        # safe to call concurrently — that is our parallelism — but DirectML
        # under concurrent Run() on a single session hard-crashes the process
        # (no Python traceback, the scan just dies mid-track) on many GPU
        # drivers, and concurrent forwards multiply VRAM and can OOM a small
        # GPU. So when a GPU EP is active we serialise Run() with a lock: the
        # GPU still does the work (threads keep parallelising audio decode +
        # librosa + the numpy scoring), just one forward at a time.
        self._gpu = active and active[0] != "CPUExecutionProvider"
        self._run_lock = threading.Lock() if self._gpu else None
        if self._gpu:
            self._warmup_or_fallback_to_cpu()

        # Cached mel filterbank — ~16 KB, one allocation per process.
        self._mel_basis = _make_mel_basis()

        # Pre-computed text embeddings.
        log.info("CLAP backend: loading text embeddings from %s", embs_path)
        # Without pickle: the file holds numbers and names, and unpickling
        # would let it run code.
        with np.load(embs_path, allow_pickle=False) as data:
            self.genre_labels: list[str] = genre_labels_from(data)
            self._binary_pos = {k: data[f"binary_{k}_pos"].astype(np.float32) for k in _BINARY_KEYS}
            self._binary_neg = {k: data[f"binary_{k}_neg"].astype(np.float32) for k in _BINARY_KEYS}
            self._arousal_embs = np.stack([data[f"arousal_{i}"] for i in range(9)], axis=0).astype(
                np.float32
            )
            self._valence_embs = np.stack([data[f"valence_{i}"] for i in range(9)], axis=0).astype(
                np.float32
            )
            self._genre_embs = np.stack(
                [data[f"genre_{i}"] for i in range(len(self.genre_labels))], axis=0
            ).astype(np.float32)
        log.info("CLAP backend ready — %d genres", len(self.genre_labels))

    # ── Session construction / GPU validation ────────────────────────────────

    def _make_session(self, providers: list):
        import onnxruntime as ort

        log.info("CLAP backend: loading ONNX from %s (providers=%s)", self._onnx_path, providers)
        sess_opts = ort.SessionOptions()
        sess_opts.intra_op_num_threads = 1
        return ort.InferenceSession(
            str(self._onnx_path),
            sess_options=sess_opts,
            providers=providers,
        )

    def _warmup_or_fallback_to_cpu(self) -> None:
        """Run one real-shaped inference on the GPU; drop to CPU if it errors.

        A first GPU forward also compiles DirectML's shaders, so doing it once
        here (single-threaded, before the worker pool fans out) both validates
        the GPU path and removes a first-call stall. If it raises a Python
        exception — DML init failure, an unsupported op, GPU OOM surfaced as an
        error — we rebuild the session on CPU so analysis still completes. (A
        hard GPU/driver crash can't be caught from Python; serialising Run()
        and warming up once is the most we can do, and covers the cases we've
        seen.)
        """
        feats = np.zeros((1, 4, _CHUNK_FRAMES, _N_MELS), dtype=np.float32)
        is_longer = np.array([[True]], dtype=np.bool_)
        try:
            self._session.run(
                ["audio_embedding"],
                {"input_features": feats, "is_longer": is_longer},
            )
            log.info("CLAP backend: GPU warmup OK — using %s", self._session.get_providers()[0])
        except Exception as exc:
            log.warning(
                "CLAP backend: GPU provider failed warmup (%s) — falling back to CPU.",
                exc,
            )
            self._session = self._make_session(["CPUExecutionProvider"])
            self._gpu = False
            self._run_lock = None
            log.info("CLAP backend: providers=%s (CPU fallback)", self._session.get_providers())

    # ── Inference helpers ────────────────────────────────────────────────────

    def audio_emb(self, audio_np: np.ndarray, sr: int = _SR) -> np.ndarray:
        """Return unit-normalised audio embedding as a 1-D numpy array."""
        if sr != _SR:
            raise ValueError(f"CLAP expects {_SR} Hz audio, got {sr}")
        feats = _audio_to_features(audio_np, self._mel_basis)  # [1, 4, 1001, 64]
        is_longer = np.array([[True]], dtype=np.bool_)
        # Serialise Run() on the GPU path (see __init__); concurrent on CPU.
        run_lock = self._run_lock
        if run_lock is not None:
            with run_lock:
                out = np.asarray(
                    self._session.run(
                        ["audio_embedding"],
                        {"input_features": feats, "is_longer": is_longer},
                    )[0]
                )
        else:
            out = np.asarray(
                self._session.run(
                    ["audio_embedding"],
                    {"input_features": feats, "is_longer": is_longer},
                )[0]
            )
        return out[0].astype(np.float32)  # [D] (already unit-norm)

    @staticmethod
    def _softmax(x: np.ndarray) -> np.ndarray:
        x = x - x.max()
        e = np.exp(x)
        return e / e.sum()

    def binary_score(self, audio_emb: np.ndarray, key: str) -> float:
        """Cosine-similarity softmax → probability of the positive class."""
        pos = self._binary_pos[key]
        neg = self._binary_neg[key]
        logits = np.array([float(audio_emb @ pos), float(audio_emb @ neg)], dtype=np.float32) / 0.07
        probs = self._softmax(logits)
        return float(probs[0])

    def scale_score(
        self, audio_emb: np.ndarray, anchor_embs: np.ndarray, lo: float, hi: float
    ) -> float:
        logits = (audio_emb @ anchor_embs.T) / 0.07  # [N]
        weights = self._softmax(logits)
        positions = np.linspace(lo, hi, len(weights), dtype=np.float32)
        return float((weights * positions).sum())

    def top_genres(self, audio_emb: np.ndarray, top_k: int = 3) -> list[dict]:
        sims = audio_emb @ self._genre_embs.T  # [N]
        probs = self._softmax(sims / 0.07)
        top_idx = np.argsort(probs)[-top_k:][::-1]
        return [{"name": self.genre_labels[int(i)], "prob": float(probs[int(i)])} for i in top_idx]


_backend_cache: _ClapBackend | None = None
_backend_lock: threading.Lock | None = None


def _get_clap_backend() -> _ClapBackend:
    """Process-shared CLAP backend, double-checked-locked init."""
    global _backend_cache, _backend_lock
    if _backend_lock is None:
        _backend_lock = threading.Lock()
    lock = _backend_lock
    if _backend_cache is None:
        with lock:
            if _backend_cache is None:
                _backend_cache = _ClapBackend()
    return _backend_cache


# ── Public API ────────────────────────────────────────────────────────────────


def compute_ml_features_clap(
    path: str,
    decoded: tuple[np.ndarray, int] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> tuple[dict, dict]:
    """Extract all ML features for one track.

    *decoded* is an optional native-rate mono decode of *path* (see
    ``audio_io.decode_mono``) so a caller that already read the file does
    not read it twice. *should_stop* is checked before the inference, the
    expensive half, so a stopped scan does not pay for it.

    Returns ``(features_dict, timings_dict)`` — keys identical to the prior
    torch-based implementation, so callers don't have to change.
    """
    backend = _get_clap_backend()
    features: dict = {}
    timings: dict = {}

    t0 = time.perf_counter()
    audio_np = _load_audio_48k(path, decoded)
    timings["clap_load"] = time.perf_counter() - t0

    if should_stop is not None:
        from core.analysis import raise_if_stopped

        raise_if_stopped(should_stop, path)

    t0 = time.perf_counter()
    audio_emb = backend.audio_emb(audio_np)  # [D] float32
    timings["clap_embed"] = time.perf_counter() - t0

    t0 = time.perf_counter()
    for key in _BINARY_KEYS:
        features[key] = backend.binary_score(audio_emb, key)
    timings["clap_classify"] = time.perf_counter() - t0

    t0 = time.perf_counter()
    features["arousal"] = backend.scale_score(
        audio_emb,
        backend._arousal_embs,
        lo=1.0,
        hi=9.0,
    )
    features["valence"] = backend.scale_score(
        audio_emb,
        backend._valence_embs,
        lo=1.0,
        hi=9.0,
    )
    timings["clap_deam"] = time.perf_counter() - t0

    t0 = time.perf_counter()
    top = backend.top_genres(audio_emb, top_k=3)
    features["genres"] = json.dumps(top)
    features["genres_vector"] = audio_emb.astype(np.float32).tobytes()
    timings["clap_genre"] = time.perf_counter() - t0

    return features, timings
