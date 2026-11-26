"""Evaluating Traktor smart playlists.

A ``SMARTLIST`` stores no tracks — only a ``SEARCH_EXPRESSION`` whose ``QUERY``
Traktor evaluates live. Exporting one therefore means running the query
ourselves against the collection.

The queries in a real collection look like::

    $FILEPATH % "(Hri)"      $GENRE == ""      $RATING < 1
    $RATING == 1             $PLAYED == TRUE

The rule this module is built around: **a query we cannot evaluate exactly
must fail, not approximate.** A smart playlist quietly exported with the wrong
tracks is worse than one that is missing, because it is only discovered in
front of an audience.
"""

from __future__ import annotations

import pytest

from core.rekordbox import Track
from core.smartlist import UnsupportedQuery, evaluate, matches, parse_query


def _t(**kw) -> Track:
    base = {
        "path": "/m/a.mp3",
        "title": "T",
        "artist": "A",
        "genre": "House",
        "bpm": 128.0,
        "rating": 0,
    }
    return Track(**{**base, **kw})


# ── The queries a real collection actually contains ─────────────────────────


def test_filepath_contains() -> None:
    q = parse_query('$FILEPATH % "(Hri)"')
    assert matches(q, _t(path="/m/Track (Hri).mp3"))
    assert not matches(q, _t(path="/m/Track.mp3"))


def test_contains_ignores_case() -> None:
    q = parse_query('$FILEPATH % "ingrid"')
    assert matches(q, _t(path="/m/INGRID - Thing.mp3"))


def test_empty_string_equality() -> None:
    q = parse_query('$GENRE == ""')
    assert matches(q, _t(genre=""))
    assert not matches(q, _t(genre="Techno"))


def test_rating_is_compared_in_stars() -> None:
    """Traktor stores RANKING as 0/51/102/153/204/255; the query means stars."""
    q = parse_query("$RATING < 1")
    assert matches(q, _t(rating=0))
    assert not matches(q, _t(rating=51)), "one star is not below one star"

    one = parse_query("$RATING == 1")
    assert matches(one, _t(rating=51))
    assert not matches(one, _t(rating=102))


def test_played_is_rejected_rather_than_guessed() -> None:
    """Session state we do not have. Exporting it as 'nothing matched' would
    silently produce an empty playlist."""
    with pytest.raises(UnsupportedQuery):
        parse_query("$PLAYED == TRUE")


# ── Fields and operators ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("query", "field", "value", "hit"),
    [
        ('$ARTIST == "A"', "artist", "A", True),
        ('$ARTIST == "B"', "artist", "A", False),
        ('$ARTIST != "B"', "artist", "A", True),
        ('$TITLE % "or"', "title", "Storm", True),
        ('$ALBUM == "LP"', "album", "LP", True),
        ('$COMMENT % "vinyl"', "comment", "from vinyl", True),
        ('$LABEL == "Ostgut"', "label", "Ostgut", True),
    ],
)
def test_string_fields(query, field, value, hit) -> None:
    assert matches(parse_query(query), _t(**{field: value})) is hit


@pytest.mark.parametrize(
    ("query", "bpm", "hit"),
    [
        ("$BPM > 120", 128.0, True),
        ("$BPM > 130", 128.0, False),
        ("$BPM < 130", 128.0, True),
        ("$BPM >= 128", 128.0, True),
        ("$BPM <= 127", 128.0, False),
        ("$BPM == 128", 128.0, True),
    ],
)
def test_numeric_comparisons(query, bpm, hit) -> None:
    assert matches(parse_query(query), _t(bpm=bpm)) is hit


def test_unknown_field_is_rejected() -> None:
    with pytest.raises(UnsupportedQuery):
        parse_query('$NONSENSE == "x"')


def test_malformed_query_is_rejected() -> None:
    for bad in ("", "$ARTIST", "ARTIST == 1", '$ARTIST ~~ "x"', "$BPM > "):
        with pytest.raises(UnsupportedQuery):
            parse_query(bad)


def test_a_string_field_compared_as_a_number_is_rejected() -> None:
    with pytest.raises(UnsupportedQuery):
        parse_query("$ARTIST > 5")


# ── Combining ───────────────────────────────────────────────────────────────


def test_and() -> None:
    q = parse_query('$BPM > 120 AND $GENRE == "House"')
    assert matches(q, _t(bpm=128.0, genre="House"))
    assert not matches(q, _t(bpm=110.0, genre="House"))
    assert not matches(q, _t(bpm=128.0, genre="Techno"))


def test_or() -> None:
    q = parse_query('$GENRE == "House" OR $GENRE == "Techno"')
    assert matches(q, _t(genre="Techno"))
    assert not matches(q, _t(genre="Ambient"))


def test_not() -> None:
    q = parse_query('NOT $GENRE == "House"')
    assert matches(q, _t(genre="Techno"))
    assert not matches(q, _t(genre="House"))


def test_parentheses_group() -> None:
    q = parse_query('($GENRE == "House" OR $GENRE == "Techno") AND $BPM > 125')
    assert matches(q, _t(genre="Techno", bpm=128.0))
    assert not matches(q, _t(genre="Techno", bpm=120.0))
    assert not matches(q, _t(genre="Ambient", bpm=128.0))


def test_and_binds_tighter_than_or() -> None:
    q = parse_query('$GENRE == "Ambient" OR $GENRE == "House" AND $BPM > 125')
    assert matches(q, _t(genre="Ambient", bpm=90.0)), "the OR branch alone"
    assert matches(q, _t(genre="House", bpm=128.0))
    assert not matches(q, _t(genre="House", bpm=120.0))


# ── evaluate() over a collection ────────────────────────────────────────────


def test_evaluate_keeps_collection_order() -> None:
    tracks = [_t(path=f"/m/{i}.mp3", bpm=b) for i, b in enumerate([100.0, 130.0, 140.0, 120.0])]
    got = evaluate("$BPM > 125", tracks)
    assert [t.path for t in got] == ["/m/1.mp3", "/m/2.mp3"]


def test_evaluate_can_match_nothing() -> None:
    assert evaluate("$BPM > 300", [_t()]) == []


def test_evaluate_propagates_unsupported() -> None:
    with pytest.raises(UnsupportedQuery):
        evaluate("$PLAYED == TRUE", [_t()])
