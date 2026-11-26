"""Which tracks a scan has work to do on, and which features it must compute.

The old answer was a global version number: change anything and everything
re-runs. The new one is per feature, so enabling a retired feature backfills
only that feature, and only on the tracks that lack it.
"""

from __future__ import annotations

import sqlite3

import pytest

from core.feature_bits import mask_for
from core.scan_plan import features_to_compute, stale_paths


@pytest.fixture
def db(tmp_path, monkeypatch):
    import core.db as db_mod

    monkeypatch.setattr(db_mod, "_db_path", lambda: tmp_path / "t.db")
    db_mod.init_db()
    conn = sqlite3.connect(tmp_path / "t.db")
    yield conn
    conn.close()


def _track(db, path: str, computed: int | None) -> None:
    db.execute(
        "INSERT INTO tracks (path, filename, computed) VALUES (?,?,?)", (path, path, computed)
    )
    db.commit()


def test_a_track_with_every_wanted_feature_is_not_stale(db) -> None:
    want = mask_for(["melodic", "danceability"])
    _track(db, "/m/a.mp3", want)
    assert stale_paths(want, conn=db) == set()


def test_a_track_missing_one_is_stale(db) -> None:
    _track(db, "/m/a.mp3", mask_for(["melodic"]))
    assert stale_paths(mask_for(["melodic", "danceability"]), conn=db) == {"/m/a.mp3"}


def test_a_never_analysed_track_is_stale(db) -> None:
    _track(db, "/m/a.mp3", None)
    assert stale_paths(mask_for(["melodic"]), conn=db) == {"/m/a.mp3"}


def test_disabling_a_feature_makes_a_track_current_again(db) -> None:
    """The point of the two levels: switching a feature off must not leave
    the library looking permanently stale."""
    _track(db, "/m/a.mp3", mask_for(["melodic"]))
    assert stale_paths(mask_for(["melodic", "aggressive"]), conn=db) == {"/m/a.mp3"}
    assert stale_paths(mask_for(["melodic"]), conn=db) == set()


def test_a_feature_beyond_what_is_wanted_is_harmless(db) -> None:
    _track(db, "/m/a.mp3", mask_for(["melodic", "bounce"]))
    assert stale_paths(mask_for(["melodic"]), conn=db) == set()


# ── Which compute groups a track needs ──────────────────────────────────────


def test_only_the_groups_of_missing_features_run() -> None:
    """Compute is grouped because the features share a decode and an HPSS;
    accounting is per feature. A track missing only a shape feature must not
    re-run CLAP."""
    groups = features_to_compute(
        have=mask_for(["melodic", "danceability"]),
        want=mask_for(["melodic", "danceability", "bounce"]),
    )
    assert groups == {"shape"}


def test_nothing_missing_means_no_work() -> None:
    want = mask_for(["melodic"])
    assert features_to_compute(have=want, want=want) == set()


def test_a_fresh_track_runs_every_wanted_group() -> None:
    groups = features_to_compute(have=None, want=mask_for(["melodic", "danceability", "bounce"]))
    assert groups == {"melodic", "signal", "shape"}


# ── Recording what an analysis produced ─────────────────────────────────────


def test_only_features_that_produced_a_value_are_recorded() -> None:
    """Read from the result, not from what was asked for: a shape feature is
    skipped when Traktor has no BPM, and marking it done would mean the track
    never gets a second chance once it is gridded."""
    from core.feature_bits import missing_features
    from workers.analysis import computed_mask

    got = computed_mask({"melodic": 0.7, "danceability": 0.9, "sameness": None})
    assert missing_features(got, mask_for(["melodic", "danceability"])) == []
    assert missing_features(got, mask_for(["sameness"])) == ["sameness"]


def test_an_empty_result_records_nothing() -> None:
    from workers.analysis import computed_mask

    assert computed_mask({}) == 0


# ── A track only runs the groups it is missing ──────────────────────────────


def test_a_worker_gives_each_track_only_its_missing_groups(tmp_path, monkeypatch) -> None:
    """The whole point: a library that needs one new feature backfills that
    feature instead of decoding and re-classifying everything."""
    import workers.analysis as wa
    from core.features import BY_KEY

    monkeypatch.setattr(
        wa,
        "current_masks",
        lambda: {
            "/m/done.mp3": mask_for(["melodic", "danceability", "bounce"]),
            "/m/needs_shape.mp3": mask_for(["melodic", "danceability"]),
            "/m/fresh.mp3": 0,
        },
    )
    monkeypatch.setattr(wa, "_wanted_mask", lambda: mask_for(["melodic", "danceability", "bounce"]))
    w = wa.AnalysisWorker.__new__(wa.AnalysisWorker)
    w.force = False
    w._compute = frozenset({"signal", "melodic", "shape", "ml"})
    w._wanted = wa._wanted_mask()
    w._masks = wa.current_masks()

    assert w.groups_for("/m/done.mp3") == frozenset()
    assert w.groups_for("/m/needs_shape.mp3") == frozenset({BY_KEY["bounce"].group})
    assert w.groups_for("/m/fresh.mp3") >= {"signal", "melodic", "shape"}


def test_a_forced_run_computes_everything(tmp_path, monkeypatch) -> None:
    import workers.analysis as wa

    w = wa.AnalysisWorker.__new__(wa.AnalysisWorker)
    w.force = True
    w._compute = frozenset({"signal", "ml"})
    w._wanted = 0
    w._masks = {}
    assert w.groups_for("/m/a.mp3") == frozenset({"signal", "ml"})


# ── The global version is gone ──────────────────────────────────────────────


def test_nothing_filters_on_a_global_analysis_version() -> None:
    """It could only say "something changed", so one feature's definition
    changing re-ran every feature on every track."""
    from pathlib import Path

    src = Path(__file__).resolve().parents[2] / "src"
    offenders = []
    for py in (src / "core" / "db.py", src / "workers" / "analysis.py"):
        for i, line in enumerate(py.read_text().splitlines(), 1):
            if "analysis_version = ?" in line or "analysis_version=?" in line:
                offenders.append(f"{py.name}:{i}")
    assert not offenders, f"still gating on the global version: {offenders}"


def test_the_up_to_date_count_is_per_feature(tmp_path, monkeypatch) -> None:
    import core.db as db_mod

    monkeypatch.setattr(db_mod, "_db_path", lambda: tmp_path / "t.db")
    db_mod.init_db()
    with db_mod.get_conn() as c:
        c.execute(
            "INSERT INTO tracks (path, filename, computed) VALUES ('/a','a',?)",
            (mask_for(["melodic"]),),
        )
        c.execute("INSERT INTO tracks (path, filename, computed) VALUES ('/b','b',0)")
        c.commit()
    monkeypatch.setattr(db_mod, "_wanted_for_count", lambda: mask_for(["melodic"]))
    assert db_mod.get_track_count() == (2, 1)


def test_traktor_metadata_never_makes_a_track_stale() -> None:
    """It is re-read from the NML on every scan, so it cannot be missing in
    the sense the mask means — and counting it would make every track with
    no rating look permanently unanalysed."""
    from core.scan_plan import computable_features

    keys = {f.key for f in computable_features()}
    assert not keys & {"bpm", "key", "rating", "genre"}
    assert "melodic" in keys and "bounce" in keys
