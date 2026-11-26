"""What the Library's stats line says about analysis.

It used to say "none at v8": the global analysis version, retired when each
feature got its own bit. A version number says nothing a user can act on.
The line now names what tracks are missing — and, for structure and
bassline, the reason that is usually the cause: no BPM in Traktor.
"""

from __future__ import annotations

import pytest

import core.db as db_mod
from core.analysis_status import describe
from core.db import AnalysisGaps
from core.feature_bits import mask_for

SHAPE = [
    "loop",
    "sameness",
    "evolution",
    "onset_density",
    "sections",
    "bass_density",
    "bass_range",
    "bounce",
]


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(db_mod, "_db_path", lambda: tmp_path / "t.db")
    db_mod.init_db()
    return db_mod


def _track(db_mod, path: str, computed: int, bpm: float | None = 128.0) -> None:
    with db_mod.get_conn() as c:
        c.execute(
            "INSERT INTO tracks (path, filename, computed, bpm) VALUES (?,?,?,?)",
            (path, path, computed, bpm),
        )
        c.commit()


# ── The breakdown ───────────────────────────────────────────────────────────


def test_gaps_are_counted_per_compute_group(db, monkeypatch) -> None:
    want = mask_for(["melodic", *SHAPE])
    monkeypatch.setattr(db, "_wanted_for_count", lambda: want)
    _track(db, "/done", want)
    _track(db, "/no-shape", mask_for(["melodic"]))
    _track(db, "/no-shape-no-bpm", mask_for(["melodic"]), bpm=None)
    _track(db, "/nothing", 0)

    gaps = db.analysis_gaps()
    assert gaps.total == 4
    assert gaps.complete == 1
    assert gaps.missing == {"shape": 3, "melodic": 1}
    assert gaps.shape_without_bpm == 1


def test_a_track_never_analysed_counts_as_missing_everything(db, monkeypatch) -> None:
    monkeypatch.setattr(db, "_wanted_for_count", lambda: mask_for(["melodic"]))
    with db.get_conn() as c:
        c.execute("INSERT INTO tracks (path, filename) VALUES ('/x','x')")
        c.commit()
    gaps = db.analysis_gaps()
    assert gaps.complete == 0 and gaps.missing == {"melodic": 1}


def test_nothing_wanted_means_everything_is_complete(db, monkeypatch) -> None:
    monkeypatch.setattr(db, "_wanted_for_count", lambda: 0)
    _track(db, "/a", 0)
    gaps = db.analysis_gaps()
    assert gaps.complete == 1 and gaps.missing == {}


# ── The sentence ────────────────────────────────────────────────────────────


def _gaps(**kw):
    base = dict(total=10, complete=10, missing={}, shape_without_bpm=0)
    base.update(kw)
    return AnalysisGaps(**base)


def test_no_version_number_is_shown() -> None:
    for g in (_gaps(), _gaps(complete=0, missing={"ml": 10})):
        assert " v8" not in describe(g) and "at v" not in describe(g)


def test_all_done() -> None:
    assert describe(_gaps()) == "10 tracks in database — all fully analysed"


def test_empty() -> None:
    assert "No tracks" in describe(_gaps(total=0, complete=0))


def test_it_names_what_is_missing() -> None:
    text = describe(_gaps(complete=6, missing={"shape": 4, "ml": 1}))
    assert "6 fully analysed" in text
    assert "Structure · bassline: 4" in text
    assert "CLAP" in text


def test_it_says_when_the_bpm_is_the_reason() -> None:
    """A scan cannot fix these: the tempo comes from Traktor."""
    text = describe(_gaps(complete=6, missing={"shape": 4}, shape_without_bpm=4))
    assert "4 have no BPM in Traktor" in text
