"""Filling in the computed mask for a library analysed before it existed.

Without this every one of an existing library's tracks reads as never
analysed, and the first scan after upgrading re-runs CLAP on all of them.
"""

from __future__ import annotations

import sqlite3

import pytest

from core.db import repair_computed_masks
from core.feature_bits import mask_for, missing_features


@pytest.fixture
def db(tmp_path, monkeypatch):
    import core.db as db_mod

    monkeypatch.setattr(db_mod, "_db_path", lambda: tmp_path / "t.db")
    db_mod.init_db()
    conn = sqlite3.connect(tmp_path / "t.db")
    yield conn
    conn.close()


def _mask(db, path: str) -> int | None:
    return db.execute("SELECT computed FROM tracks WHERE path = ?", (path,)).fetchone()[0]


def test_a_track_with_values_is_marked_as_analysed(db) -> None:
    from core.db import seed_computed_masks

    db.execute(
        "INSERT INTO tracks (path, filename, melodic, danceability) "
        "VALUES ('/m/a.mp3','a.mp3', 0.7, 0.9)"
    )
    db.commit()
    seed_computed_masks(conn=db)
    assert missing_features(_mask(db, "/m/a.mp3"), mask_for(["melodic", "danceability"])) == []


def test_a_feature_with_no_value_is_not_claimed(db) -> None:
    from core.db import seed_computed_masks

    db.execute("INSERT INTO tracks (path, filename, melodic) VALUES ('/m/a.mp3','a.mp3', 0.7)")
    db.commit()
    seed_computed_masks(conn=db)
    assert missing_features(_mask(db, "/m/a.mp3"), mask_for(["melodic", "danceability"])) == [
        "danceability"
    ]


def test_an_unanalysed_track_stays_unanalysed(db) -> None:
    from core.db import seed_computed_masks

    db.execute("INSERT INTO tracks (path, filename) VALUES ('/m/a.mp3','a.mp3')")
    db.commit()
    seed_computed_masks(conn=db)
    assert (_mask(db, "/m/a.mp3") or 0) == 0


def test_seeding_twice_changes_nothing(db) -> None:
    """It runs on every start; it must not undo a mask the scan has set."""
    from core.db import seed_computed_masks

    db.execute("INSERT INTO tracks (path, filename, melodic) VALUES ('/m/a.mp3','a.mp3', 0.7)")
    db.commit()
    seed_computed_masks(conn=db)
    first = _mask(db, "/m/a.mp3")
    seed_computed_masks(conn=db)
    assert _mask(db, "/m/a.mp3") == first


def test_a_mask_the_scan_already_wrote_is_left_alone(db) -> None:
    from core.db import seed_computed_masks

    written = mask_for(["bounce"])
    db.execute(
        "INSERT INTO tracks (path, filename, melodic, computed) "
        "VALUES ('/m/a.mp3','a.mp3', 0.7, ?)",
        (written,),
    )
    db.commit()
    seed_computed_masks(conn=db)
    assert _mask(db, "/m/a.mp3") == written


# ── Repairing a bit that lied ───────────────────────────────────────────────


def test_a_bit_set_over_an_empty_column_is_cleared(db) -> None:
    """The scan computed these, the write dropped them, and the mask said
    done — so nothing would ever have re-run them."""
    from core.db import repair_computed_masks

    db.execute(
        "INSERT INTO tracks (path, filename, melodic, computed) "
        "VALUES ('/m/a.mp3','a.mp3', 0.7, ?)",
        (mask_for(["melodic", "sameness"]),),
    )
    db.commit()
    assert repair_computed_masks(conn=db) == 1
    assert missing_features(_mask(db, "/m/a.mp3"), mask_for(["melodic", "sameness"])) == [
        "sameness"
    ]


def test_a_truthful_mask_is_left_alone(db) -> None:
    from core.db import repair_computed_masks

    db.execute(
        "INSERT INTO tracks (path, filename, melodic, sameness, computed) "
        "VALUES ('/m/a.mp3','a.mp3', 0.7, 0.4, ?)",
        (mask_for(["melodic", "sameness"]),),
    )
    db.commit()
    repair_computed_masks(conn=db)
    assert missing_features(_mask(db, "/m/a.mp3"), mask_for(["melodic", "sameness"])) == []


def test_every_bit_that_lied_is_cleared_at_once(db) -> None:
    claimed = mask_for(["melodic", "sameness", "danceability", "bounce"])
    db.execute(
        "INSERT INTO tracks (path, filename, melodic, computed) "
        "VALUES ('/m/a.mp3','a.mp3', 0.7, ?)",
        (claimed,),
    )
    db.execute(
        "INSERT INTO tracks (path, filename, danceability, computed) "
        "VALUES ('/m/b.mp3','b.mp3', 0.9, ?)",
        (claimed,),
    )
    db.execute(
        "INSERT INTO tracks (path, filename, melodic, computed) "
        "VALUES ('/m/ok.mp3','ok.mp3', 0.7, ?)",
        (mask_for(["melodic"]),),
    )
    db.commit()

    assert repair_computed_masks(conn=db) == 2, "counted in tracks"

    assert _mask(db, "/m/a.mp3") == mask_for(["melodic"])
    assert _mask(db, "/m/b.mp3") == mask_for(["danceability"])
    assert _mask(db, "/m/ok.mp3") == mask_for(["melodic"])


def test_a_track_with_no_mask_is_not_given_one(db) -> None:
    db.execute("INSERT INTO tracks (path, filename) VALUES ('/m/a.mp3','a.mp3')")
    db.commit()

    assert repair_computed_masks(conn=db) == 0
    assert _mask(db, "/m/a.mp3") is None


# It runs on every start, before the window exists. One statement per
# feature was 39 passes over the whole table: 367 ms on 20,000 tracks.


def test_the_repair_reads_the_table_once(db) -> None:
    seen: list[str] = []
    db.set_trace_callback(seen.append)

    repair_computed_masks(conn=db)

    db.set_trace_callback(None)
    assert len([s for s in seen if s.lstrip().upper().startswith("UPDATE")]) == 1
