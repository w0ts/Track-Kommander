"""Opening an audio file from several threads at once must be safe.

This is a regression test for a real crash. The device export gained a thread
pool that computed waveforms across cores; within a minute of real use the app
died with ``EXC_BAD_ACCESS (SIGBUS)``:

    Thread 12 CRASHED:  sf_open -> psf_open_file -> mpeg_open -> mpeg_init -> ??? 0x1
    Threads 10,11,13..17:  sf_readf_float -> mpeg_dec_read_f

Eight threads inside libsndfile's MP3 decoder, and one jumped to address
``0x1`` inside ``mpeg_init`` — a half-written global function-pointer table.
mpg123 selects its NEON decoder routines during one-time global init, and a
second thread opening a file while the first is still filling that table reads
a pointer that is not there yet.

``core.audio_io`` already documented the hazard and shipped ``warmup()`` for
it, but that only helps callers who remember to call it — and the new pool did
not. The guarantee therefore belongs in :func:`safe_sf_read` itself: the
**open** is serialised, the decode is not, so the expensive part still runs in
parallel.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
import soundfile as sf

from core import audio_io
from workers.waveforms import analyse_many


@pytest.fixture
def mp3s(tmp_path):
    """Several real MP3s — the format whose decoder has the racy init."""
    sf = pytest.importorskip("soundfile")
    np = pytest.importorskip("numpy")
    if "MP3" not in sf.available_formats():
        pytest.skip("libsndfile has no MP3 support")
    rate = 44100
    out = []
    for i in range(8):
        p = tmp_path / f"t{i}.mp3"
        t = np.arange(rate) / rate
        sf.write(p, np.sin(2 * np.pi * (110 + 40 * i) * t).astype("float32"), rate, format="MP3")
        out.append(p)
    return out


def test_the_open_is_serialised(mp3s, monkeypatch) -> None:
    """The lock has to be held while the file is opened, not merely exist."""
    real = sf.SoundFile
    seen: list[bool] = []

    class Watched(real):  # type: ignore[misc, valid-type]
        def __init__(self, *args, **kwargs):
            seen.append(audio_io._OPEN_LOCK.locked())
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(sf, "SoundFile", Watched)
    audio_io.safe_sf_read(str(mp3s[0]))
    assert seen and all(seen), "SoundFile was constructed without the lock"


def test_the_decode_is_not_serialised(mp3s, monkeypatch) -> None:
    """Holding the lock through the decode would undo the parallelism the
    pool exists for."""
    real_read = sf.SoundFile.read
    held: list[bool] = []

    def watched_read(self, *args, **kwargs):
        held.append(audio_io._OPEN_LOCK.locked())
        return real_read(self, *args, **kwargs)

    monkeypatch.setattr(sf.SoundFile, "read", watched_read)
    audio_io.safe_sf_read(str(mp3s[0]))
    assert held and not any(held), "the lock was still held while decoding"


def test_concurrent_reads_all_succeed(mp3s) -> None:
    """The shape that crashed: many threads opening MP3s at once."""
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda p: audio_io.safe_sf_read(str(p)), mp3s))
    assert len(results) == len(mp3s)
    assert all(data.size > 0 for data, _rate in results)


def test_a_hammering_of_opens_stays_upright(mp3s) -> None:
    """Repeated rounds, because a race does not fail every time."""

    def once(path):
        data, rate = audio_io.safe_sf_read(str(path))
        return data.shape[0], rate

    for _ in range(6):
        with ThreadPoolExecutor(max_workers=8) as pool:
            got = list(pool.map(once, mp3s * 2))
        assert all(frames > 0 for frames, _ in got)


def test_the_fallback_still_works(tmp_path) -> None:
    """The PyAV path must survive the change; a broken file takes it."""
    bad = tmp_path / "broken.mp3"
    bad.write_bytes(b"not audio at all")
    # soundfile refuses it, then PyAV refuses it too — the point is that the
    # fallback is still reached and still raises rather than crashing.
    with pytest.raises((sf.LibsndfileError, OSError, ValueError)):
        audio_io.safe_sf_read(str(bad))


def test_parallel_waveforms_do_not_crash(mp3s) -> None:
    """End to end through the pool that caused it."""

    class Item:
        def __init__(self, source):
            self.source = source

    items = [Item(p) for p in mp3s]
    got = dict(analyse_many(items))
    assert len(got) == len(items)
    assert all(wf is not None for wf in got.values())


def test_lock_is_a_real_lock() -> None:
    assert isinstance(audio_io._OPEN_LOCK, type(threading.Lock()))
