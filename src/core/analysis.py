"""Audio analysis — librosa signal features + CLAP ML backend.

Architecture
------------
Signal features (no ML):
  Danceability, DynamicComplexity, HFC, SpectralCentroid
  → computed via librosa at 22 kHz, cross-platform

ML features (PyTorch/CLAP):
  CLAP zero-shot classification via laion/clap-htsat-fused
  → mood_party, mood_aggressive, mood_happy, mood_relaxed
  → voice/instrumental, acoustic/electronic
  → genre (zero-shot, 400-class), arousal/valence

BPM / Key:
  Always taken from Traktor metadata when available.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable

import numpy as np

from core.audio_meta import read_duration
from core.collection_source import collection_metadata
from core.rekordbox import camelot, key_parts
from core.traktor import find_traktor_nml

log = logging.getLogger("analysis")


class AnalysisCancelled(Exception):
    """The scan was stopped while this track was being analysed.

    Raised between stages so a stop does not wait for a whole track, and so
    the half-analysed track is never stored as if it were complete.
    """


def raise_if_stopped(should_stop: Callable[[], bool] | None, path: str) -> None:
    if should_stop is not None and should_stop():
        raise AnalysisCancelled(path)


# ── Traktor collection cache ───────────────────────────────────────────────────
_traktor_collection: dict[str, dict] | None = None
# The scan's worker threads all read the collection. Without the lock, the
# first scan had every one of them find it missing and parse the whole
# collection.nml at once.
_traktor_collection_lock = threading.Lock()


def _load_traktor_collection() -> dict[str, dict]:
    return collection_metadata(find_traktor_nml())


def _get_traktor_collection() -> dict[str, dict]:
    global _traktor_collection
    collection = _traktor_collection
    if collection is None:
        with _traktor_collection_lock:
            if _traktor_collection is None:
                _traktor_collection = _load_traktor_collection()
            collection = _traktor_collection
    return collection


def preload_traktor_collection() -> None:
    """Parse the collection now, so the scan's worker threads find it ready."""
    _get_traktor_collection()


def reload_traktor_collection() -> None:
    global _traktor_collection
    with _traktor_collection_lock:
        _traktor_collection = None


def collection_lists(path: str) -> bool:
    """Does Traktor's collection have an entry for *path*?

    What separates a track whose tags are empty from one nothing is known
    about. :func:`read_metadata` returns the same blanks for both.
    """
    return path in _get_traktor_collection()


def read_metadata(path: str) -> tuple[str, str, str, str, int, float, int, dict]:
    """Return (artist, album, title, genre, musical_key, traktor_bpm, rating, extra).

    ``extra`` carries display-only metadata (release_date, import_date, label,
    bitrate, play_count) from the Traktor collection.

    Blank for a track the collection does not list. Before writing these
    over something, ask :func:`collection_lists`.
    """
    meta = _get_traktor_collection().get(path, {})
    extra = {
        "release_date": meta.get("release_date", ""),
        "import_date": meta.get("import_date", ""),
        "label": meta.get("label", ""),
        "bitrate": meta.get("bitrate"),
        "play_count": meta.get("play_count"),
    }
    return (
        meta.get("artist", ""),
        meta.get("album", ""),
        meta.get("title", ""),
        meta.get("genre", ""),
        int(meta.get("musical_key", -1)),
        float(meta.get("traktor_bpm", 0.0)),
        int(meta.get("rating", 0)),
        extra,
    )


def traktor_key_to_fields(musical_key: int) -> tuple[int, bool, str] | None:
    """Convert Traktor MUSICAL_KEY (0–23) → (key_num, is_major, camelot)."""
    parts = key_parts(musical_key)
    if parts is None:
        return None
    return (*parts, camelot(musical_key))


# ── Signal features via librosa ────────────────────────────────────────────────


def compute_signal_features(
    path: str,
    want_signal: bool = True,
    want_melodic: bool = True,
    want_shape: bool = False,
    bpm: float = 0.0,
    decoded: tuple[np.ndarray, int] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> tuple[dict, dict]:
    """Compute audio signal features using librosa.

    ``want_signal`` gates the cheap librosa features (centroid, HFC, dynamic
    complexity, danceability); ``want_melodic`` gates the pricey HPSS melodic
    ratio; ``want_shape`` gates the bar-aligned structure and bassline
    measures, which need *bpm* because every window they use is a number of
    bars. They share one audio decode, so all are computed here when enabled.

    *decoded* is an optional native-rate mono decode of *path* (see
    ``audio_io.decode_mono``); without it the file is read here.
    *should_stop* is checked between the groups and raises
    :class:`AnalysisCancelled`.

    Returns (features, timings).
    """
    import librosa

    features: dict = {}
    timings: dict = {}

    if not (want_signal or want_melodic or want_shape):
        return features, timings

    t0 = time.perf_counter()
    from core.audio_io import resample_mono, safe_sf_read

    if decoded is not None:
        y = resample_mono(decoded[0], decoded[1], 22050)
    else:
        _raw, _sr = safe_sf_read(path, dtype="float32", always_2d=True, samplerate=22050)
        y = _raw.mean(axis=1)
    sr = 22050
    timings["librosa_load"] = time.perf_counter() - t0

    # Onset strength is wanted by danceability and by the shape features, with
    # the same arguments; computed once, when the first of them needs it.
    onset_env = None

    if want_signal:
        raise_if_stopped(should_stop, path)
        # Spectral band features — all seven from a single STFT:
        #   mean energy   (low / mid / high + bassiness ratio)
        #   variation     (low / mid / high)
        t0 = time.perf_counter()
        S = np.abs(librosa.stft(y))
        timings["hfc"] = time.perf_counter() - t0

        # Spectral centroid — mean centroid normalised by Nyquist (0–1).
        # From the same |STFT|: spectral_centroid(y=y) would take its own,
        # with the same defaults (n_fft 2048, hop 512, hann, centred, zero
        # padded, magnitude), and that STFT was most of its cost.
        t0 = time.perf_counter()
        centroid = librosa.feature.spectral_centroid(S=S, sr=sr)[0]
        features["spectral_centroid"] = float(np.mean(centroid) / (sr / 2))
        timings["spectral_centroid"] = time.perf_counter() - t0

        t0 = time.perf_counter()
        freqs = librosa.fft_frequencies(sr=sr)
        low_rms = S[freqs < 200, :].mean(axis=0)
        mid_rms = S[(freqs >= 200) & (freqs < 5000), :].mean(axis=0)
        high_rms = S[freqs >= 5000, :].mean(axis=0)
        low_mean, mid_mean, high_mean = low_rms.mean(), mid_rms.mean(), high_rms.mean()
        features["low_energy"] = float(low_mean)
        features["mid_energy"] = float(mid_mean)
        features["hfc"] = float(high_mean)
        # Bassiness = fraction of total energy in the bass band — normalised for
        # loudness so a quiet and a loud bass-heavy track score equally.
        total_mean = low_mean + mid_mean + high_mean
        features["bassiness"] = float(low_mean / (total_mean + 1e-8))
        features["low_dynamics"] = float(low_rms.std())
        features["mid_dynamics"] = float(mid_rms.std())
        features["high_dynamics"] = float(high_rms.std())
        timings["hfc"] += time.perf_counter() - t0

        # Dynamic complexity + loudness — both come from one RMS pass
        t0 = time.perf_counter()
        rms = librosa.feature.rms(y=y)[0]
        features["dynamic_complexity"] = float(np.std(rms))
        features["loudness"] = float(np.mean(rms))
        timings["dynamic_complexity"] = time.perf_counter() - t0

        # Danceability proxy — peak of normalised onset-strength autocorrelation
        t0 = time.perf_counter()
        onset_env = _onset_strength(y, sr)
        ac = librosa.autocorrelate(onset_env, max_size=sr // 512 * 4)
        ac = ac / (ac[0] + 1e-8)
        features["danceability"] = float(np.max(ac[1:]))
        timings["danceability"] = time.perf_counter() - t0

    # Melodic — harmonic energy ratio via HPSS.
    # harmonic / (harmonic + percussive) → 1.0 = fully melodic, 0.0 = fully rhythmic.
    #
    # HPSS on a full track is O(13 s/track) — completely dominates the scan.
    # We sample a 30-second window centred on the track midpoint instead.
    # Numerically the ratio differs by a few percent vs the full-track value
    # (validated on a 7-minute track: 0.62 vs 0.66), and the relative
    # ordering across the library is preserved for our matching purposes.
    if want_melodic:
        raise_if_stopped(should_stop, path)
        # Its own guard, like the shape features: an error here must not
        # cost the signal features above.
        try:
            t0 = time.perf_counter()
            _MELODIC_WINDOW_SEC = 30
            half = sr * _MELODIC_WINDOW_SEC // 2
            mid = len(y) // 2
            y_seg = y[max(0, mid - half) : mid + half] if len(y) > sr else y
            y_harm, y_perc = librosa.effects.hpss(y_seg)
            harm_rms = float(np.sqrt(np.mean(y_harm**2)))
            perc_rms = float(np.sqrt(np.mean(y_perc**2)))
            features["melodic"] = harm_rms / (harm_rms + perc_rms + 1e-8)
            timings["melodic"] = time.perf_counter() - t0
        except Exception as exc:
            log.warning(
                "melodic failed for %s: %s: %s", os.path.basename(path), type(exc).__name__, exc
            )

    if want_shape:
        raise_if_stopped(should_stop, path)
        # Its own guard: an error here must not cost the signal and melodic
        # features above, which a shared except once threw away with it.
        try:
            features.update(_shape_features(y, sr, bpm, timings, onset_env=onset_env))
        except Exception as exc:
            _log_shape_failure(path, exc)

    return features, timings


def _onset_strength(y: np.ndarray, sr: int) -> np.ndarray:
    """The onset envelope danceability and the shape features both use.

    One definition for both, at the hop the shape features count frames in,
    so the envelope one of them computed is the envelope the other wants.
    """
    import librosa

    return librosa.onset.onset_strength(y=y, sr=sr, hop_length=_SHAPE_HOP)


_SHAPE_TRACEBACK_LOGGED = False


def _log_shape_failure(path: str, exc: Exception) -> None:
    """Warn about a structure/bassline failure; the traceback once a run.

    The first one says where it broke, which is what a shared log needs.
    The rest would say the same thing per track.
    """
    global _SHAPE_TRACEBACK_LOGGED
    log.warning(
        "structure/bassline failed for %s: %s: %s",
        os.path.basename(path),
        type(exc).__name__,
        exc,
        exc_info=not _SHAPE_TRACEBACK_LOGGED,
    )
    _SHAPE_TRACEBACK_LOGGED = True


#: Where the bassline lives: C1–B2, 33–123 Hz. Two octaves, not three —
#: C3 and up is where pads sit, and on a track whose pads enter halfway the
#: loudest low note flips between the bass and the chords, reading as a
#: bassline spanning two and a half octaves.
#:
#: Two minutes from the middle is plenty to characterise a line, and a CQT
#: over a whole track is not worth its cost.
_BASS_LOW_NOTE = "C1"
_BASS_OCTAVES = 2
_BASS_WINDOW_SEC = 120
_BASS_HPSS_MARGIN = 1.0

#: Frame hop of every bar-aligned measure; also librosa's default, which the
#: danceability onset envelope has always used.
_SHAPE_HOP = 512


def _shape_features(
    y, sr: int, bpm: float, timings: dict, onset_env: np.ndarray | None = None
) -> dict:
    """Bar-aligned structure and bassline measures.

    Both need the tempo: a bar at 138 BPM is 1.74 s, so windows in round
    seconds cut across the phrasing and measure nothing a DJ would recognise.
    Without a BPM they are skipped rather than guessed.

    *onset_env* is :func:`_onset_strength` of *y* when the caller already
    has it.
    """
    import librosa

    from core.bassline import analyse_bassline, notes_from_cqt
    from core.structure import analyse_structure, onsets_per_bar

    out: dict = {}
    if bpm <= 0:
        log.debug("shape features skipped: no BPM")
        return out
    hop = _SHAPE_HOP
    fps = sr / hop

    # How busy the track is, counted rather than correlated — see
    # core.structure.onsets_per_bar.
    t0 = time.perf_counter()
    if onset_env is None:
        onset_env = _onset_strength(y, sr)
    onsets = librosa.onset.onset_detect(onset_envelope=onset_env, sr=sr, hop_length=hop)
    density = onsets_per_bar(onsets, n_frames=len(onset_env), bpm=bpm, fps=fps)
    if density is not None:
        out["onset_density"] = density
    timings["onset_density"] = time.perf_counter() - t0

    t0 = time.perf_counter()
    mfcc = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=20, hop_length=hop)
    shape = analyse_structure(mfcc, bpm=bpm, fps=fps)
    if shape is not None:
        out["loop_1bar"] = shape.loop_1
        out["loop_4bar"] = shape.loop_4
        out["sameness"] = shape.sameness
        out["evolution"] = shape.evolution
        out["sections_per_min"] = shape.sections_per_min
    timings["structure"] = time.perf_counter() - t0

    t0 = time.perf_counter()
    half = sr * _BASS_WINDOW_SEC // 2
    mid = len(y) // 2
    seg = y[max(0, mid - half) : mid + half] if len(y) > sr else y
    # The kick has to come out or its transient reads as a stream of notes —
    # a one-note stab bass measured 12 semitones wide without this. But the
    # separation happens on the 24-bin CQT, not the waveform: on the waveform
    # it costs 3.5 s a track and the CQT itself costs 0.03 s.
    #
    # A gentle margin on purpose. Filtering hard enough to remove every
    # transient also removes a percussive bassline — a tribal track came back
    # reporting no bass at all at margin 4.
    cqt = np.abs(
        librosa.cqt(
            seg,
            sr=sr,
            hop_length=hop,
            fmin=librosa.note_to_hz(_BASS_LOW_NOTE),
            n_bins=12 * _BASS_OCTAVES,
            bins_per_octave=12,
        )
    )
    cqt, _percussive = librosa.decompose.hpss(cqt, margin=_BASS_HPSS_MARGIN)
    sixteenth = (60.0 / bpm / 4) * fps
    notes = notes_from_cqt(cqt, frames_per_slot=sixteenth)
    pitch_weight = cqt.sum(axis=1).reshape(_BASS_OCTAVES, 12).sum(axis=0)
    line = analyse_bassline(notes, pitch_weight=pitch_weight)
    out["bass_notes_per_bar"] = line.notes_per_bar
    out["bass_range"] = line.semitone_range
    out["bass_jump"] = line.mean_jump
    out["bass_rest"] = line.rest_fraction
    out["bass_is_major"] = None if line.is_major is None else int(line.is_major)
    # Busy and travelling at once: what a DJ hears as bounce. Either alone is
    # a rolling one-note bass or a sparse melodic one.
    out["bounce"] = line.notes_per_bar * line.semitone_range
    timings["bassline"] = time.perf_counter() - t0
    return out


# ── Full analysis entry point ──────────────────────────────────────────────────


def analyse_file(
    path: str,
    traktor_musical_key: int = -1,
    traktor_bpm: float = 0.0,
    compute: frozenset[str] | None = None,
    embed_models: tuple[str, ...] = (),
    should_stop: Callable[[], bool] | None = None,
) -> tuple[dict, dict]:
    """Full analysis for one file.

    ``compute`` is the set of enabled compute groups (subset of
    ``{"signal", "melodic", "ml"}``); ``None`` means compute everything. Core
    metadata (BPM, key, duration) is always read — it's free. Disabled groups
    are skipped, leaving their columns untouched on upsert.

    ``should_stop`` is checked between stages (decode, signal, melodic,
    shape, CLAP, each embedding model); once it returns True the analysis
    raises :class:`AnalysisCancelled` rather than return a partial result.

    Returns (features, timings).

    features contains:
      bpm, key_num, is_major, camelot, duration
      + librosa signal features (danceability, hfc, spectral_centroid, dynamic_complexity)
      + CLAP ML features (mood_*, is_vocal, is_electronic, arousal, valence, genres)
    """
    import os

    want_signal = compute is None or "signal" in compute
    want_melodic = compute is None or "melodic" in compute
    want_shape = compute is None or "shape" in compute
    want_ml = compute is None or "ml" in compute
    features: dict = {}
    timings: dict = {}

    log.debug("analyse_file START  %s", os.path.basename(path))

    # ── BPM — Traktor only ────────────────────────────────────────────────────
    features["bpm"] = traktor_bpm if traktor_bpm > 0 else None

    # ── Key — Traktor only ────────────────────────────────────────────────────
    key_fields = traktor_key_to_fields(traktor_musical_key)
    if key_fields is not None:
        features["key_num"], features["is_major"], features["camelot"] = key_fields
    else:
        features["key_num"], features["is_major"], features["camelot"] = None, None, None

    # ── Duration from the container header (fast metadata read, no decode) ────
    # 0.0 when unreadable (often: malformed MP3 header) — recomputed from the
    # resampled buffer loaded below, so no extra decode is needed.
    t0 = time.perf_counter()
    features["duration"] = read_duration(path)
    timings["audio_load"] = time.perf_counter() - t0

    want_any_signal = want_signal or want_melodic or want_shape

    # ── One decode for every stage ────────────────────────────────────────────
    # The signal features want 22.05 kHz and CLAP 48 kHz. Each used to decode
    # the file itself; now both resample the one native-rate decode, the same
    # steps each took on its own, so the buffers are unchanged.
    raise_if_stopped(should_stop, path)
    decoded: tuple[np.ndarray, int] | None = None
    decode_error: Exception | None = None
    if want_any_signal or want_ml:
        from core.audio_io import decode_mono

        t0 = time.perf_counter()
        try:
            decoded = decode_mono(path)
        except Exception as exc:
            decode_error = exc
        timings["audio_decode"] = time.perf_counter() - t0
    raise_if_stopped(should_stop, path)

    # ── Signal features via librosa (best-effort — skip if format unsupported) ─
    if want_any_signal:
        log.debug("  computing signal features…")
        try:
            if decode_error is not None:
                raise decode_error
            sig_features, sig_timings = compute_signal_features(
                path,
                want_signal=want_signal,
                want_melodic=want_melodic,
                want_shape=want_shape,
                bpm=features.get("bpm") or 0.0,
                decoded=decoded,
                should_stop=should_stop,
            )
            features.update(sig_features)
            timings.update(sig_timings)
        except AnalysisCancelled:
            raise
        except Exception as _sig_err:
            log.warning("signal features skipped for %s: %s", os.path.basename(path), _sig_err)

    # ── ML features via CLAP ──────────────────────────────────────────────────
    if want_ml:
        raise_if_stopped(should_stop, path)
        # An unreadable file fails the track here, as it did when CLAP read
        # the file itself.
        if decode_error is not None:
            raise decode_error
        log.debug("  computing ML features (CLAP)…")
        from core.clap_analysis import compute_ml_features_clap

        ml_features, ml_timings = compute_ml_features_clap(
            path, decoded=decoded, should_stop=should_stop
        )
        features.update(ml_features)
        timings.update(ml_timings)
    # The embedding models decode for themselves; nothing below needs this.
    del decoded

    # ── Extra embedding models (pluggable ONNX backends) ──────────────────────
    # Each is computed independently of the CLAP pass and stored in the
    # track_embeddings side table by the worker. compute_vector never raises —
    # a missing backend / model file just yields no embedding for that model.
    if embed_models:
        from core.embeddings import EMBEDDING_MODELS, compute_vector

        embs: dict[str, bytes] = {}
        for key in embed_models:
            model = EMBEDDING_MODELS.get(key)
            if model is None:
                continue
            raise_if_stopped(should_stop, path)
            t0 = time.perf_counter()
            vec = compute_vector(model, path)
            timings[f"emb_{key}"] = time.perf_counter() - t0
            if vec is not None:
                embs[key] = vec.astype(np.float32).tobytes()
        if embs:
            features["_embeddings"] = embs

    log.debug(
        "analyse_file DONE   %s  arousal=%.2f valence=%.2f is_vocal=%.2f",
        os.path.basename(path),
        features.get("arousal", 0),
        features.get("valence", 0),
        features.get("is_vocal", 0),
    )
    return features, timings
