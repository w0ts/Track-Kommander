"""The genre, label, year and import day the matcher sees (core.track_meta),
and the axes that score them (core.ranking)."""

from __future__ import annotations

import json
import sqlite3

import pytest

from core import track_meta as tm

# ── genre ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "junk", ["www.electronicfresh.com", "https://djsoundtop.com", "Other", "UNKNOWN", "Misc", " "]
)
def test_junk_genres_are_no_genre(junk: str) -> None:
    assert tm.clean_genre(junk) == ""


def test_a_real_genre_is_kept_as_typed() -> None:
    assert tm.clean_genre("Techno (Raw / Deep / Hypnotic)") == "Techno (Raw / Deep / Hypnotic)"


def test_discogs_genres_and_styles_win_over_the_tag() -> None:
    genres, styles = json.dumps(["Electronic"]), json.dumps(["Techno", "Minimal"])
    assert tm.effective_genre("www.site.com", genres, styles) == "Electronic, Techno, Minimal"
    assert tm.effective_genre("Tech House", None, None) == "Tech House"


def test_non_latin_genres_have_words() -> None:
    from core.genre_match import genre_tokens

    assert genre_tokens("Электронная музыка") == frozenset({"электронная", "музыка"})


# ── label ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("label", "canonical"),
    [
        ("Planet Rhythm Records", "planet rhythm"),
        ("Planet Rhythm", "planet rhythm"),
        ("Planet Rhythm (2)", "planet rhythm"),
        ("Hyperdub Recordings Ltd", "hyperdub"),
        ("Mutual Rytm", "mutual rytm"),
        ("M-Plant", "m plant"),  # not a pressing plant
    ],
)
def test_labels_are_tidied(label: str, canonical: str) -> None:
    assert tm.canonical_label(label) == canonical


@pytest.mark.parametrize(
    "not_a_label",
    [
        "www.beatport.com",
        "Not On Label",
        "Believe",
        "Label Worx",
        "Triple Vision Music Group",
        "Hardgroove Mastering",
        "Schallplattenfabrik Pallas GmbH",
        "2429111 Records DK",
    ],
)
def test_distributors_and_plants_are_not_labels(not_a_label: str) -> None:
    assert tm.canonical_label(not_a_label) == ""


def test_traktors_label_first_then_discogs() -> None:
    assert tm.effective_label("Smile Sessions", "Other Label") == "smile sessions"
    assert tm.effective_label("www.junk.com", "Smile Sessions Records") == "smile sessions"
    assert tm.effective_label("", None) == ""


# ── dates ─────────────────────────────────────────────────────────────────────


def test_the_original_year_comes_first() -> None:
    assert tm.effective_year("2024/1/1", 1994, 2016) == 1994
    assert tm.effective_year("2024/1/1", None, 2016) == 2016
    assert tm.effective_year("2019/9/27", None, None) == 2019
    assert tm.effective_year("", None, None) is None


def test_import_days() -> None:
    assert tm.import_day("2025/9/5") - tm.import_day("2025/9/1") == 4
    assert tm.import_day("2025/2/31") is None
    assert tm.import_day("") is None


# ── the axes ─────────────────────────────────────────────────────────────────


@pytest.fixture()
def snap(tmp_path, monkeypatch):
    import core.db as db
    from core import discogs_store
    from core.discogs_fields import DiscogsRecord
    from core.discogs_store import Lookup, record_fields
    from core.library_snapshot import snapshot

    monkeypatch.setattr(db, "_db_path", lambda: tmp_path / "t.db")
    db.init_db()
    tracks = [
        # path, label, release date, import date
        ("/m/src.mp3", "Planet Rhythm Records", "2019/5/1", "2025/9/1"),
        ("/m/same.mp3", "Planet Rhythm", "2019/1/1", "2025/9/3"),
        ("/m/other.mp3", "Hyperdub", "2009/1/1", "2023/1/1"),
        ("/m/none.mp3", "", "", ""),
        ("/m/discogs.mp3", "", "", "2025/8/20"),
    ]
    for path, *_meta in tracks:
        db.upsert_track(path, path[3:], 130.0, 1, True, "8A", 300.0)
    with sqlite3.connect(tmp_path / "t.db") as c:
        for path, label, released, imported in tracks:
            c.execute(
                "UPDATE tracks SET label = ?, release_date = ?, import_date = ? WHERE path = ?",
                (label, released, imported, path),
            )
    record = DiscogsRecord(1, ("Electronic",), ("Techno",), "Planet Rhythm Records (2)", year=2019)
    discogs_store.save(Lookup("/m/discogs.mp3", "matched", 1.0, record_fields(record)))
    return snapshot(db.get_conn)


def _scores(snap, kind_fn):
    score, has = kind_fn
    return {
        snap.paths[i]: (round(float(score[i]), 1) if has[i] else None) for i in range(len(snap))
    }


def test_label_axis_same_other_and_unknown(snap) -> None:
    from core.ranking import _label

    src = snap.rows[snap.index["/m/src.mp3"]]
    got = _scores(snap, _label(snap, src))
    assert got["/m/same.mp3"] == 100.0
    assert got["/m/other.mp3"] == 0.0
    assert got["/m/none.mp3"] is None  # no label: not scored, not a mismatch
    assert got["/m/discogs.mp3"] == 100.0  # Traktor has none; the Discogs match does


def test_year_and_import_follow_the_track_and_fall_off(snap) -> None:
    """Numbers like any scalar axis: synced to the source's own value, a
    narrow Gaussian — years for the release, weeks for the import."""
    from core.features import BY_KEY
    from core.ranking import _scalar

    i = snap.index["/m/src.mp3"]

    def scores(key: str) -> dict:
        spec = BY_KEY[key]
        values, has = snap.normalised(spec)
        return _scores(snap, _scalar(snap, spec, float(values[i])))

    years = scores("year")
    assert years["/m/same.mp3"] == 100.0
    assert years["/m/other.mp3"] < 1.0  # ten years apart
    assert years["/m/none.mp3"] is None
    assert years["/m/discogs.mp3"] == 100.0  # Discogs' year, Traktor had none
    days = scores("imported")
    assert days["/m/same.mp3"] > days["/m/discogs.mp3"] > days["/m/other.mp3"]


def test_year_and_import_are_numbers_not_relational() -> None:
    from core.features import BY_KEY

    for key in ("year", "imported"):
        spec = BY_KEY[key]
        assert spec.kind == "scalar" and not spec.relational and spec.derived


def test_dates_read_as_dates_on_the_stats_page() -> None:
    from datetime import date

    from core.features import BY_KEY

    imported, year = BY_KEY["imported"], BY_KEY["year"]
    assert imported.fmt_value is not None and imported.fmt_width is not None
    assert imported.fmt_value(date(2025, 9, 5).toordinal()) == "2025-09-05"
    assert imported.fmt_width(20) == "20 d"
    assert imported.fmt_width(572) == "19 mo"
    assert imported.fmt_width(1000) == "2.7 y"
    assert year.fmt_value is not None and year.fmt_value(2019.4) == "2019"
