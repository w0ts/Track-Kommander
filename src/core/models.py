"""Essentia ML model download and cache management.

**Not wired into the app, and deliberately so — read this before calling it.**

Nothing in ``src/`` imports this module. The analysis pipeline moved to the
ONNX backends in :mod:`core.embeddings` (and CLAP in :mod:`core.clap_models`);
this downloader is kept because it is the only code that knows where the
Essentia classifiers live and how to fetch them, and re-deriving that later
would be pure waste. Treat it as a tool, not as part of the product.

**Licence trap.** Every model in ``MODELS`` below is published by the MTG
(Universitat Pompeu Fabra) under **CC BY-NC-SA 4.0** — non-commercial and
share-alike. Track Kommander's shipping build downloads and bundles none of
them, which is what keeps the binaries redistributable. Calling
``ensure_models_downloaded()`` from app code, or shipping anything it fetches,
makes the product non-commercial-only and forces share-alike on derived
output. If you enable any of it, say so prominently and update
``THIRD_PARTY.md`` first. A proprietary licence is available from the MTG on
request — see <https://essentia.upf.edu/models.html>.

Models are downloaded from the MTG Essentia model repository on first use
and cached locally.  The download directory is:
  - Frozen app : ~/Library/Application Support/Track Kommander/models/
  - Development: <project_root>/models/

Call ensure_models_downloaded() before running ML inference, passing an
optional progress callback to surface download progress in the UI.
"""

from __future__ import annotations

import logging
import ssl
import urllib.request
from collections.abc import Callable
from pathlib import Path

from core.constants import APP_NAME

log = logging.getLogger("models")

_BASE_URL = "https://essentia.upf.edu/models"

# ── Model registry ────────────────────────────────────────────────────────────
# key → (local_filename, remote_path_relative_to_BASE_URL)
# Grouped by role for clarity.
MODELS: dict[str, tuple[str, str]] = {
    # ── Embedding extractors ──────────────────────────────────────────────────
    # Note: msd-musicnn-1 (standalone embedding extractor) is NOT listed here.
    # The mood / voice classifiers are full MusiCNN models that include their
    # own spectrogram extraction and do not depend on a shared embedding model.
    "effnet": (
        "discogs-effnet-bs64-1.pb",
        "feature-extractors/discogs-effnet/discogs-effnet-bs64-1.pb",
    ),
    "vggish": (
        "audioset-vggish-3.pb",
        "feature-extractors/vggish/audioset-vggish-3.pb",
    ),
    # ── Genre (EffNet embeddings) ─────────────────────────────────────────────
    "genre400": (
        "genre_discogs400-discogs-effnet-1.pb",
        "classification-heads/genre_discogs400/genre_discogs400-discogs-effnet-1.pb",
    ),
    "genre400_json": (
        "genre_discogs400-discogs-effnet-1.json",
        "classification-heads/genre_discogs400/genre_discogs400-discogs-effnet-1.json",
    ),
    # ── Mood / style (MusiCNN embeddings) ─────────────────────────────────────
    "mood_party": (
        "mood_party-musicnn-msd-2.pb",
        "classifiers/mood_party/mood_party-musicnn-msd-2.pb",
    ),
    "mood_aggressive": (
        "mood_aggressive-musicnn-msd-2.pb",
        "classifiers/mood_aggressive/mood_aggressive-musicnn-msd-2.pb",
    ),
    "mood_happy": (
        "mood_happy-musicnn-msd-2.pb",
        "classifiers/mood_happy/mood_happy-musicnn-msd-2.pb",
    ),
    "mood_relaxed": (
        "mood_relaxed-musicnn-msd-2.pb",
        "classifiers/mood_relaxed/mood_relaxed-musicnn-msd-2.pb",
    ),
    "mood_acoustic": (
        "mood_acoustic-musicnn-msd-2.pb",
        "classifiers/mood_acoustic/mood_acoustic-musicnn-msd-2.pb",
    ),
    # ── Voice detection (MusiCNN embeddings) ──────────────────────────────────
    "voice_inst": (
        "voice_instrumental-musicnn-msd-2.pb",
        "classifiers/voice_instrumental/voice_instrumental-musicnn-msd-2.pb",
    ),
    # ── Arousal / Valence — DEAM (VGGish embeddings) ─────────────────────────
    # Two separate models: -1 = arousal, -2 = valence
    "deam_arousal": (
        "deam-audioset-vggish-1.pb",
        "classification-heads/deam/deam-audioset-vggish-1.pb",
    ),
    "deam_valence": (
        "deam-audioset-vggish-2.pb",
        "classification-heads/deam/deam-audioset-vggish-2.pb",
    ),
}

# Human-readable labels for the timing display
MODEL_LABELS: dict[str, str] = {
    "effnet": "EffNet embeddings",
    "vggish": "VGGish embeddings",
    "genre400": "Genre Discogs400",
    "mood_party": "Mood: party",
    "mood_aggressive": "Mood: aggressive",
    "mood_happy": "Mood: happy",
    "mood_relaxed": "Mood: relaxed",
    "mood_acoustic": "Mood: acoustic/electronic",
    "voice_inst": "Voice/Instrumental",
    "deam_arousal": "Arousal (DEAM)",
    "deam_valence": "Valence (DEAM)",
}


# ── Path helpers ──────────────────────────────────────────────────────────────


def models_dir() -> Path:
    from core.runtime import bundle_data_dir, is_frozen

    if is_frozen():
        p = Path.home() / "Library" / "Application Support" / APP_NAME / "models"
    else:
        p = bundle_data_dir() / "models"
    p.mkdir(parents=True, exist_ok=True)
    return p


def get_model_path(key: str) -> Path:
    filename, _ = MODELS[key]
    return models_dir() / filename


def all_models_present() -> bool:
    return all(get_model_path(k).exists() for k in MODELS)


def missing_models() -> list[str]:
    return [k for k in MODELS if not get_model_path(k).exists()]


# ── Download ──────────────────────────────────────────────────────────────────

ProgressCb = Callable[[str, int, int], None]  # (filename, current, total)


def _make_reporthook(filename: str, last_pct_ref: list[int]) -> Callable[[int, int, int], None]:
    """Create a download progress callback that logs every 10%."""

    def _reporthook(block_num: int, block_size: int, total_size: int) -> None:
        if total_size <= 0:
            return
        downloaded = block_num * block_size
        pct = min(int(downloaded * 100 / total_size), 100)
        if pct >= last_pct_ref[0] + 10:  # log every 10 %
            last_pct_ref[0] = pct
            mb_done = downloaded / 1_048_576
            mb_total = total_size / 1_048_576
            log.info("      %s  %d%%  (%.1f / %.1f MB)", filename, pct, mb_done, mb_total)

    return _reporthook


def ensure_models_downloaded(progress_cb: ProgressCb | None = None) -> None:
    """Download any missing model files.

    progress_cb(filename, current_index, total_missing) is called before each
    download so the UI can show progress.  Raises on the first download failure.
    """
    missing = missing_models()
    if not missing:
        return

    total = len(missing)
    log.info("Downloading %d missing Essentia model(s)…", total)

    for i, key in enumerate(missing):
        filename, remote_path = MODELS[key]
        url = f"{_BASE_URL}/{remote_path}"
        dest = models_dir() / filename

        if progress_cb:
            progress_cb(filename, i, total)

        log.info("  [%d/%d] %s  ← %s", i + 1, total, filename, url)
        try:
            last_pct = [-1]

            try:
                import certifi as _certifi

                _ctx = ssl.create_default_context(cafile=_certifi.where())
            except ImportError:
                _ctx = ssl.create_default_context()
            opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=_ctx))
            reporthook = _make_reporthook(filename, last_pct)
            with opener.open(url) as resp:
                total_size = int(resp.headers.get("Content-Length", 0))
                block_size = 65536
                block_num = 0
                with open(dest, "wb") as fout:
                    while True:
                        chunk = resp.read(block_size)
                        if not chunk:
                            break
                        fout.write(chunk)
                        block_num += 1
                        reporthook(block_num, block_size, total_size)
            log.info("      %s  done", filename)
        except Exception as exc:
            dest.unlink(missing_ok=True)  # remove partial file
            raise RuntimeError(f"Failed to download {filename}: {exc}") from exc

    log.info("All Essentia models ready in %s", models_dir())
