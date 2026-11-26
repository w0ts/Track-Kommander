"""analyse_file: one decode per track, shared spectra, cooperative stop.

The signal features and CLAP each used to decode the file themselves (at
22.05 kHz and 48 kHz), and the signal features took three STFTs of the same
signal. The shared versions must produce exactly what the separate ones did:
stored feature values may not move.
"""

from __future__ import annotations

import threading
import time

import numpy as np
import pytest

sf = pytest.importorskip("soundfile")
librosa = pytest.importorskip("librosa")
spectrum = pytest.importorskip("librosa.core.spectrum")

import core.analysis as analysis  # noqa: E402
import core.clap_analysis as clap  # noqa: E402
from core import audio_io  # noqa: E402


@pytest.fixture
def stereo_wav(tmp_path):
    """12 s of stereo at 44.1 kHz — neither analysis rate, so both resample."""
    sr = 44100
    t = np.arange(sr * 12) / sr
    left = 0.3 * np.sin(2 * np.pi * 110 * t) * (1 + np.sin(2 * np.pi * 2 * t))
    right = 0.2 * np.sin(2 * np.pi * 440 * t)
    y = np.stack([left, right], axis=1).astype(np.float32)
    path = tmp_path / "stereo.wav"
    sf.write(path, y, sr)
    return str(path)


class _FakeClap:
    """Records the audio CLAP would have embedded; scores are constants."""

    def __init__(self) -> None:
        self.audio: list[np.ndarray] = []
        self._arousal_embs = np.eye(9, dtype=np.float32)
        self._valence_embs = np.eye(9, dtype=np.float32)

    def audio_emb(self, audio_np):
        self.audio.append(audio_np)
        return np.ones(9, dtype=np.float32) / 3.0

    def binary_score(self, _emb, _key):
        return 0.5

    def scale_score(self, _emb, _anchors, lo, hi):
        return (lo + hi) / 2

    def top_genres(self, _emb, top_k=3):
        return [{"name": "Techno", "prob": 1.0}]


@pytest.fixture
def fake_clap(monkeypatch):
    fake = _FakeClap()
    monkeypatch.setattr(clap, "_get_clap_backend", lambda: fake)
    return fake


@pytest.fixture
def count_opens(monkeypatch):
    real = sf.SoundFile
    opened: list[str] = []

    class Counting(real):  # type: ignore[misc, valid-type]
        def __init__(self, file, *a, **k):
            opened.append(str(file))
            super().__init__(file, *a, **k)

    monkeypatch.setattr(sf, "SoundFile", Counting)
    return opened


# ── One decode ──────────────────────────────────────────────────────────────


def test_a_track_is_decoded_once(stereo_wav, fake_clap, count_opens) -> None:
    analysis.analyse_file(stereo_wav, traktor_bpm=124.0, compute=frozenset({"signal", "ml"}))
    assert count_opens == [stereo_wav]


def test_shared_decode_gives_the_same_buffers_and_features(stereo_wav, fake_clap) -> None:
    # What each stage decoded for itself before.
    raw48, _ = audio_io.safe_sf_read(stereo_wav, samplerate=48000)
    want_48k = raw48.mean(axis=1).astype(np.float32)
    want_sig, _t = analysis.compute_signal_features(
        stereo_wav, want_signal=True, want_melodic=True, want_shape=True, bpm=124.0
    )

    got, _t = analysis.analyse_file(stereo_wav, traktor_bpm=124.0)

    assert len(fake_clap.audio) == 1
    assert fake_clap.audio[0].dtype == np.float32
    assert np.array_equal(fake_clap.audio[0], want_48k)
    for key, value in want_sig.items():
        assert got[key] == value, key


def test_signal_only_does_not_resample_for_clap(stereo_wav, fake_clap, monkeypatch) -> None:
    rates: list[int] = []
    real = librosa.resample

    def _spy(y, *, orig_sr, target_sr, **kw):
        rates.append(target_sr)
        return real(y, orig_sr=orig_sr, target_sr=target_sr, **kw)

    monkeypatch.setattr(librosa, "resample", _spy)
    analysis.analyse_file(stereo_wav, compute=frozenset({"signal"}))
    assert rates == [22050]
    assert fake_clap.audio == []


def test_ml_only_does_not_resample_for_the_signal_features(
    stereo_wav, fake_clap, monkeypatch
) -> None:
    rates: list[int] = []
    real = librosa.resample

    def _spy(y, *, orig_sr, target_sr, **kw):
        rates.append(target_sr)
        return real(y, orig_sr=orig_sr, target_sr=target_sr, **kw)

    monkeypatch.setattr(librosa, "resample", _spy)
    analysis.analyse_file(stereo_wav, compute=frozenset({"ml"}))
    assert rates == [48000]
    assert len(fake_clap.audio) == 1


def test_an_unreadable_file_still_fails_the_ml_pass(tmp_path, fake_clap) -> None:
    bad = tmp_path / "bad.mp3"
    bad.write_bytes(b"not audio at all" * 100)
    with pytest.raises(Exception):  # noqa: B017
        analysis.analyse_file(str(bad), compute=frozenset({"signal", "ml"}))


def test_an_unreadable_file_skips_signal_features_quietly(tmp_path) -> None:
    bad = tmp_path / "bad.mp3"
    bad.write_bytes(b"not audio at all" * 100)
    features, _t = analysis.analyse_file(str(bad), compute=frozenset({"signal"}))
    assert "danceability" not in features


# ── Shared spectra ──────────────────────────────────────────────────────────


def test_signal_features_take_one_stft_of_their_own(stereo_wav, monkeypatch) -> None:
    """Centroid reuses the band features' |STFT|; onset strength keeps its mel."""

    calls: list[int] = []
    real = spectrum.stft

    def _counting(*a, **k):
        calls.append(1)
        return real(*a, **k)

    monkeypatch.setattr(spectrum, "stft", _counting)
    monkeypatch.setattr(librosa, "stft", _counting)
    features, _t = analysis.compute_signal_features(
        stereo_wav, want_signal=True, want_melodic=False, want_shape=False
    )
    # One for |S| (bands + centroid), one inside onset_strength's mel.
    assert len(calls) == 2

    raw, sr = audio_io.safe_sf_read(stereo_wav, samplerate=22050)
    y = raw.mean(axis=1)
    centroid = librosa.feature.spectral_centroid(y=y, sr=sr)[0]
    assert features["spectral_centroid"] == float(np.mean(centroid) / (sr / 2))


def test_onset_strength_is_computed_once_for_signal_and_shape(stereo_wav, monkeypatch) -> None:
    calls: list[int] = []
    real = librosa.onset.onset_strength

    def _counting(*a, **k):
        calls.append(1)
        return real(*a, **k)

    monkeypatch.setattr(librosa.onset, "onset_strength", _counting)
    analysis.compute_signal_features(
        stereo_wav, want_signal=True, want_melodic=False, want_shape=True, bpm=124.0
    )
    assert len(calls) == 1


# ── Cooperative stop ────────────────────────────────────────────────────────


def test_a_stop_between_stages_raises_cancelled(stereo_wav, fake_clap) -> None:
    checks = {"n": 0}

    def _stop_after_decode() -> bool:
        checks["n"] += 1
        return checks["n"] > 2

    with pytest.raises(analysis.AnalysisCancelled):
        analysis.analyse_file(stereo_wav, traktor_bpm=124.0, should_stop=_stop_after_decode)
    assert fake_clap.audio == [], "CLAP ran after the stop"


def test_a_stop_before_anything_skips_the_decode(stereo_wav, count_opens) -> None:
    with pytest.raises(analysis.AnalysisCancelled):
        analysis.analyse_file(stereo_wav, should_stop=lambda: True)
    assert count_opens == []


# ── Traktor collection cache ────────────────────────────────────────────────


def test_concurrent_first_reads_parse_the_collection_once(monkeypatch) -> None:
    loads: list[int] = []

    def _slow_load() -> dict:
        loads.append(1)
        time.sleep(0.05)
        return {"/m/a.mp3": {"artist": "A"}}

    monkeypatch.setattr(analysis, "_load_traktor_collection", _slow_load)
    analysis.reload_traktor_collection()
    gate = threading.Barrier(8)
    results: list[dict] = []

    def _read() -> None:
        gate.wait()
        results.append(analysis._get_traktor_collection())

    threads = [threading.Thread(target=_read) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    analysis.reload_traktor_collection()
    assert len(loads) == 1
    assert all(r is results[0] for r in results)
