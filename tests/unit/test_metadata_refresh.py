"""Refreshing the library's metadata from Traktor's collection.

Real failure: a scan that read ``collection.nml`` while Traktor was rewriting
it got a parse error, which came back as an empty collection. Every track then
read as having no artist, no title, no genre and no rating, all of which
differed from what the library held — so the refresh wrote the blanks over
every analysed track.

The rule that prevents it: the collection can only change a track it lists.
"""

from __future__ import annotations

import logging

import pytest

import core.analysis as analysis
from core.metadata_refresh import plan_refresh
from core.traktor import parse_traktor_nml_metadata

_HELD = (
    "Kuss",
    "Artificial Serenity EP",
    "Artificial Serenity",
    "Techno",
    4,
    "2024/1/5",
    "2024/2/1",
    "Mord",
    320000,
    7,
)


@pytest.fixture
def collection(monkeypatch):
    """Stand in for the parsed collection; returns the dict to fill."""
    listed: dict[str, dict] = {}
    monkeypatch.setattr(analysis, "_load_traktor_collection", lambda: listed)
    analysis.reload_traktor_collection()
    yield listed
    analysis.reload_traktor_collection()


def _entry(**changes) -> dict:
    entry = {
        "artist": "Kuss",
        "album": "Artificial Serenity EP",
        "title": "Artificial Serenity",
        "genre": "Techno",
        "musical_key": 21,
        "traktor_bpm": 138.0,
        "rating": 4,
        "release_date": "2024/1/5",
        "import_date": "2024/2/1",
        "label": "Mord",
        "bitrate": 320000,
        "play_count": 7,
    }
    return {**entry, **changes}


# ── A collection that could not be read ─────────────────────────────────────


def test_a_collection_cut_short_reads_as_empty_and_says_so(tmp_path, caplog) -> None:
    nml = tmp_path / "collection.nml"
    nml.write_text('<?xml version="1.0"?><NML><COLLECTION><ENTRY ARTIST="Ku')

    with caplog.at_level(logging.WARNING):
        assert parse_traktor_nml_metadata(nml) == {}

    assert "collection.nml" in caplog.text


def test_a_collection_that_is_not_there_reads_as_empty(tmp_path, caplog) -> None:
    with caplog.at_level(logging.WARNING):
        assert parse_traktor_nml_metadata(tmp_path / "collection.nml") == {}

    assert "collection.nml" in caplog.text


def test_an_empty_collection_changes_nothing(collection) -> None:
    paths = ["/m/a.mp3", "/m/b.mp3"]

    plan = plan_refresh(paths, dict.fromkeys(paths, _HELD))

    assert plan.metadata == ()
    assert plan.keys == ()
    assert plan.tempos == ()
    assert plan.unlisted == 2


# ── A collection that could ─────────────────────────────────────────────────


def test_a_track_the_collection_does_not_list_is_left_alone(collection) -> None:
    collection["/m/a.mp3"] = _entry(title="Renamed")

    plan = plan_refresh(["/m/a.mp3", "/m/b.mp3"], {"/m/a.mp3": _HELD, "/m/b.mp3": _HELD})

    assert [row[-1] for row in plan.metadata] == ["/m/a.mp3"]
    assert plan.unlisted == 1


def test_a_changed_track_is_updated(collection) -> None:
    collection["/m/a.mp3"] = _entry(title="Renamed", rating=5)

    plan = plan_refresh(["/m/a.mp3"], {"/m/a.mp3": _HELD})

    assert plan.metadata == (
        (
            "Kuss",
            "Artificial Serenity EP",
            "Renamed",
            "Techno",
            5,
            "2024/1/5",
            "2024/2/1",
            "Mord",
            320000,
            7,
            "/m/a.mp3",
        ),
    )


def test_an_unchanged_track_is_not_written(collection) -> None:
    collection["/m/a.mp3"] = _entry()

    plan = plan_refresh(["/m/a.mp3"], {"/m/a.mp3": _HELD})

    assert plan.metadata == ()


def test_a_tag_cleared_in_traktor_is_cleared_in_the_library(collection) -> None:
    """Blanks are still written for a track the collection does list — that
    is the user emptying a field, not a collection that failed to load."""
    collection["/m/a.mp3"] = _entry(genre="")

    plan = plan_refresh(["/m/a.mp3"], {"/m/a.mp3": _HELD})

    assert plan.metadata[0][3] == ""


def test_key_and_tempo_come_from_the_collection(collection) -> None:
    collection["/m/a.mp3"] = _entry()

    plan = plan_refresh(["/m/a.mp3"], {"/m/a.mp3": _HELD})

    assert plan.keys == ((*analysis.traktor_key_to_fields(21), "/m/a.mp3"),)
    assert plan.tempos == ((138.0, "/m/a.mp3"),)


def test_a_track_without_key_or_tempo_keeps_the_ones_it_has(collection) -> None:
    collection["/m/a.mp3"] = _entry(musical_key=-1, traktor_bpm=0.0)

    plan = plan_refresh(["/m/a.mp3"], {"/m/a.mp3": _HELD})

    assert plan.keys == () and plan.tempos == ()


def test_a_stop_ends_the_pass(collection) -> None:
    for name in "abc":
        collection[f"/m/{name}.mp3"] = _entry(title="Renamed")
    seen: list[int] = []

    plan = plan_refresh(
        [f"/m/{n}.mp3" for n in "abc"], {}, should_stop=lambda: len(seen) >= 2, tick=seen.append
    )

    assert len(plan.metadata) == 2


def test_a_track_new_to_the_library_is_written(collection) -> None:
    collection["/m/a.mp3"] = _entry()

    plan = plan_refresh(["/m/a.mp3"], {})

    assert len(plan.metadata) == 1
