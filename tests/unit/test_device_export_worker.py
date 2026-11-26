"""The export runs on a worker thread and can be stopped.

It used to run inline in the click handler, pumping ``processEvents()``
between tracks. That kept the window painting but re-entered the event loop
from inside a long operation, and it left the ``should_stop`` hooks that
:func:`core.device_export.export` and :func:`workers.waveforms.analyse_many`
already provide wired to nothing.

These drive :meth:`DeviceExportWorker._run` directly rather than starting the
thread: the work is the same, the signals are emitted the same way, and the
test does not depend on an event loop turning over in time.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

import workers.device_export as dew
from core.device_export import Orphans
from workers.device_export import DeviceExportWorker


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@dataclass
class FakeWaveform:
    preview: tuple = (1, 2, 3)


@dataclass
class FakeTrack:
    source: Path
    device_path: str = ""
    analysis: object = None
    waveform: list = field(default_factory=list)


@dataclass
class FakeReport:
    db_path: Path | None = Path("/usb/PIONEER/rekordbox/export.pdb")
    copied: int = 0
    errors: list = field(default_factory=list)
    missing: list = field(default_factory=list)
    sidecars_removed: int = 0
    replaced: int = 0
    kept: int = 0
    unchanged: int = 0
    reused: int = 0


@pytest.fixture
def stub(monkeypatch):
    """Replace everything the worker calls out to, recording the calls."""
    calls: dict[str, object] = {"stored": [], "exported": None}

    monkeypatch.setattr(dew, "analysis_plan", lambda *a, **k: object())
    monkeypatch.setattr(dew, "needs_analysis", lambda plan, tracks: list(tracks))
    monkeypatch.setattr(dew.waveform_cache, "load_many", lambda paths: {})
    monkeypatch.setattr(
        dew.waveform_cache, "store", lambda src, wf: calls["stored"].append(str(src))
    )
    monkeypatch.setattr(
        dew,
        "analyse_many",
        lambda tracks, should_stop=None: iter([(t, FakeWaveform()) for t in tracks]),
    )
    monkeypatch.setattr(dew, "strip_macos_metadata", lambda dest: 0)

    def _export(tracks, dest, seed, **kw):
        calls["exported"] = {"tracks": len(tracks), "kw": kw}
        return FakeReport(copied=len(tracks))

    monkeypatch.setattr(dew, "export", _export)
    return calls


def _worker(tmp_path, n=2, **kw) -> DeviceExportWorker:
    tracks = [FakeTrack(source=tmp_path / f"t{i}.mp3") for i in range(n)]
    defaults = dict(playlists={}, merge=False, refresh_existing=True, reanalyse=False)
    defaults.update(kw)
    return DeviceExportWorker(tracks, tmp_path / "usb", tmp_path / "seed.pdb", **defaults)


def _collect(worker):
    events: dict[str, list] = {"progress": [], "done": [], "error": []}
    worker.progress.connect(lambda d, t, w: events["progress"].append((d, t, w)))
    worker.done.connect(lambda o: events["done"].append(o))
    worker.error.connect(lambda m: events["error"].append(m))
    return events


# ── The happy path ──────────────────────────────────────────────────────────


def test_a_clean_run_reports_done_not_stopped(qapp, tmp_path, stub) -> None:
    w = _worker(tmp_path)
    events = _collect(w)
    w._run()

    assert events["error"] == []
    assert len(events["done"]) == 1
    outcome = events["done"][0]
    assert not outcome.stopped
    assert outcome.report.copied == 2


def test_a_track_whose_audio_cannot_be_read_is_reported(
    qapp, tmp_path, stub, monkeypatch, caplog
) -> None:
    """It still goes on the key — a missing waveform is a cosmetic loss —
    but it used to go silently."""
    monkeypatch.setattr(
        dew,
        "analyse_many",
        lambda tracks, should_stop=None: iter(
            [(t, None if t.source.name == "t1.mp3" else FakeWaveform()) for t in tracks]
        ),
    )
    w = _worker(tmp_path, n=3)
    events = _collect(w)

    with caplog.at_level("WARNING"):
        w._run()

    [outcome] = events["done"]
    assert outcome.no_waveform == ["t1.mp3"]
    assert stub["exported"]["tracks"] == 3
    assert "t1.mp3" in caplog.text


def test_every_analysed_track_is_cached(qapp, tmp_path, stub) -> None:
    """Stored as each one lands, so a run that stops keeps what it paid for."""
    w = _worker(tmp_path, n=3)
    _collect(w)
    w._run()
    assert len(stub["stored"]) == 3


def test_a_cache_hit_is_not_analysed_again(qapp, tmp_path, stub, monkeypatch) -> None:
    hit = FakeWaveform()
    monkeypatch.setattr(dew.waveform_cache, "load_many", lambda paths: {str(p): hit for p in paths})
    analysed = []
    monkeypatch.setattr(
        dew,
        "analyse_many",
        lambda tracks, should_stop=None: iter(
            [(t, FakeWaveform()) for t in analysed.extend(tracks) or tracks]
        ),
    )

    w = _worker(tmp_path, n=2)
    events = _collect(w)
    w._run()

    assert analysed == [], "a cached track must not be decoded again"
    assert events["done"][0].from_cache == 2


def test_reanalyse_skips_the_cache_lookup(qapp, tmp_path, stub, monkeypatch) -> None:
    looked = []
    monkeypatch.setattr(dew.waveform_cache, "load_many", lambda paths: looked.append(paths) or {})
    w = _worker(tmp_path, reanalyse=True)
    _collect(w)
    w._run()
    assert looked == [], "force-re-analyse must not consult the cache"


# ── Progress ────────────────────────────────────────────────────────────────


def test_progress_counts_analysis_and_copying_as_one_run(qapp, tmp_path, stub) -> None:
    """Two passes over the same tracks, one bar — not 0-100% twice."""
    w = _worker(tmp_path, n=4)
    events = _collect(w)
    w._run()

    totals = {t for _d, t, _w in events["progress"]}
    assert totals == {12}, "total must be 3x the track count throughout"
    assert events["progress"][-1][0] == 12


def test_copy_progress_lands_in_the_second_half(qapp, tmp_path, stub, monkeypatch) -> None:
    def _export(tracks, dest, seed, *, progress=None, **kw):
        progress(1, 2, "one.mp3")
        return FakeReport(copied=2)

    monkeypatch.setattr(dew, "export", _export)
    w = _worker(tmp_path, n=2)
    events = _collect(w)
    w._run()

    copying = [e for e in events["progress"] if "Copying" in e[2]]
    assert copying and copying[0][0] == 3, "n + copied, with n = 2"


def test_analysis_writes_land_in_the_last_third(qapp, tmp_path, stub, monkeypatch) -> None:
    def _export(tracks, dest, seed, *, analysis_progress=None, phase=None, **kw):
        analysis_progress(1, 2, "one.mp3")
        phase("Writing the database…")
        return FakeReport(copied=2)

    monkeypatch.setattr(dew, "export", _export)
    w = _worker(tmp_path, n=2)
    events = _collect(w)
    w._run()

    writing = [e for e in events["progress"] if "Writing analysis" in e[2]]
    assert writing and writing[0][0] == 5, "2n + written, with n = 2"
    assert "1/2" in writing[0][2] and "one.mp3" in writing[0][2]
    assert any("database" in e[2] for e in events["progress"])


def test_every_phase_after_the_copy_is_announced(qapp, tmp_path, stub) -> None:
    w = _worker(tmp_path, n=2)
    events = _collect(w)
    w._run()
    labels = [w for _d, _t, w in events["progress"]]
    assert any("Traktor playlists" in lb for lb in labels)
    assert any("playlist" in lb.lower() for lb in labels)
    assert any("no longer listed" in lb for lb in labels)


# ── Stopping ────────────────────────────────────────────────────────────────


def test_stopping_before_analysis_writes_nothing(qapp, tmp_path, stub) -> None:
    w = _worker(tmp_path)
    events = _collect(w)
    w.stop()
    w._run()

    outcome = events["done"][0]
    assert outcome.stopped
    assert outcome.report is None
    assert stub["exported"] is None, "the key must not be touched"


def test_the_stop_hook_reaches_the_export(qapp, tmp_path, stub) -> None:
    """export() has always taken should_stop; nothing used to pass it."""
    w = _worker(tmp_path)
    _collect(w)
    w._run()

    assert callable(stub["exported"]["kw"]["should_stop"])


def test_the_stop_hook_reaches_the_analysis(qapp, tmp_path, stub, monkeypatch) -> None:
    seen = {}

    def _analyse(tracks, should_stop=None):
        seen["hook"] = should_stop
        return iter([])

    monkeypatch.setattr(dew, "analyse_many", _analyse)
    w = _worker(tmp_path)
    _collect(w)
    w._run()
    assert callable(seen["hook"])


def test_a_stop_during_analysis_does_not_start_the_copy(qapp, tmp_path, stub, monkeypatch) -> None:
    w = _worker(tmp_path, n=3)

    def _analyse(tracks, should_stop=None):
        yield tracks[0], FakeWaveform()
        w.stop()  # the user presses Stop mid-pass

    monkeypatch.setattr(dew, "analyse_many", _analyse)
    events = _collect(w)
    w._run()

    assert events["done"][0].stopped
    assert stub["exported"] is None
    assert len(stub["stored"]) == 1, "what was analysed is still kept"


# ── Failure ─────────────────────────────────────────────────────────────────


def test_an_unexpected_failure_is_reported_not_raised(qapp, tmp_path, stub, monkeypatch) -> None:
    def _boom(*a, **k):
        raise OSError("drive gone")

    monkeypatch.setattr(dew, "analysis_plan", _boom)
    w = _worker(tmp_path)
    events = _collect(w)
    w.run()  # run(), not _run() — the handler under test lives there

    assert events["error"] == ["OSError: drive gone"]
    assert events["done"] == []


# ── Plain playlists ─────────────────────────────────────────────────────────


def test_m3u_playlists_are_written_too(qapp, tmp_path, stub, monkeypatch) -> None:
    wrote = []
    monkeypatch.setattr(
        dew.m3u, "write_playlists", lambda dest, tracks, pls: wrote.append(dest) or [dest]
    )
    w = _worker(tmp_path, playlists={"Set A": [0, 1]})
    events = _collect(w)
    w._run()

    assert wrote == [tmp_path / "usb"]
    assert events["done"][0].m3u_written == 1


def test_a_failed_m3u_write_does_not_lose_the_export(qapp, tmp_path, stub, monkeypatch) -> None:
    """The database is the stick; the text playlists are a convenience."""

    def _boom(*a, **k):
        raise OSError("read-only")

    monkeypatch.setattr(dew.m3u, "write_playlists", _boom)
    w = _worker(tmp_path, playlists={"Set A": [0]})
    events = _collect(w)
    w._run()

    outcome = events["done"][0]
    assert outcome.report.db_path is not None
    assert outcome.m3u_written == 0
    assert any("m3u8" in e for e in outcome.report.errors)


def test_traktor_playlists_are_written_too(qapp, tmp_path, stub, monkeypatch) -> None:
    wrote = []
    monkeypatch.setattr(
        dew.traktor_playlists,
        "write_playlists",
        lambda dest, tracks, pls, nml: wrote.append((dest, nml)) or [dest],
    )
    w = _worker(tmp_path, playlists={"Set A": [0, 1]})
    w._nml_path = tmp_path / "collection.nml"
    events = _collect(w)
    w._run()

    assert wrote == [(tmp_path / "usb", tmp_path / "collection.nml")]
    assert events["done"][0].nml_written == 1


def test_a_failed_traktor_write_does_not_lose_the_export(qapp, tmp_path, stub, monkeypatch) -> None:
    def _boom(*a, **k):
        raise OSError("read-only")

    monkeypatch.setattr(dew.traktor_playlists, "write_playlists", _boom)
    w = _worker(tmp_path, playlists={"Set A": [0]})
    events = _collect(w)
    w._run()

    outcome = events["done"][0]
    assert outcome.report.db_path is not None
    assert outcome.nml_written == 0
    assert any("Traktor playlists" in e for e in outcome.report.errors)


# ── Orphan sweep ────────────────────────────────────────────────────────────


def test_orphans_are_reported_without_being_deleted(qapp, tmp_path, stub, monkeypatch) -> None:
    """Default is to tell, not to delete — this removes audio."""
    removed = []
    monkeypatch.setattr(
        dew, "find_orphans", lambda dest: Orphans(audio=[Path("a.mp3")], bytes=2048)
    )
    monkeypatch.setattr(dew, "remove_orphans", lambda dest, o: removed.append(o) or (1, []))

    w = _worker(tmp_path)  # sweep_orphans defaults to False
    events = _collect(w)
    w._run()

    outcome = events["done"][0]
    assert outcome.orphans_found == 1
    assert outcome.orphans_bytes == 2048
    assert outcome.orphans_removed == 0
    assert removed == [], "nothing may be deleted unless asked"


def test_orphans_are_deleted_when_asked(qapp, tmp_path, stub, monkeypatch) -> None:
    monkeypatch.setattr(
        dew, "find_orphans", lambda dest: Orphans(audio=[Path("a.mp3")], bytes=2048)
    )
    monkeypatch.setattr(dew, "remove_orphans", lambda dest, o: (1, []))

    w = _worker(tmp_path, sweep_orphans=True)
    events = _collect(w)
    w._run()
    assert events["done"][0].orphans_removed == 1


def test_a_failing_sweep_does_not_lose_the_export(qapp, tmp_path, stub, monkeypatch) -> None:
    def _boom(dest):
        raise OSError("gone")

    monkeypatch.setattr(dew, "find_orphans", _boom)
    w = _worker(tmp_path, sweep_orphans=True)
    events = _collect(w)
    w._run()

    outcome = events["done"][0]
    assert outcome.report.db_path is not None
    assert any("orphan sweep" in e for e in outcome.report.errors)


# ── Time left ───────────────────────────────────────────────────────────────


def test_the_copy_label_carries_an_estimate_once_there_is_a_rate(
    qapp, tmp_path, stub, monkeypatch
) -> None:
    clock = {"t": 0.0}
    monkeypatch.setattr(dew.time, "monotonic", lambda: clock["t"])
    import core.eta as eta_mod

    monkeypatch.setattr(eta_mod.time, "monotonic", lambda: clock["t"])

    def _export(tracks, dest, seed, *, progress=None, **kw):
        progress(1, 4, "a.mp3")
        clock["t"] += 30
        progress(2, 4, "b.mp3")
        clock["t"] += 30
        progress(3, 4, "c.mp3")  # 2 done in 60 s: 2 to go, ~1 min
        return FakeReport(copied=4)

    monkeypatch.setattr(dew, "export", _export)
    w = _worker(tmp_path, n=4)
    events = _collect(w)
    w._run()

    copying = [e[2] for e in events["progress"] if e[2].startswith("Copying")]
    assert "left" not in copying[0]
    assert copying[2].endswith("about 1 min left"), copying[2]
