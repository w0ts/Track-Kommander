"""The analysis worker: stopping promptly, and a cheap constructor.

Stop used to wait for every track already in flight. The pool was managed by
``with ThreadPoolExecutor(...)``, whose exit joins every worker, so a stop
still paid a full decode + CLAP pass per thread — longer than the three
seconds the main window gives a closing worker before tearing it down.

The constructor runs on the GUI thread (the per-track Re-analyse builds one on
a right-click), so it must not scan the tracks table or fork ``vm_stat``.
"""

from __future__ import annotations

import threading
import time

import pytest

import core.analysis as ca
import workers.analysis as wa
from core.analysis import AnalysisCancelled

_BLOCK_SEC = 10.0


@pytest.fixture
def light_worker(monkeypatch):
    """Keep construction and run() away from the DB, QSettings and CLAP."""
    monkeypatch.setattr(
        wa, "_enabled_compute_groups", lambda: (frozenset({"signal"}), ["danceability"])
    )
    monkeypatch.setattr(wa, "_wanted_mask", lambda: 0)
    monkeypatch.setattr(wa, "_enabled_embedding_models", lambda: ())
    monkeypatch.setattr(wa, "reload_traktor_collection", lambda: None)
    monkeypatch.setattr(wa, "preload_traktor_collection", lambda: None)


def test_stop_returns_without_waiting_for_tracks_in_flight(
    tmp_path, monkeypatch, light_worker
) -> None:
    paths = []
    for i in range(6):
        p = tmp_path / f"t{i}.wav"
        p.write_bytes(b"")
        paths.append(str(p))

    started = threading.Semaphore(0)
    release = threading.Event()

    def _blocking_analyse(path, compute=None, embed_models=(), should_stop=None):
        started.release()
        # Deliberately ignores should_stop: the collector must not wait on a
        # track that does not cooperate either.
        release.wait(_BLOCK_SEC)
        return {"path": path}

    monkeypatch.setattr(wa, "_analyse_one", _blocking_analyse)
    saved: list[str] = []
    monkeypatch.setattr(wa, "upsert_track", lambda path, *a, **k: saved.append(path))

    worker = wa.AnalysisWorker(paths, force=True, n_workers=2)
    runner = threading.Thread(target=worker.run, daemon=True)
    try:
        runner.start()
        assert started.acquire(timeout=5) and started.acquire(timeout=5)
        t0 = time.monotonic()
        worker.stop()
        runner.join(timeout=_BLOCK_SEC)
        elapsed = time.monotonic() - t0
        assert not runner.is_alive(), "run() is still waiting on the pool"
        assert elapsed < 2.0, f"stop took {elapsed:.2f}s"
    finally:
        release.set()
    # The tracks that finish after the stop are not written.
    time.sleep(0.2)
    assert saved == []


def test_a_cancelled_track_is_not_saved(tmp_path, monkeypatch, light_worker) -> None:
    """A track that bails out mid-analysis must not be stored half-done."""

    p = tmp_path / "a.wav"
    p.write_bytes(b"")
    worker = wa.AnalysisWorker([str(p)], force=True, n_workers=1)

    def _cancelled(path, compute=None, embed_models=(), should_stop=None):
        assert should_stop is not None, "the track must be told how to notice a stop"
        worker.stop()
        assert should_stop()
        raise AnalysisCancelled(path)

    monkeypatch.setattr(wa, "_analyse_one", _cancelled)
    saved: list[str] = []
    errors: list[str] = []
    monkeypatch.setattr(wa, "upsert_track", lambda path, *a, **k: saved.append(path))
    worker.error.connect(errors.append)
    worker.run()
    assert saved == []
    assert errors == []


def test_analyse_one_passes_the_stop_check_through(monkeypatch) -> None:
    seen = {}

    def _fake_analyse_file(path, **kw):
        seen.update(kw)
        return {}, {}

    monkeypatch.setattr(ca, "analyse_file", _fake_analyse_file)
    monkeypatch.setattr(ca, "read_metadata", lambda p: ("", "", "", "", -1, 0.0, 0, {}))
    check = lambda: False  # noqa: E731
    wa._analyse_one("/x.wav", None, (), should_stop=check)
    assert seen["should_stop"] is check


def test_constructor_does_no_db_or_ram_work(monkeypatch, light_worker) -> None:
    def _forbidden(*_a, **_k):
        raise AssertionError("must not run on the constructing (GUI) thread")

    monkeypatch.setattr(wa, "current_masks", _forbidden)
    monkeypatch.setattr(wa, "_available_ram_bytes", _forbidden)
    monkeypatch.setattr(wa, "_autodetect_n_workers", _forbidden)
    w = wa.AnalysisWorker(["/m/a.mp3"])
    assert w.force is False
    assert w._compute == frozenset({"signal"})


def test_autodetect_probes_ram_once(monkeypatch) -> None:
    calls = []

    def _ram() -> int:
        calls.append(1)
        return 16 * 1024**3

    monkeypatch.setattr(wa, "_available_ram_bytes", _ram)
    assert wa._autodetect_n_workers() >= 1
    assert len(calls) == 1
