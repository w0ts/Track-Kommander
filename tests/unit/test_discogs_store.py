"""Discogs lookups in our database, and the lookup that produces them."""

from __future__ import annotations

import os

import pytest

import core.db as db
from core import discogs_store
from core.discogs_api import DiscogsError, DiscogsStopped
from core.discogs_fields import DiscogsRecord
from core.discogs_lookup import accept, lookup
from core.discogs_match import Query
from core.discogs_store import Candidate, Lookup, record_fields


@pytest.fixture()
def fresh_db(tmp_path, monkeypatch):
    dbfile = tmp_path / "t.db"
    monkeypatch.setattr(db, "_db_path", lambda: dbfile)
    db.init_db()
    return dbfile


def _track(path: str, artist: str = "Aero", title: str = "Come Again") -> None:
    db.upsert_track(path, path.rsplit("/", 1)[-1], 130.0, 1, True, "8A", 400.0)
    db.update_track_metadata(path, artist, "", title, "")


def _matched(path: str) -> Lookup:
    rec = DiscogsRecord(7, ("Electronic",), ("Techno",), "PRR", "PRR 1", "UK", "2024-11-15", 2024)
    return Lookup(path, "matched", 0.95, record_fields(rec, position="A2"))


# ── store ─────────────────────────────────────────────────────────────────────


def test_a_match_round_trips(fresh_db) -> None:
    discogs_store.save(_matched("/m/a.mp3"))
    got = discogs_store.get("/m/a.mp3")
    assert got is not None and got.status == "matched" and got.record is not None
    assert got.record["position"] == "A2"
    record = discogs_store.matched_records()["/m/a.mp3"]
    assert record.styles == ("Techno",) and record.label == "PRR" and record.year == 2024


def test_tracks_with_an_answer_are_not_looked_up_again(fresh_db) -> None:
    for p in ("/m/a.mp3", "/m/b.mp3", "/m/c.mp3", "/m/d.mp3"):
        _track(p)
    discogs_store.save(_matched("/m/a.mp3"))
    discogs_store.save(Lookup("/m/b.mp3", "none"))
    discogs_store.save(Lookup("/m/c.mp3", "error", error="HTTP 500"))
    assert sorted(p for p, _q in discogs_store.pending()) == ["/m/c.mp3", "/m/d.mp3"]


def test_tracks_without_artist_or_title_are_not_looked_up(fresh_db) -> None:
    _track("/m/a.mp3", artist="")
    assert discogs_store.pending() == []


def test_skipping_keeps_the_candidates_and_drops_the_match(fresh_db) -> None:
    cand = Candidate(1, "Aero - EP", 2024, "PRR", "PRR 1", "Vinyl", 0.6)
    discogs_store.save(Lookup("/m/a.mp3", "review", 0.6, None, (cand,)))
    discogs_store.skip("/m/a.mp3")
    got = discogs_store.get("/m/a.mp3")
    assert got is not None and got.status == "skipped" and got.candidates == (cand,)
    assert discogs_store.counts() == {"skipped": 1}


def test_removing_a_track_removes_its_lookup(fresh_db) -> None:
    _track("/m/a.mp3")
    discogs_store.save(_matched("/m/a.mp3"))
    db.remove_track("/m/a.mp3")
    assert discogs_store.get("/m/a.mp3") is None


# ── lookup ───────────────────────────────────────────────────────────────────


class FakeClient:
    def __init__(self, results, releases, fail=None):
        self.results, self.releases, self.fail = results, releases, fail
        self.calls = []

    def search(self, **params):
        self.calls.append(("search", params))
        if self.fail:
            raise self.fail
        return self.results if "artist" in params else []

    def release(self, release_id):
        self.calls.append(("release", release_id))
        return self.releases[release_id]


_RELEASE = {
    "id": 7,
    "title": "Straight From The Shoulder EP",
    "artists": [{"name": "Aero"}],
    "genres": ["Electronic"],
    "styles": ["Techno"],
    "labels": [{"name": "Planet Rhythm Records (2)", "catno": "PRRUK 108"}],
    "country": "UK",
    "released": "2024-11-15",
    "year": 2024,
    "master_id": 9,
    "tracklist": [{"type_": "track", "position": "A2", "title": "Come Again", "duration": "6:43"}],
}
_RESULTS = [
    {"id": 7, "title": "Aero - Straight From The Shoulder EP", "master_id": 9, "year": "2024"},
    {"id": 8, "title": "Aero - Straight From The Shoulder EP", "master_id": 9, "year": "2023"},
]


def test_a_sure_match_is_kept_with_its_fields() -> None:
    client = FakeClient(_RESULTS, {7: _RELEASE})
    query = Query("Aero", "Come Again (Original Mix)", seconds=402)
    got = lookup(client, "/m/a.mp3", query, exact=True)
    assert got.status == "matched"
    assert got.record is not None
    assert got.record["label"] == "Planet Rhythm Records"  # Discogs' "(2)" dropped
    assert got.record["position"] == "A2"
    assert got.record["original_year"] == 2023  # the earliest on the same master
    assert ("search", {"artist": "Aero", "track": "Come Again"}) in client.calls
    assert [c for c in client.calls if c[0] == "release"] == [("release", 7)]


def test_nothing_found_is_none() -> None:
    client = FakeClient([], {})
    assert lookup(client, "/m/a.mp3", Query("Nobody", "Nothing")).status == "none"
    assert len(client.calls) == 2  # the field search, then the free-text one


def test_a_doubtful_match_waits_for_review() -> None:
    wrong = {**_RELEASE, "tracklist": [{"type_": "track", "position": "1", "title": "Else"}]}
    client = FakeClient(_RESULTS, {7: wrong, 8: wrong})
    # exact: the tracklists are read (a conclusive search alone matches at once)
    got = lookup(client, "/m/a.mp3", Query("Aero", "Come Again"), exact=True)
    assert got.status == "review" and got.record is None
    assert [c.release_id for c in got.candidates] == [7, 8]


def test_a_failed_request_is_an_error_lookup_but_stop_is_not() -> None:
    client = FakeClient(_RESULTS, {}, fail=DiscogsError("HTTP 503", 503))
    got = lookup(client, "/m/a.mp3", Query("Aero", "Come Again"))
    assert got.status == "error" and got.error == "HTTP 503"
    with pytest.raises(DiscogsStopped):
        lookup(FakeClient([], {}, fail=DiscogsStopped("stopped")), "/m/a.mp3", Query("A", "B"))


def test_a_release_picked_in_review_becomes_the_match() -> None:
    client = FakeClient([], {7: _RELEASE})
    cand = Candidate(7, "Aero - EP", 2024, "PRR", "PRRUK 108", "Vinyl", 0.6)
    got = accept(client, "/m/a.mp3", Query("Aero", "Come Again"), cand)
    assert got.status == "matched" and got.record is not None and got.record["release_id"] == 7


def test_a_release_that_is_gone_settles_as_none() -> None:
    class Gone(FakeClient):
        def release(self, release_id):
            raise DiscogsError("not found", 404)

    got = lookup(Gone(_RESULTS, {}), "/m/a.mp3", Query("Aero", "Come Again"), exact=True)
    assert got.status == "none" and got.error == "not found"


def test_earlier_failures_are_retried_after_fresh_tracks(fresh_db) -> None:
    _track("/m/a.mp3", artist="Aaa")
    _track("/m/b.mp3", artist="Bbb")
    discogs_store.save(Lookup("/m/a.mp3", "error", error="HTTP 500"))
    assert [p for p, _q in discogs_store.pending()] == ["/m/b.mp3", "/m/a.mp3"]


def test_one_unexpected_failure_does_not_stop_the_run(fresh_db, monkeypatch) -> None:
    from workers import discogs_worker

    _track("/m/a.mp3", artist="Aaa")
    _track("/m/b.mp3", artist="Bbb")

    def fake_lookup(client, path, query, exact=False, region=""):
        if path == "/m/a.mp3":
            raise AttributeError("'str' object has no attribute 'get'")
        return Lookup(path, "none")

    monkeypatch.setattr(discogs_worker, "lookup", fake_lookup)
    monkeypatch.setattr(discogs_worker, "spotify_key_set", lambda: False)
    worker = discogs_worker.DiscogsWorker(discogs_store.pending())
    worker._run()
    assert worker.outcome == {"matched": 0, "review": 0, "none": 1, "error": 1}
    failed = discogs_store.get("/m/a.mp3")
    assert failed is not None and failed.status == "error" and "unexpected" in failed.error


_SEARCH_ONLY = [
    {
        "id": 7,
        "title": "Aero - Straight From The Shoulder EP",
        "master_id": 9,
        "year": "2024",
        "genre": ["Electronic"],
        "style": ["Techno"],
        "label": ["Planet Rhythm Records (2)", "Distributor"],
        "catno": "PRRUK 108",
        "country": "UK",
        "format": ["Vinyl", '12"'],
    },
    {"id": 8, "title": "Aero - Straight From The Shoulder EP", "master_id": 9, "year": "2023"},
]


def test_a_conclusive_search_needs_no_second_request() -> None:
    client = FakeClient(_SEARCH_ONLY, {})
    got = lookup(client, "/m/a.mp3", Query("Aero", "Come Again"))
    assert got.status == "matched" and got.record is not None
    assert [c[0] for c in client.calls] == ["search"]
    record = got.record
    assert record["styles"] == ["Techno"] and record["label"] == "Planet Rhythm Records"
    assert record["year"] == 2024 and record["original_year"] == 2023
    assert record["released"] == "2024" and record["verified"] is False


def test_two_different_records_by_the_artist_are_checked_on_their_tracklists() -> None:
    other = {**_SEARCH_ONLY[1], "master_id": 10, "id": 8}
    client = FakeClient([_SEARCH_ONLY[0], other], {7: _RELEASE, 8: _RELEASE})
    got = lookup(client, "/m/a.mp3", Query("Aero", "Come Again"))
    assert ("release", 7) in client.calls
    assert got.record is not None and got.record["verified"] is True


def test_exact_dates_always_fetch_the_release() -> None:
    client = FakeClient(_SEARCH_ONLY, {7: _RELEASE})
    got = lookup(client, "/m/a.mp3", Query("Aero", "Come Again"), exact=True)
    assert ("release", 7) in client.calls
    assert got.record is not None and got.record["released"] == "2024-11-15"


def test_look_up_again_spares_releases_picked_by_hand(fresh_db) -> None:
    for p in ("/m/a.mp3", "/m/b.mp3", "/m/c.mp3"):
        _track(p)
    discogs_store.save(_matched("/m/a.mp3"))
    picked = _matched("/m/b.mp3")
    discogs_store.save(
        Lookup(picked.path, "matched", 1.0, {**(picked.record or {}), "picked": True})
    )
    assert [p for p, _q in discogs_store.pending()] == ["/m/c.mp3"]
    assert sorted(p for p, _q in discogs_store.pending(again=True)) == ["/m/a.mp3", "/m/c.mp3"]


_COMPILATION = {
    "id": 32484069,
    "title": "Workout Sessions Vol. 2",
    "artists": [{"name": "Various"}],
    "genres": ["Electronic"],
    "styles": ["Techno"],
    "labels": [{"name": "Smile Sessions", "catno": "SMILE007"}],
    "year": 2024,
    "tracklist": [
        {"type_": "track", "position": "A1", "title": "Breeze", "artists": [{"name": "ANNĒ"}]},
        {"type_": "track", "position": "A2", "title": "Other", "artists": [{"name": "X"}]},
    ],
}


class AlbumClient(FakeClient):
    """Discogs' artist filter matches release artists: a track only on a
    compilation is found by the album, not by its own artist."""

    def search(self, **params):
        self.calls.append(("search", params))
        if "release_title" in params:
            return [{"id": 32484069, "title": "Various - Workout Sessions Vol. 2"}]
        if "artist" in params:
            return [{"id": 1, "title": "Anne Bisson - The Essentials"}]
        return []


def test_a_track_only_on_a_compilation_is_found_through_its_album() -> None:
    client = AlbumClient([], {32484069: _COMPILATION, 1: {"tracklist": []}})
    got = lookup(client, "/m/a.mp3", Query("ANNE", "Breeze", album="Workout Sessions Vol. 2"))
    assert (
        "search",
        {"release_title": "Workout Sessions Vol. 2", "track": "Breeze"},
    ) in client.calls
    assert got.status == "matched" and got.record is not None
    assert got.record["position"] == "A1" and got.record["catno"] == "SMILE007"


def test_no_album_search_when_the_artist_search_is_strong() -> None:
    client = FakeClient(_SEARCH_ONLY, {})
    lookup(client, "/m/a.mp3", Query("Aero", "Come Again", album="Some Album"))
    assert not any("release_title" in params for _kind, params in client.calls)


def test_the_tracklist_marks_where_the_track_is() -> None:
    from core.discogs_lookup import tracklist

    rows = tracklist(Query("ANNE", "Breeze"), _COMPILATION)
    assert [(r.position, r.artists, r.is_match) for r in rows] == [
        ("A1", "ANNĒ", True),
        ("A2", "X", False),
    ]


def test_the_search_alone_keeps_the_digital_pressing() -> None:
    pressings = [
        {**_SEARCH_ONLY[0], "id": 7, "format": ["Vinyl", '12"'], "catno": "PRR 108"},
        {**_SEARCH_ONLY[0], "id": 70, "format": ["CD"], "catno": "PRRCD 108"},
        {**_SEARCH_ONLY[0], "id": 71, "format": ["File", "WAV"], "catno": "PRRD 108"},
    ]
    got = lookup(FakeClient(pressings, {}), "/m/a.mp3", Query("Aero", "Come Again"))
    assert got.record is not None
    assert (got.record["release_id"], got.record["catno"]) == (71, "PRRD 108")


def test_the_search_alone_keeps_the_preferred_regions_release() -> None:
    pressings = [
        {**_SEARCH_ONLY[0], "id": 80, "format": ["CD"], "country": "Japan", "catno": "JP-1"},
        {**_SEARCH_ONLY[0], "id": 81, "format": ["CD"], "country": "UK & Europe", "catno": "EU-1"},
    ]
    eu = lookup(FakeClient(pressings, {}), "/m/a.mp3", Query("Aero", "Come Again"))
    jp = lookup(FakeClient(pressings, {}), "/m/a.mp3", Query("Aero", "Come Again"), region="japan")
    assert eu.record is not None and eu.record["catno"] == "EU-1"
    assert jp.record is not None and jp.record["catno"] == "JP-1"


def test_candidates_by_unrelated_artists_mean_not_on_discogs() -> None:
    """The search matched a word ("Sentimental"), not the record."""
    junk = [{"id": 5, "title": "Luis Miguel - Sentimental"}]
    client = FakeClient(junk, {5: {"tracklist": [{"type_": "track", "title": "Else"}]}})
    got = lookup(client, "/m/a.mp3", Query("Danny Wabbit", "Suave", album="Sentimental"))
    assert got.status == "none" and got.candidates == ()


def test_only_plausible_candidates_are_kept_for_review() -> None:
    results = [
        {"id": 1, "title": "Second Woman - S/W"},
        {"id": 2, "title": "Someone Else - S/W"},
    ]
    releases = {i: {"tracklist": [{"type_": "track", "title": "Other"}]} for i in (1, 2)}
    client = FakeClient(results, releases)
    got = lookup(client, "/m/a.mp3", Query("Second Woman", "Tune"), exact=True)
    assert got.status == "review"
    assert [c.release_id for c in got.candidates] == [1]


def test_stored_reviews_are_pruned_by_the_same_rule(fresh_db) -> None:
    from core.discogs_lookup import prune_reviews

    _track("/m/a.mp3", artist="Mall Grab", title="Foxes")
    _track("/m/b.mp3", artist="Second Woman", title="////")
    _track("/m/c.mp3", artist="Mall Grab", title="Foxes")
    db.update_track_metadata("/m/c.mp3", "Mall Grab", "Workout Sessions Vol. 2", "Foxes", "")

    def cand(i: int, title: str) -> Candidate:
        return Candidate(i, title, 2020, "L", "C", "File", 0.6)

    discogs_store.save(Lookup("/m/a.mp3", "review", 0.65, None, (cand(1, "Meret Ester - Foxes"),)))
    discogs_store.save(Lookup("/m/b.mp3", "review", 0.0, None, (cand(2, "Second Woman - S/W"),)))
    discogs_store.save(
        Lookup("/m/c.mp3", "review", 0.5, None, (cand(3, "Various - Workout Sessions Vol. 2"),))
    )
    assert prune_reviews() == 1
    statuses = {p: discogs_store.get(p).status for p in ("/m/a.mp3", "/m/b.mp3", "/m/c.mp3")}
    assert statuses == {"/m/a.mp3": "none", "/m/b.mp3": "review", "/m/c.mp3": "review"}


def test_tracks_added_while_the_last_one_runs_are_still_looked_up(fresh_db, monkeypatch) -> None:
    """Library ▸ Spotify adds tracks to a running lookup. An iterator over the
    list, once at its end, never saw them: they were dropped."""
    from workers import discogs_worker

    seen: list[str] = []
    worker = discogs_worker.DiscogsWorker([("/m/a.mp3", Query("A", "a"))])

    def fake_lookup(client, path, query, exact=False, region=""):
        seen.append(path)
        if path == "/m/a.mp3":  # the only track is in flight: add two, take one off
            worker.add([("/m/b.mp3", Query("B", "b")), ("/m/c.mp3", Query("C", "c"))])
            worker.skip({"/m/c.mp3"})
        return Lookup(path, "none")

    monkeypatch.setattr(discogs_worker, "lookup", fake_lookup)
    monkeypatch.setattr(discogs_worker, "spotify_key_set", lambda: False)
    worker._run()
    assert seen == ["/m/a.mp3", "/m/b.mp3"]
    assert worker.leftover() == []


def test_each_spotify_request_waits_for_the_workers_pacer(fresh_db, monkeypatch) -> None:
    """A Spotify lookup is 1 to 10 requests: each one is paced, not the track."""
    import types

    from core import spotify_store
    from core.discogs_api import RateLimiter
    from workers import discogs_worker

    asked: list[bool] = []

    def fake_lookup_track(artist, title, duration_s, *, before_request=None):
        assert before_request is not None
        asked.extend(before_request() for _ in range(3))  # search, track, album
        return types.SimpleNamespace(
            spotify_id="s1", artist=artist, title=title, album="Al", release_date="2017-09-01",
            track_number=2, duration_s=duration_s, label="Ninja Tune", isrc="", genres=(), sure=True,
        )  # fmt: skip

    monkeypatch.setitem(
        __import__("sys").modules,
        "core.spotify_api",
        types.SimpleNamespace(lookup_track=fake_lookup_track),
    )
    worker = discogs_worker.DiscogsWorker([])
    worker._spotify = RateLimiter(100)
    worker._ask_spotify("/m/a.mp3", Query("Bicep", "Glue", seconds=269.0))
    assert asked == [True, True, True]
    assert spotify_store.infos()[os.path.normpath("/m/a.mp3")].label == "Ninja Tune"
