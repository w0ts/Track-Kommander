"""Capturing a wrong beat grid from the phase meter, and applying the fix.

With the tempos locked (sync on, or matched by ear) the phase meter stops
drifting, so whatever offset a jog nudge leaves standing is the error in the
track's beat grid — in beats. That is what gets queued, and written to the
AutoGrid marker in collection.nml once Traktor is closed.
"""

from __future__ import annotations

import pytest

from core.collection_write import backups_of
from core.grid_fix import (
    GridFix,
    GridQueue,
    PhaseTracker,
    apply_grid_fixes,
    phase_to_ms,
)

# ── Beats to milliseconds ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("phase", "bpm", "ms"),
    [
        (0.0, 120.0, 0.0),
        (0.5, 120.0, 250.0),  # half a beat at 120 = 250 ms
        (-0.25, 120.0, -125.0),
        (0.27, 130.0, 124.6),
    ],
)
def test_phase_to_ms(phase: float, bpm: float, ms: float) -> None:
    assert phase_to_ms(phase, bpm) == pytest.approx(ms, abs=0.1)


def test_a_bpm_of_zero_has_no_answer() -> None:
    assert phase_to_ms(0.25, 0.0) is None


# ── Only a settled phase means anything ─────────────────────────────────────


def test_a_held_phase_is_capturable() -> None:
    t = PhaseTracker()
    for i in range(6):
        t.add("A", -0.0030, at=100.0 + i)
    assert t.settled("A") is True
    assert t.latest("A") == -0.0030


def test_a_drifting_phase_is_not() -> None:
    """Tempos not matched: the phase runs and wraps. Nothing to record."""
    t = PhaseTracker()
    for i, ph in enumerate((0.20, 0.26, 0.32, 0.38, 0.44, 0.49)):
        t.add("A", ph, at=100.0 + i)
    assert t.settled("A") is False


def test_a_wrap_counts_as_drift() -> None:
    t = PhaseTracker()
    for i, ph in enumerate((0.44, 0.49, -0.47, -0.41, -0.35, -0.29)):
        t.add("A", ph, at=100.0 + i)
    assert t.settled("A") is False


def test_too_few_samples_is_not_settled() -> None:
    t = PhaseTracker()
    t.add("A", 0.1, at=100.0)
    t.add("A", 0.1, at=101.0)
    assert t.settled("A") is False


def test_stale_samples_do_not_count() -> None:
    """The bridge went quiet — an old reading says nothing about now."""
    t = PhaseTracker()
    for i in range(6):
        t.add("A", 0.1, at=100.0 + i)
    assert t.settled("A", now=200.0) is False


def test_a_deck_never_seen_is_not_settled() -> None:
    assert PhaseTracker().settled("D") is False


# ── The queue ───────────────────────────────────────────────────────────────


def _fix(path: str = "/m/a.mp3", phase: float = 0.27, bpm: float = 130.0) -> GridFix:
    return GridFix(
        path=path,
        deck="A",
        phase=phase,
        bpm=bpm,
        title="T",
        artist="A",
        captured_at="2026-09-22T14:00:00",
    )


def test_the_queue_round_trips(tmp_path) -> None:
    q = GridQueue(tmp_path / "fixes.json")
    q.add(_fix())
    assert [f.path for f in GridQueue(tmp_path / "fixes.json").entries()] == ["/m/a.mp3"]


def test_capturing_a_track_twice_keeps_the_later_reading(tmp_path) -> None:
    """A second go at the same track is a correction, not another entry."""
    q = GridQueue(tmp_path / "fixes.json")
    q.add(_fix(phase=0.27))
    q.add(_fix(phase=0.05))
    assert [f.phase for f in q.entries()] == [0.05]


def test_entries_can_be_dropped(tmp_path) -> None:
    q = GridQueue(tmp_path / "fixes.json")
    q.add(_fix())
    q.add(_fix(path="/m/b.mp3"))
    q.remove("/m/a.mp3")
    assert [f.path for f in q.entries()] == ["/m/b.mp3"]
    q.clear()
    assert q.entries() == []


def test_a_corrupt_queue_file_is_not_fatal(tmp_path) -> None:
    p = tmp_path / "fixes.json"
    p.write_text("{not json")
    assert GridQueue(p).entries() == []


def test_the_offset_is_carried_on_the_entry() -> None:
    assert _fix(phase=0.5, bpm=120.0).offset_ms == pytest.approx(250.0)


# ── Writing it to the collection ────────────────────────────────────────────

_NML = """<?xml version="1.0" encoding="UTF-8"?>
<NML VERSION="19">
 <COLLECTION ENTRIES="2">
  <ENTRY TITLE="A" ARTIST="x">
   <LOCATION DIR="/:m/:" FILE="a.mp3" VOLUME="Macintosh HD"></LOCATION>
   <TEMPO BPM="130.000000" BPM_QUALITY="100.000000"></TEMPO>
   <CUE_V2 NAME="AutoGrid" DISPL_ORDER="0" TYPE="4" START="130.752340" LEN="0.000000" REPEATS="-1" HOTCUE="-1"></CUE_V2>
   <CUE_V2 NAME="n.n." DISPL_ORDER="0" TYPE="0" START="7749.802266" LEN="0.000000" REPEATS="-1" HOTCUE="0"></CUE_V2>
  </ENTRY>
  <ENTRY TITLE="B" ARTIST="y">
   <LOCATION DIR="/:m/:" FILE="b.mp3" VOLUME="Macintosh HD"></LOCATION>
   <TEMPO BPM="120.000000"></TEMPO>
  </ENTRY>
 </COLLECTION>
</NML>
"""


@pytest.fixture
def nml(tmp_path):
    p = tmp_path / "collection.nml"
    p.write_text(_NML)
    return p


def _grid_start(nml_path) -> float:
    import xml.etree.ElementTree as ET

    for cue in ET.parse(str(nml_path)).getroot().iter("CUE_V2"):
        if cue.get("TYPE") == "4":
            return float(cue.get("START"))
    raise AssertionError("no grid marker")


def test_the_grid_marker_moves_by_the_offset(nml) -> None:
    done, skipped = apply_grid_fixes(nml, [_fix(path="/m/a.mp3", phase=0.27, bpm=130.0)])
    assert (done, skipped) == (1, [])
    assert _grid_start(nml) == pytest.approx(130.752340 + 124.6, abs=0.2)


def test_the_hotcues_are_left_alone(nml) -> None:
    import xml.etree.ElementTree as ET

    apply_grid_fixes(nml, [_fix(path="/m/a.mp3")])
    starts = [
        c.get("START") for c in ET.parse(str(nml)).getroot().iter("CUE_V2") if c.get("TYPE") == "0"
    ]
    assert starts == ["7749.802266"]


def test_a_backup_is_made(nml) -> None:
    before = nml.read_text()
    apply_grid_fixes(nml, [_fix(path="/m/a.mp3")])
    [backup] = backups_of(nml)
    assert backup.read_text() == before


def test_a_track_not_in_the_collection_is_reported(nml) -> None:
    done, skipped = apply_grid_fixes(nml, [_fix(path="/m/gone.mp3")])
    assert done == 0
    assert skipped and "gone.mp3" in skipped[0]


def test_the_separators_in_the_path_do_not_decide_it(tmp_path) -> None:
    """The collection's paths are spelled the platform's way, backslashes on
    Windows, and a fix carries the path as Traktor reported it. Compared
    letter for letter, a path spelled with the other separator was "not in
    the collection" and its fix was never written."""
    nml = tmp_path / "collection.nml"
    nml.write_text(_NML.replace('VOLUME="Macintosh HD"', 'VOLUME="C:"'))

    done, skipped = apply_grid_fixes(nml, [_fix(path="C:/m/a.mp3")])

    assert (done, skipped) == (1, [])


def test_a_track_with_no_grid_marker_is_reported(nml) -> None:
    done, skipped = apply_grid_fixes(nml, [_fix(path="/m/b.mp3", bpm=120.0)])
    assert done == 0
    assert skipped and "b.mp3" in skipped[0]


def test_a_grid_on_the_offbeat_is_shifted_half_a_beat(nml) -> None:
    """The common error: everything lands between the beats."""
    done, _skipped = apply_grid_fixes(nml, [_fix(path="/m/a.mp3", phase=-0.5, bpm=130.0)])
    assert done == 1
    beat = 60_000 / 130.0
    # -0.5 beat from 130.75 ms goes negative, so it comes back a beat later —
    # the same grid either way.
    assert _grid_start(nml) == pytest.approx(130.752340 - beat / 2 + beat, abs=0.2)


def test_a_marker_never_goes_negative(nml) -> None:
    """Shifting the first beat before the start of the file is not a grid."""
    done, skipped = apply_grid_fixes(nml, [_fix(path="/m/a.mp3", phase=-0.5, bpm=130.0)])
    assert done == 1
    assert _grid_start(nml) == pytest.approx(130.752340 - 230.77 + 60000 / 130.0, abs=0.2)


def test_nothing_to_do_writes_nothing(nml) -> None:
    before = nml.read_text()
    assert apply_grid_fixes(nml, []) == (0, [])
    assert nml.read_text() == before
    assert backups_of(nml) == []
