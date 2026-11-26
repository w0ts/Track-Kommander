"""Damaged MP3s: a few bad frames must cost a few frames, not the track.

A real library has them. One such file — eight corrupt frames 56 KB in —
came back as 0.39 s of a 445 s track from every decoder path, so hotcue
detection saw a clip too short to work on and the waveform was lost.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from core import audio_io
from core.audio_io import _pyav_read, safe_sf_read

soundfile = pytest.importorskip("soundfile")
pytest.importorskip("av")


@pytest.fixture(scope="module")
def corrupt_mp3(tmp_path_factory) -> Path:
    """30 s of noise with a few hundred bytes of garbage a tenth of the way in."""
    if "MP3" not in soundfile.available_formats():
        pytest.skip("libsndfile has no MP3 support")
    d = tmp_path_factory.mktemp("audio")
    good = d / "good.mp3"
    rng = np.random.default_rng(0)
    soundfile.write(
        good, (rng.standard_normal(44100 * 30) * 0.1).astype("float32"), 44100, format="MP3"
    )
    raw = bytearray(good.read_bytes())
    off = len(raw) // 10
    raw[off : off + 400] = bytes([0x28, 0x0D, 0x84, 0xD8] * 100)
    bad = d / "bad.mp3"
    bad.write_bytes(raw)
    return bad


def test_pyav_skips_the_bad_frames_and_decodes_the_rest(corrupt_mp3) -> None:
    """The old loop pulled from one decode() generator; a generator that
    raised is finished, so the first bad frame ended the read at 2.9 s."""
    audio, sr = _pyav_read(str(corrupt_mp3), always_2d=True)
    assert len(audio) / sr > 29.5


def test_a_short_libsndfile_read_falls_back_to_pyav(corrupt_mp3, monkeypatch) -> None:
    """libsndfile does not always error on a bad frame — on the real file it
    returned 0.39 s and reported no problem. When it hands back far less
    than the length it itself claims, the file is decoded again via PyAV."""
    real = soundfile.SoundFile

    class _Truncating(real):
        def read(self, *a, **k):
            data = super().read(*a, **k)
            return data[: self.samplerate // 2]  # half a second

    monkeypatch.setattr(soundfile, "SoundFile", _Truncating)
    audio, sr = safe_sf_read(str(corrupt_mp3))
    assert len(audio) / sr > 29.5


def test_a_genuinely_short_file_is_not_second_guessed(tmp_path) -> None:
    if "MP3" not in soundfile.available_formats():
        pytest.skip("libsndfile has no MP3 support")
    p = tmp_path / "short.mp3"
    soundfile.write(p, np.zeros(4410, dtype="float32"), 44100, format="MP3")
    calls = []
    orig = audio_io._pyav_read

    def _spy(*a, **k):
        calls.append(1)
        return orig(*a, **k)

    audio_io._pyav_read = _spy
    try:
        safe_sf_read(str(p))
    finally:
        audio_io._pyav_read = orig
    assert calls == []
