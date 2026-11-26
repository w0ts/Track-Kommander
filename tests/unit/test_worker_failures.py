"""A worker that fails still says it has finished.

The window disables its controls when a worker starts and enables them again
on ``finished``. A worker whose ``run()`` raised never emitted it: the thread
ended, the Scan button stayed disabled, the results table stayed disabled, and
nothing said why — a locked database was enough.
"""

from __future__ import annotations

import sqlite3

import pytest

import workers.analysis as wa
import workers.hotcue_worker as hw


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


def _heard(worker) -> tuple[list[tuple], list[str]]:
    finished: list[tuple] = []
    errors: list[str] = []
    worker.finished.connect(lambda *args: finished.append(args))
    worker.error.connect(errors.append)
    return finished, errors


# ── The scan ────────────────────────────────────────────────────────────────


def test_a_scan_that_cannot_read_the_library_still_finishes(
    tmp_path, monkeypatch, light_worker
) -> None:
    def locked() -> dict[str, int]:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(wa, "current_masks", locked)
    track = tmp_path / "a.wav"
    track.write_bytes(b"")
    worker = wa.AnalysisWorker([str(track)], n_workers=1)
    finished, errors = _heard(worker)

    worker.run()

    assert finished == [(0, 0, {})]
    assert len(errors) == 1 and "database is locked" in errors[0]


def test_a_scan_that_works_finishes_once(tmp_path, monkeypatch, light_worker) -> None:
    track = tmp_path / "a.wav"
    track.write_bytes(b"")
    monkeypatch.setattr(
        wa,
        "_analyse_one",
        lambda path, compute=None, embed_models=(), should_stop=None: {"path": path},
    )
    monkeypatch.setattr(wa, "upsert_track", lambda path, *a, **k: None)
    worker = wa.AnalysisWorker([str(track)], force=True, n_workers=1)
    finished, errors = _heard(worker)

    worker.run()

    assert len(finished) == 1
    assert errors == []


# ── Hotcue detection ────────────────────────────────────────────────────────


def test_hotcue_detection_that_cannot_start_still_finishes(tmp_path, monkeypatch) -> None:
    def no_processes(*_args, **_kw):
        raise OSError(24, "Too many open files")

    monkeypatch.setattr(hw, "ProcessPoolExecutor", no_processes)
    nml = str(tmp_path / "collection.nml")
    worker = hw.HotcueWorker(nml, [("/m/a.mp3", 128.0, 0.0)])
    finished, errors = _heard(worker)

    worker.run()

    assert finished == [(0, nml)]
    assert len(errors) == 1 and "Too many open files" in errors[0]
