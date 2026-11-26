"""What the hotcue worker writes to collection.nml.

The detection itself (audio decoding in a process pool) is faked: the pool is
replaced by one that runs each job in this process, and ``detect_one`` by a
function that returns the cues the test wants. What is under test is the
worker's own job — which tracks it hands out, what it collects, what ends up in
the collection, and what it says when it finishes.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from concurrent.futures import Future
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication

import workers.hotcue_worker as hw
from core.traktor import get_hotcue_candidates

NML = """<?xml version="1.0" encoding="UTF-8"?>
<NML VERSION="19"><COLLECTION ENTRIES="4">
 <ENTRY TITLE="Untouched">
  <LOCATION DIR="/:m/:" FILE="a.mp3" VOLUME="HD"/>
  <TEMPO BPM="120.0" BPM_QUALITY="100.0"/>
  <CUE_V2 NAME="AutoGrid" TYPE="4" START="10.0" HOTCUE="0"/>
 </ENTRY>
 <ENTRY TITLE="Also untouched">
  <LOCATION DIR="/:m/:" FILE="b.mp3" VOLUME="HD"/>
  <TEMPO BPM="120.0" BPM_QUALITY="100.0"/>
  <CUE_V2 NAME="AutoGrid" TYPE="4" START="10.0" HOTCUE="0"/>
 </ENTRY>
 <ENTRY TITLE="User cues">
  <LOCATION DIR="/:m/:" FILE="c.mp3" VOLUME="HD"/>
  <TEMPO BPM="120.0" BPM_QUALITY="100.0"/>
  <CUE_V2 NAME="AutoGrid" TYPE="4" START="10.0" HOTCUE="0"/>
  <CUE_V2 NAME="My cue" TYPE="0" START="5000.0" HOTCUE="2"/>
 </ENTRY>
 <ENTRY TITLE="Ours already">
  <LOCATION DIR="/:m/:" FILE="d.mp3" VOLUME="HD"/>
  <TEMPO BPM="120.0" BPM_QUALITY="100.0"/>
  <CUE_V2 NAME="AutoGrid" TYPE="4" START="10.0" HOTCUE="0"/>
  <CUE_V2 NAME="tk: Old" TYPE="0" START="9000.0" HOTCUE="1"/>
 </ENTRY>
</COLLECTION></NML>
"""

#: Cues the fake detector finds, as ``(start_ms, label, type)``.
CUES = [(30_000.0, "tk: Drop", 0), (60_000.0, "tk: Break", 0)]


class _InlinePool:
    """A ``ProcessPoolExecutor`` that runs every job at once, in this process."""

    def __init__(self, *_args, **_kwargs) -> None:
        self.shut_down = False

    def submit(self, fn, *args) -> Future:
        future: Future = Future()
        try:
            future.set_result(fn(*args))
        except Exception as exc:
            future.set_exception(exc)
        return future

    def shutdown(self, *_args, **_kwargs) -> None:
        self.shut_down = True


@pytest.fixture(scope="module")
def _app():
    # A QApplication, not a bare QCoreApplication: the widget tests in the same
    # session need one, and Qt allows only one application object.
    return QCoreApplication.instance() or QApplication([])


@pytest.fixture
def nml(tmp_path) -> Path:
    path = tmp_path / "collection.nml"
    path.write_text(NML)
    return path


@pytest.fixture
def detect(monkeypatch, _app):
    """Replace the pool and ``detect_one``; ``detect.results`` maps a file
    name to its cues, or to an exception to raise. ``detect.calls`` records
    what the worker asked for."""

    class Detect:
        results: dict[str, object] = {}
        calls: list[tuple[str, float, float, int]] = []

    def fake_detect_one(path, bpm, phase_ms, max_cues):
        Detect.calls.append((path, bpm, phase_ms, max_cues))
        got = Detect.results.get(Path(path).name, [])
        if isinstance(got, Exception):
            raise got
        return path, got, "" if got else "silence"

    Detect.results = {}
    Detect.calls = []
    monkeypatch.setattr(hw, "ProcessPoolExecutor", _InlinePool)
    monkeypatch.setattr(hw, "detect_one", fake_detect_one)
    return Detect


def _run(nml: Path, candidates=None, force: bool = False):
    """Run the worker's flow on this thread; return ``(worker, finished,
    errors)`` where *finished* is every ``finished(count, path)`` emitted."""
    if candidates is None:
        candidates = get_hotcue_candidates(nml)
    worker = hw.HotcueWorker(str(nml), candidates, force=force)
    finished: list[tuple[int, str]] = []
    errors: list[str] = []
    worker.finished.connect(lambda n, p: finished.append((n, p)))
    worker.error.connect(errors.append)
    worker.run()
    return worker, finished, errors


def _cues(nml: Path, filename: str) -> list[dict[str, str]]:
    """The hotcues (slot 1 and up) written on the track called *filename*."""
    for entry in ET.parse(str(nml)).getroot().iter("ENTRY"):
        loc = entry.find("LOCATION")
        if loc is not None and loc.get("FILE") == filename:
            return [
                dict(c.attrib) for c in entry.findall("CUE_V2") if int(c.get("HOTCUE", "-1")) >= 1
            ]
    raise AssertionError(f"{filename} not in {nml}")


# ── What is handed to detection ─────────────────────────────────────────────


def test_only_tracks_without_cues_are_analysed(nml, detect) -> None:
    _run(nml)
    assert sorted(Path(c[0]).name for c in detect.calls) == ["a.mp3", "b.mp3"]


def test_detection_gets_the_bpm_and_room_for_seven_cues(nml, detect) -> None:
    _run(nml)
    assert {(c[1], c[3]) for c in detect.calls} == {(120.0, 7)}


# ── What is written ─────────────────────────────────────────────────────────


def test_the_cues_found_end_up_in_the_collection(nml, detect) -> None:
    detect.results = {"a.mp3": CUES, "b.mp3": CUES}

    _, finished, errors = _run(nml)

    assert errors == []
    for name in ("a.mp3", "b.mp3"):
        written = _cues(nml, name)
        assert [(c["NAME"], float(c["START"]), c["TYPE"], c["HOTCUE"]) for c in written] == [
            ("tk: Drop", 30_000.0, "0", "1"),
            ("tk: Break", 60_000.0, "0", "2"),
        ]


def test_finished_reports_how_many_tracks_were_written(nml, detect) -> None:
    detect.results = {"a.mp3": CUES, "b.mp3": CUES}
    _, finished, _ = _run(nml)
    assert finished == [(2, str(nml))]


def test_a_track_with_no_cues_found_is_left_out_of_the_count(nml, detect) -> None:
    detect.results = {"a.mp3": CUES}  # b.mp3: nothing detected
    worker, finished, _ = _run(nml)
    assert finished == [(1, str(nml))]
    assert _cues(nml, "b.mp3") == []
    assert worker.outcome["cued"] == 1
    assert worker.outcome["none: silence"] == 1


def test_the_grid_marker_is_kept(nml, detect) -> None:
    detect.results = {"a.mp3": CUES}
    _run(nml)
    entry = next(
        e
        for e in ET.parse(str(nml)).getroot().iter("ENTRY")
        if e.find("LOCATION").get("FILE") == "a.mp3"
    )
    assert any(c.get("TYPE") == "4" for c in entry.findall("CUE_V2"))


def test_a_backup_is_made_before_the_write(nml, detect) -> None:
    detect.results = {"a.mp3": CUES}
    _run(nml)
    backups = [p for p in nml.parent.iterdir() if p != nml and p.name.startswith(nml.name)]
    assert backups, "collection.nml was rewritten with no backup"
    assert any(p.read_text() == NML for p in backups)


def test_user_cues_are_not_touched_even_if_the_track_is_offered(nml, detect) -> None:
    """c.mp3 is not a candidate, but if it were handed over anyway the
    write itself must still refuse to touch a user's cues."""
    detect.results = {"c.mp3": CUES}
    candidates = [(str(Path("/m/c.mp3")), 120.0, 10.0)]

    _, finished, _ = _run(nml, candidates)

    assert finished == [(0, str(nml))]
    assert [c["NAME"] for c in _cues(nml, "c.mp3")] == ["My cue"]


def test_other_tracks_are_left_alone(nml, detect) -> None:
    detect.results = {"a.mp3": CUES}
    _run(nml)
    assert [c["NAME"] for c in _cues(nml, "c.mp3")] == ["My cue"]
    assert [c["NAME"] for c in _cues(nml, "d.mp3")] == ["tk: Old"]


def test_force_replaces_our_own_cues_but_never_the_users(nml, detect) -> None:
    detect.results = {"c.mp3": CUES, "d.mp3": CUES}
    candidates = get_hotcue_candidates(nml, include_existing=True)

    _, finished, _ = _run(nml, candidates, force=True)

    assert [c["NAME"] for c in _cues(nml, "d.mp3")] == ["tk: Drop", "tk: Break"]
    user = [c["NAME"] for c in _cues(nml, "c.mp3")]
    assert "My cue" in user
    assert finished[0][0] >= 1


# ── Nothing to write ────────────────────────────────────────────────────────


def test_no_candidates_finishes_with_zero_and_writes_nothing(nml, detect) -> None:
    _, finished, _ = _run(nml, candidates=[])
    assert finished == [(0, str(nml))]
    assert detect.calls == []
    assert nml.read_text() == NML


def test_nothing_detected_leaves_the_file_byte_for_byte(nml, detect) -> None:
    _, finished, errors = _run(nml)
    assert finished == [(0, str(nml))]
    assert errors == []
    assert nml.read_text() == NML


# ── Failures ────────────────────────────────────────────────────────────────


def test_one_track_failing_is_reported_and_the_rest_are_written(nml, detect) -> None:
    detect.results = {"a.mp3": RuntimeError("cannot decode"), "b.mp3": CUES}

    worker, finished, errors = _run(nml)

    assert errors == ["a.mp3: cannot decode"]
    assert finished == [(1, str(nml))]
    assert worker.outcome["failed"] == 1
    assert _cues(nml, "a.mp3") == []
    assert len(_cues(nml, "b.mp3")) == 2


def test_an_unwritable_collection_is_reported_and_finished_still_fires(
    nml, detect, monkeypatch
) -> None:
    detect.results = {"a.mp3": CUES}

    def refuse(*_a, **_kw):
        raise RuntimeError("disk full")

    monkeypatch.setattr(hw, "write_hotcues_to_nml", refuse)

    _, finished, errors = _run(nml)

    assert errors == ["NML write failed: disk full"]
    assert finished == [(0, str(nml))]
    assert nml.read_text() == NML


def test_a_broken_pool_is_reported_and_finished_still_fires(nml, detect, monkeypatch) -> None:
    """The window waits on ``finished`` to enable its controls again."""

    class Broken(_InlinePool):
        def submit(self, fn, *args):
            raise OSError("cannot start workers")

    monkeypatch.setattr(hw, "ProcessPoolExecutor", Broken)

    _, finished, errors = _run(nml)

    assert finished == [(0, str(nml))]
    assert len(errors) == 1 and "cannot start workers" in errors[0]


def test_the_pool_is_shut_down_afterwards(nml, detect, monkeypatch) -> None:
    pools: list[_InlinePool] = []

    class Recording(_InlinePool):
        def __init__(self, *a, **kw) -> None:
            super().__init__(*a, **kw)
            pools.append(self)

    monkeypatch.setattr(hw, "ProcessPoolExecutor", Recording)
    detect.results = {"a.mp3": CUES}

    worker, _, _ = _run(nml)

    assert len(pools) == 1 and pools[0].shut_down
    assert worker._pool is None
