"""The rankers give the answers they always gave.

They were a Python loop over rows and are now arithmetic over arrays
(``core.ranking``). The loop is kept, as it was, in ``reference_matcher.py``,
and every case here ranks the same library both ways and compares them result
for result.

The library is random but fixed by a seed, and full of what a real one holds:
tracks with no tempo, keys that are not keys, tracks never analysed, a genre
tag nobody else has, embeddings missing or all zeros.

**What "the same" means.** The same tracks, the same scores, in the same
order. Scores that went through ``exp`` or a matrix product can differ in the
last bits between one number at a time and an array of them, so they are
compared to nine places (similarity, which is 32-bit arithmetic, to four) —
and two results whose composites are closer than that may swap places.
"""

from __future__ import annotations

import json
import sqlite3

import numpy as np
import pytest
import reference_matcher as reference

import core.matcher as matcher
from core import library_snapshot, ranking
from core.embeddings import EMBEDDING_MODELS, load_vectors, score_similarity_bulk
from core.features import BY_KEY, ActiveAxis
from core.ranking import Ranked

LABELS = ("Techno", "House", "Tech House", "Ambient", "Drum & Bass", "Acid")
GENRES = (
    "Techno",
    "Deep House",
    "Tech House",
    "Dark Ambient · Drone",
    "Drum & Bass / Jungle",
    "Acid Techno",
    "Hard Techno",
    "",
    None,
    "A Genre Nobody Else Has",
)
KEYS = [f"{n}{m}" for n in range(1, 13) for m in "AB"] + [None, "", "X", "13Z", "8"]

#: Every scalar axis the application ships, by the column it reads.
SCALARS = sorted(k for k, s in BY_KEY.items() if s.kind == "scalar" and s.column)
# Stored columns only: the derived ones (release year, import day) are worked
# out from the dates below, by both rankers alike.
SCALAR_COLUMNS = sorted({BY_KEY[k].column for k in SCALARS if not BY_KEY[k].derived})
FINGERPRINT = (
    "danceability",
    "dynamic_complexity",
    "loudness",
    "hfc",
    "low_energy",
    "mid_energy",
    "bassiness",
    "spectral_centroid",
    "low_dynamics",
    "mid_dynamics",
    "high_dynamics",
)
REAL = sorted((set(SCALAR_COLUMNS) | set(FINGERPRINT)) - {"rating", "bpm"})
TEXT = (
    "path", "filename", "camelot", "artist", "title", "genre", "genres",
    "release_date", "import_date",
)  # fmt: skip


def _style(rng) -> str | None:
    roll = rng.random()
    if roll < 0.15:
        return None
    if roll < 0.2:
        return "not json"
    picked = rng.choice(len(LABELS), size=3, replace=False)
    return json.dumps([{"name": LABELS[i], "prob": round(float(rng.random()), 4)} for i in picked])


def _embedding(rng) -> bytes | None:
    roll = rng.random()
    if roll < 0.1:
        return None
    if roll < 0.13:
        return np.zeros(8, dtype=np.float32).tobytes()  # no direction
    if roll < 0.16:
        return rng.normal(size=5).astype(np.float32).tobytes()  # wrong size
    return rng.normal(size=8).astype(np.float32).tobytes()


def _maybe(rng, value, missing: float = 0.15):
    return None if rng.random() < missing else value


@pytest.fixture(scope="module")
def library(tmp_path_factory):
    """400 tracks, and how to reach them."""
    path = str(tmp_path_factory.mktemp("parity") / "library.db")

    def connect() -> sqlite3.Connection:
        c = sqlite3.connect(path)
        c.row_factory = sqlite3.Row
        return c

    columns = [
        *(f"{n} TEXT" for n in TEXT),
        "bpm REAL",
        "rating INTEGER",
        "genres_vector BLOB",
        *(f"{n} REAL" for n in REAL if n != "bpm"),
    ]
    names = [c.split()[0] for c in columns]
    rng = np.random.default_rng(20260929)
    rows = []
    for i in range(400):
        tempo = float(
            rng.choice([0.0, 70.0, 87.5, 120.0, 124.0, 126.5, 128.0, 132.0, 140.0, 174.0])
            + rng.normal() * 1.5
        )
        track = {
            "path": f"/m/{i:04d}.mp3",
            "filename": f"{i:04d}.mp3",
            "camelot": KEYS[rng.integers(len(KEYS))],
            "artist": f"Artist {i % 37}",
            "title": f"Title {i}",
            "genre": GENRES[rng.integers(len(GENRES))],
            "genres": _style(rng),
            "bpm": _maybe(rng, max(0.0, tempo), 0.08),
            "rating": _maybe(rng, int(rng.integers(0, 6)), 0.3),
            "genres_vector": _embedding(rng),
            "release_date": _maybe(rng, f"{int(rng.integers(1985, 2026))}/1/1", 0.2),
            "import_date": _maybe(
                rng,
                f"{int(rng.integers(2019, 2026))}/{int(rng.integers(1, 13))}/{int(rng.integers(1, 28))}",
                0.1,
            ),
        }
        analysed = rng.random() > 0.1  # one in ten never analysed
        for name in REAL:
            if name != "bpm":
                track[name] = (
                    _maybe(rng, float(rng.random() * 1.4 - 0.2), 0.05) if analysed else None
                )
        rows.append([track.get(n) for n in names])
    with connect() as c:
        c.execute(f"CREATE TABLE tracks ({', '.join(columns)})")
        c.executemany(f"INSERT INTO tracks VALUES ({', '.join('?' * len(names))})", rows)
        c.commit()
    return connect


@pytest.fixture(autouse=True)
def both_read_the_same_library(library, monkeypatch):
    monkeypatch.setattr(matcher, "get_conn", library)
    library_snapshot.forget()
    yield
    library_snapshot.forget()


def _source(library, path: str) -> sqlite3.Row:
    with library() as c:
        return c.execute("SELECT * FROM tracks WHERE path = ?", (path,)).fetchone()


def _close(a, b, places: float) -> bool:
    if a is None or b is None or isinstance(a, (str, bool)):
        return a == b
    return abs(a - b) <= places


def _same(new: Ranked | list, old: list, *, places: float = 1e-9) -> None:
    assert len(new) == len(old)
    theirs = {r["row"]["path"]: r for r in old}
    assert {r["row"]["path"] for r in new} == set(theirs)

    for mine in new:
        want = theirs[mine["row"]["path"]]
        where = mine["row"]["path"]
        assert set(mine) == set(want), where
        for key in (
            "composite",
            "bpm_score",
            "key_score",
            "vibe_score",
            "rating_score",
            "effective_bpm",
        ):
            assert _close(mine[key], want[key], places), (where, key, mine[key], want[key])
        assert mine["bpm_factor"] == want["bpm_factor"], where
        assert mine.get("transpose") == want.get("transpose"), where
        assert list(mine["axis_scores"]) == list(want["axis_scores"]), where
        for axis, score in mine["axis_scores"].items():
            assert _close(score, want["axis_scores"][axis], places), (where, axis)
        for column in want["row"].keys():
            if column != "genres_vector":
                assert mine["row"][column] == want["row"][column], (where, column)

    # The order: the same, except that two results a hair apart may swap.
    for position, (mine, want) in enumerate(zip(new, old, strict=True)):
        assert _close(mine["composite"], want["composite"], places), position
        if mine["row"]["path"] != want["row"]["path"]:
            assert _close(theirs[mine["row"]["path"]]["composite"], want["composite"], places), (
                position
            )


SOURCES = ["/m/0000.mp3", "/m/0007.mp3", "/m/0123.mp3", "/m/0399.mp3"]


def _axes(*keys: str, strength: float = 1.0, **more) -> list[ActiveAxis]:
    return [ActiveAxis(k, value=0.5, strength=strength, **more) for k in keys]


CASES = {
    "tempo and key": _axes("bpm", "key"),
    "key alone": _axes("key"),
    "key, transposing": [
        ActiveAxis("bpm", strength=0.7),
        ActiveAxis("key", strength=1.0, max_semitones=3),
    ],
    "key, transposing one semitone": [ActiveAxis("key", strength=1.0, max_semitones=1)],
    "genre": _axes("bpm", "genre"),
    "style": _axes("key", "style"),
    "similarity": _axes("bpm", "key", "similarity"),
    "rating": _axes("rating"),
    "one scalar": [ActiveAxis(SCALARS[0], value=0.9, strength=1.0)],
    "every scalar": [
        ActiveAxis(k, value=(i % 11) / 10, strength=0.2 + i % 5 / 5) for i, k in enumerate(SCALARS)
    ],
    "everything": [
        ActiveAxis("bpm", strength=0.9),
        ActiveAxis("key", strength=0.8, max_semitones=2),
        ActiveAxis("genre", strength=0.4),
        ActiveAxis("style", strength=0.6),
        ActiveAxis("similarity", strength=1.0),
        *[ActiveAxis(k, value=0.3, strength=0.5) for k in SCALARS[:6]],
    ],
    "an axis with no weight": [ActiveAxis("bpm", strength=1.0), ActiveAxis("genre", strength=0.0)],
    "no axes at all": [],
}
CASES = {name: [a for a in axes if a.key in BY_KEY] for name, axes in CASES.items()}


@pytest.mark.parametrize("case", sorted(CASES))
@pytest.mark.parametrize("source", SOURCES)
def test_ranking_against_a_track(library, source: str, case: str) -> None:
    src = _source(library, source)
    places = 1e-4 if case in ("similarity", "everything") else 1e-9

    _same(
        matcher.rank_matches_axes(src, CASES[case]),
        reference.rank_matches_axes(library, src, CASES[case]),
        places=places,
    )


@pytest.mark.parametrize(
    ("window", "harmonic", "override"),
    [
        (8.0, False, None),
        (3.0, False, None),
        (50.0, False, None),
        (0.0, True, None),
        (8.0, True, None),
        (8.0, False, 131.0),
        (8.0, True, 64.0),
    ],
)
@pytest.mark.parametrize("case", ["tempo and key", "genre", "similarity", "everything"])
def test_ranking_inside_a_tempo_window(
    library, case: str, window: float, harmonic: bool, override
) -> None:
    """The window decides who is ranked at all — and with it, what counts as
    a rare genre."""
    src = _source(library, "/m/0007.mp3")
    settings = dict(max_bpm_pct=window, harmonic_only=harmonic, src_bpm_override=override)
    places = 1e-4 if case in ("similarity", "everything") else 1e-9

    _same(
        matcher.rank_matches_axes(src, CASES[case], **settings),
        reference.rank_matches_axes(library, src, CASES[case], **settings),
        places=places,
    )


def test_a_source_with_nothing_known_about_it(library) -> None:
    with library() as c:
        c.execute("INSERT INTO tracks (path, filename) VALUES ('/m/new.mp3', 'new.mp3')")
        c.commit()
    src = _source(library, "/m/new.mp3")
    try:
        _same(
            matcher.rank_matches_axes(src, CASES["everything"]),
            reference.rank_matches_axes(library, src, CASES["everything"]),
            places=1e-4,
        )
    finally:
        with library() as c:
            c.execute("DELETE FROM tracks WHERE path = '/m/new.mp3'")
            c.commit()


# The fingerprint axis is parked out of the release, so no ranking above
# reaches the similarity that is scaled against the set being ranked. It is
# still in the tree and will come back, so it is held to the same standard
# here, against the functions the row-by-row ranker called.


@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize("window", [0.0, 8.0])
def test_similarity_scaled_against_the_set_being_ranked(
    library, source: str, window: float
) -> None:
    model = next(m for m in EMBEDDING_MODELS.values() if m.storage == "derived")
    src = _source(library, source)
    snap = library_snapshot.snapshot(library)
    chosen = ranking._candidates(snap, src["path"], src["bpm"] or 0, window)
    assert len(chosen) > 20

    score, has = ranking._derived_similarity(snap, model, src, chosen)

    rows = [src, *(snap.rows[i] for i in chosen)]
    vectors = load_vectors(model, rows)
    want = score_similarity_bulk(model, vectors.get(src["path"]), vectors)
    want.pop(src["path"], None)
    got = {snap.paths[i]: float(score[i]) for i in chosen if has[i]}
    assert set(got) == set(want)
    for path, value in got.items():
        assert value == pytest.approx(want[path], abs=1e-4), path
    if src["path"] in vectors:
        assert len(got) > 20, "a comparison of nothing proves nothing"


@pytest.mark.parametrize("case", ["rating", "one scalar", "every scalar"])
def test_ranking_with_nothing_playing(library, case: str) -> None:
    _same(
        matcher.rank_matches_absolute(CASES[case]),
        reference.rank_matches_absolute(library, CASES[case]),
    )


def test_nothing_to_rank_on_is_an_empty_list(library) -> None:
    assert matcher.rank_matches_absolute(CASES["tempo and key"]) == []


# ── the library is read once, and again when it changes ─────────────────────


def test_a_second_ranking_does_not_read_the_library_again(library) -> None:
    src = _source(library, "/m/0000.mp3")
    matcher.rank_matches_axes(src, CASES["tempo and key"])
    first = library_snapshot.snapshot(library)

    matcher.rank_matches_axes(src, CASES["genre"])

    assert library_snapshot.snapshot(library) is first


def test_a_change_to_the_library_is_seen(library) -> None:
    src = _source(library, "/m/0000.mp3")
    before = matcher.rank_matches_axes(src, CASES["tempo and key"])
    with library() as c:
        c.execute(
            "INSERT INTO tracks (path, filename, bpm, camelot) "
            "VALUES ('/m/added.mp3', 'added.mp3', ?, ?)",
            (src["bpm"] or 124.0, src["camelot"] or "8A"),
        )
        c.commit()
    try:
        after = matcher.rank_matches_axes(src, CASES["tempo and key"])
        assert len(after) == len(before) + 1
        assert "/m/added.mp3" in {r["row"]["path"] for r in after}
    finally:
        with library() as c:
            c.execute("DELETE FROM tracks WHERE path = '/m/added.mp3'")
            c.commit()


def test_a_changed_value_is_seen(library) -> None:
    src = _source(library, "/m/0000.mp3")
    with library() as c:
        was = c.execute("SELECT rating FROM tracks WHERE path = '/m/0001.mp3'").fetchone()[0]
    matcher.rank_matches_axes(src, CASES["rating"])
    with library() as c:
        c.execute("UPDATE tracks SET rating = 5 WHERE path = '/m/0001.mp3'")
        c.commit()
    try:
        ranked = matcher.rank_matches_axes(src, CASES["rating"])
        [hit] = [r for r in ranked if r["row"]["path"] == "/m/0001.mp3"]
        assert hit["row"]["rating"] == 5 and hit["rating_score"] == 100.0
    finally:
        with library() as c:
            c.execute("UPDATE tracks SET rating = ? WHERE path = '/m/0001.mp3'", (was,))
            c.commit()


# ── the result still reads as a list ────────────────────────────────────────


def test_a_ranking_reads_as_a_list(library) -> None:
    src = _source(library, "/m/0000.mp3")
    ranked = matcher.rank_matches_axes(src, CASES["tempo and key"])

    assert len(ranked) > 50 and bool(ranked)
    assert ranked[0] is ranked[0], "the same position is the same object"
    assert ranked[-1] is ranked[len(ranked) - 1]
    assert ranked[:3] == [ranked[0], ranked[1], ranked[2]]
    assert [r["row"]["path"] for r in ranked][:3] == [r["row"]["path"] for r in ranked[:3]]
    assert ranked == list(ranked)
    with pytest.raises(IndexError):
        ranked[len(ranked)]
