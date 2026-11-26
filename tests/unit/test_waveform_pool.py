"""Computing waveforms across the machine's cores.

Measured on real tracks: decode + analyse costs ~0.75 s each and is the
largest single cost of an export — larger than copying to the key (9.1 s
against 5.4 s for the same 220 MB). It is CPU-bound, per-track independent
work, so it parallelises cleanly.

Results come back as they finish, not in order, because the caller drives a
progress bar from them.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core import anlz
from workers.waveforms import analyse_many


class _Item:
    """The shape analyse_many needs: anything with a ``source``."""

    def __init__(self, source: Path) -> None:
        self.source = source


@pytest.fixture
def audio(tmp_path):
    sf = pytest.importorskip("soundfile")
    np = pytest.importorskip("numpy")
    rate = 44100
    out = []
    for i in range(5):
        p = tmp_path / f"t{i}.wav"
        t = np.arange(rate) / rate
        sf.write(p, (np.sin(2 * np.pi * (200 + 100 * i) * t)).astype("float32"), rate)
        out.append(_Item(p))
    return out


def test_every_track_comes_back(audio) -> None:
    got = list(analyse_many(audio))
    assert len(got) == len(audio)
    assert {item for item, _wf in got} == set(audio)


def test_results_carry_a_waveform(audio) -> None:
    for _item, wf in analyse_many(audio):
        assert isinstance(wf, anlz.Waveform)
        assert len(wf.preview) == 400
        assert wf.detail and wf.colour


def test_it_matches_the_single_threaded_answer(audio) -> None:
    """Parallelism must not change the output, only when it arrives."""
    expected = {i.source: anlz.analyse_waveform(i.source) for i in audio}
    for item, wf in analyse_many(audio):
        assert wf == expected[item.source]


def test_an_unreadable_file_yields_none_and_does_not_sink_the_rest(audio, tmp_path) -> None:
    bad = _Item(tmp_path / "broken.mp3")
    bad.source.write_bytes(b"not audio")
    got = dict(analyse_many([*audio, bad]))
    assert got[bad] is None
    assert all(got[i] is not None for i in audio)


def test_nothing_to_do_is_fine() -> None:
    assert list(analyse_many([])) == []


def test_a_single_track_does_not_need_a_pool(audio) -> None:
    got = list(analyse_many(audio[:1]))
    assert len(got) == 1 and got[0][0] is audio[0]


def test_stopping_early_is_honoured(audio) -> None:
    calls = {"n": 0}

    def should_stop() -> bool:
        calls["n"] += 1
        return calls["n"] > 2

    got = list(analyse_many(audio, should_stop=should_stop))
    assert len(got) < len(audio)
