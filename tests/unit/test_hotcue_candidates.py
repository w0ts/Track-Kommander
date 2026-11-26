"""Which tracks auto-hotcue detection should process.

Detection decodes audio, so processing a track twice is slow for no new
result. The filter previously treated its own ``tk:`` cues as replaceable,
which meant almost the whole library was re-analysed on every run — measured
at 890 of 891 "candidates" already carrying our cues. Re-running those is what
the "Reprocess existing hotcues" option is for.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from core.traktor import get_hotcue_candidates, hotcue_eligibility

NML = """<?xml version="1.0" encoding="UTF-8"?>
<NML VERSION="19"><COLLECTION ENTRIES="5">
 <ENTRY TITLE="Untouched">
  <LOCATION DIR="/:m/:" FILE="a.mp3" VOLUME="HD"/>
  <TEMPO BPM="128.0" BPM_QUALITY="100.0"/>
  <CUE_V2 NAME="AutoGrid" TYPE="4" START="10.0" HOTCUE="0"/>
 </ENTRY>
 <ENTRY TITLE="Ours already">
  <LOCATION DIR="/:m/:" FILE="b.mp3" VOLUME="HD"/>
  <TEMPO BPM="128.0" BPM_QUALITY="100.0"/>
  <CUE_V2 NAME="AutoGrid" TYPE="4" START="10.0" HOTCUE="0"/>
  <CUE_V2 NAME="tk: Drop" TYPE="0" START="5000.0" HOTCUE="1"/>
 </ENTRY>
 <ENTRY TITLE="User cues">
  <LOCATION DIR="/:m/:" FILE="c.mp3" VOLUME="HD"/>
  <TEMPO BPM="128.0" BPM_QUALITY="100.0"/>
  <CUE_V2 NAME="AutoGrid" TYPE="4" START="10.0" HOTCUE="0"/>
  <CUE_V2 NAME="My cue" TYPE="0" START="5000.0" HOTCUE="2"/>
 </ENTRY>
 <ENTRY TITLE="No grid">
  <LOCATION DIR="/:m/:" FILE="d.mp3" VOLUME="HD"/>
  <TEMPO BPM="128.0" BPM_QUALITY="100.0"/>
 </ENTRY>
 <ENTRY TITLE="Analysed, never loaded">
  <LOCATION DIR="/:m/:" FILE="e.mp3" VOLUME="HD"/>
  <TEMPO BPM="140.0" BPM_QUALITY="100.0"/>
  <CUE_V2 NAME="AutoGrid" TYPE="4" START="53.2" HOTCUE="-1"/>
 </ENTRY>
</COLLECTION></NML>
"""


@pytest.fixture
def nml(tmp_path) -> Path:
    p = tmp_path / "collection.nml"
    p.write_text(NML)
    return p


def _names(nml: Path, include_existing: bool = False) -> set[str]:
    return {Path(p).name for p, _bpm, _phase in get_hotcue_candidates(nml, include_existing)}


def test_only_tracks_without_hotcues_are_processed(nml: Path) -> None:
    assert _names(nml) == {"a.mp3", "e.mp3"}


def test_our_own_cues_are_not_redone(nml: Path) -> None:
    """The bug: a track we already processed came back as a candidate."""
    assert "b.mp3" not in _names(nml)


def test_user_cues_are_never_touched(nml: Path) -> None:
    assert "c.mp3" not in _names(nml)


def test_a_track_without_a_beat_grid_is_skipped(nml: Path) -> None:
    """No grid marker: there is no beat 1 to snap detected drops to."""
    assert "d.mp3" not in _names(nml)
    assert "d.mp3" not in _names(nml, include_existing=True)


def test_an_analysed_track_never_loaded_in_a_deck_is_processed(nml: Path) -> None:
    """The bug: the slot-0 autocue was required, but Traktor only adds it when
    a track is loaded in a deck — analysis writes the TYPE=4 grid marker
    alone. A library analysed in batch skipped 2611 of 2629 tracks."""
    candidates, _ = hotcue_eligibility(nml)
    assert ("e.mp3", 140.0, 53.2) in {(Path(p).name, b, ph) for p, b, ph in candidates}


def test_reprocessing_includes_tracks_that_already_have_cues(nml: Path) -> None:
    assert _names(nml, include_existing=True) == {"a.mp3", "b.mp3", "c.mp3", "e.mp3"}


def test_missing_nml_is_empty_not_an_error(tmp_path) -> None:
    assert get_hotcue_candidates(tmp_path / "nope.nml") == []


def test_detection_reads_through_the_shared_decoder(monkeypatch) -> None:
    """librosa.load went through libsndfile alone, which hands back 0.39 s of
    a damaged 445 s track without complaint — and 0.39 s has no drops."""
    import core.hotcues as h

    seen = {}

    def _fake_read(path, **kw):
        seen["kw"] = kw
        return np.zeros((h._SR * 10, 1), dtype="float32"), h._SR

    monkeypatch.setattr("core.audio_io.safe_sf_read", _fake_read)
    assert h.detect_drop_positions("/nowhere.mp3", 128.0) == []
    assert seen["kw"]["samplerate"] == h._SR


def test_eligibility_says_why_tracks_were_skipped(nml: Path) -> None:
    """So a run that touches few tracks explains itself."""
    candidates, skipped = hotcue_eligibility(nml)
    assert [Path(p).name for p, _b, _ph in candidates] == ["a.mp3", "e.mp3"]
    assert skipped == {"already has hotcues": 2, "no beat grid": 1}


def test_an_empty_result_says_why(monkeypatch) -> None:
    """A track that could not be read must not look like one with no drops."""
    import core.hotcues as h

    monkeypatch.setattr(
        "core.audio_io.safe_sf_read",
        lambda path, **kw: (np.zeros((h._SR * 3, 1), dtype="float32"), h._SR),
    )
    why: list[str] = []
    assert h.detect_drop_positions("/short.mp3", 128.0, why=why) == []
    assert why and why[0].startswith("decoded under 20 s")


def test_a_silent_track_has_no_drops_and_says_so(monkeypatch) -> None:
    import core.hotcues as h

    monkeypatch.setattr(
        "core.audio_io.safe_sf_read",
        lambda path, **kw: (np.zeros((h._SR * 180, 1), dtype="float32"), h._SR),
    )
    why: list[str] = []
    assert h.detect_drop_positions("/silent.mp3", 128.0, why=why) == []
    assert why == ["no drop or break found"]
