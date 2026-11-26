"""Every feature the analysis produces must survive the write to the database.

`upsert_track` names each column as a keyword argument. A feature added to
the registry and to `analyse_file` but not to that signature is computed,
marked as computed, and then silently dropped — the column stays NULL for
ever and nothing re-runs it, because the mask says it is done.
"""

from __future__ import annotations

import inspect

import pytest


@pytest.fixture
def db(tmp_path, monkeypatch):
    import core.db as db_mod

    monkeypatch.setattr(db_mod, "_db_path", lambda: tmp_path / "t.db")
    db_mod.init_db()
    yield db_mod


def test_every_feature_column_is_a_parameter_of_upsert(db) -> None:
    from core.db import upsert_track
    from core.scan_plan import computable_features

    params = set(inspect.signature(upsert_track).parameters)
    missing = sorted(f.column for f in computable_features() if f.column and f.column not in params)
    assert not missing, f"computed but never written: {missing} — add them to upsert_track"


def test_a_value_written_comes_back(db) -> None:
    from core.db import get_conn, upsert_track
    from core.scan_plan import computable_features

    cols = [f.column for f in computable_features() if f.column]
    values = {c: 0.5 for c in cols}
    upsert_track("/m/a.mp3", "a.mp3", 128.0, 0, True, "8B", 300.0, **values)
    with get_conn() as c:
        row = c.execute("SELECT * FROM tracks WHERE path='/m/a.mp3'").fetchone()
    empty = sorted(c for c in cols if row[c] is None)
    assert not empty, f"written but not stored: {empty}"
