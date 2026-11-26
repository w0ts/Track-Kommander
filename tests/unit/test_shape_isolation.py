"""A structure/bassline failure must not cost the other signal features.

They used to share one ``except`` in ``analyse_file``: an error in the new
bar-aligned code threw away danceability, loudness, melodic and the rest
along with it, and the log only said "signal features skipped".
"""

from __future__ import annotations

import logging

import numpy as np
import pytest

sf = pytest.importorskip("soundfile")

import core.analysis as analysis  # noqa: E402


@pytest.fixture
def wav(tmp_path):
    sr = 22050
    t = np.arange(sr * 8) / sr
    y = (0.3 * np.sin(2 * np.pi * 110 * t)).astype(np.float32)
    path = tmp_path / "tone.wav"
    sf.write(path, y, sr)
    return str(path)


def test_signal_features_survive_a_shape_failure(wav, monkeypatch, caplog) -> None:
    def _boom(*a, **k):
        raise ValueError("cqt blew up")

    monkeypatch.setattr(analysis, "_shape_features", _boom)
    monkeypatch.setattr(analysis, "_SHAPE_TRACEBACK_LOGGED", False)
    with caplog.at_level(logging.WARNING, logger="analysis"):
        features, _t = analysis.compute_signal_features(
            wav, want_signal=True, want_melodic=True, want_shape=True, bpm=128.0
        )

    assert features.get("danceability") is not None
    assert features.get("melodic") is not None
    assert "sameness" not in features
    record = next(r for r in caplog.records if "structure/bassline" in r.message)
    assert "ValueError: cqt blew up" in record.message
    assert record.exc_info, "the first failure carries its traceback"


def test_only_the_first_failure_carries_a_traceback(wav, monkeypatch, caplog) -> None:
    monkeypatch.setattr(
        analysis, "_shape_features", lambda *a, **k: (_ for _ in ()).throw(ValueError("x"))
    )
    monkeypatch.setattr(analysis, "_SHAPE_TRACEBACK_LOGGED", False)
    with caplog.at_level(logging.WARNING, logger="analysis"):
        for _ in range(2):
            analysis.compute_signal_features(
                wav, want_signal=False, want_melodic=False, want_shape=True, bpm=128.0
            )
    shape = [r for r in caplog.records if "structure/bassline" in r.message]
    assert [bool(r.exc_info) for r in shape] == [True, False]
