"""Replaying Traktor's play history against the matcher (core.history_replay)."""

from __future__ import annotations

from datetime import date
from os.path import normpath
from pathlib import Path

import pytest

from core.history_replay import Result, played_tracks, replay, transitions


def _entry(name: str, start: int, played: float) -> str:
    key = f"Macintosh HD/:m/:{name}.mp3"
    return (
        f'<ENTRY><PRIMARYKEY TYPE="TRACK" KEY="{key}"/>'
        f'<EXTENDEDDATA DECK="1" DURATION="{played}" STARTDATE="132778499" '
        f'STARTTIME="{start}" EXTENDEDTYPE="HistoryData"/></ENTRY>'
    )


def _history(folder: Path, name: str, entries: list[tuple[str, int, float]]) -> Path:
    body = "".join(_entry(*e) for e in entries)
    path = folder / name
    path.write_text(
        f'<NML VERSION="20"><PLAYLISTS><NODE TYPE="FOLDER"><SUBNODES><NODE TYPE="PLAYLIST">'
        f'<PLAYLIST ENTRIES="{len(entries)}" TYPE="LIST">{body}</PLAYLIST>'
        f"</NODE></SUBNODES></NODE></PLAYLISTS></NML>",
        encoding="utf-8",
    )
    return path


def test_plays_are_in_start_order_and_short_ones_are_dropped(tmp_path) -> None:
    f = _history(
        tmp_path,
        "history_2025y01m02d_20h00m00s.nml",
        [("b", 200, 300.0), ("a", 100, 300.0), ("audition", 150, 12.0)],
    )
    assert played_tracks(f) == [normpath("/m/a.mp3"), normpath("/m/b.mp3")]


def test_transitions_need_both_tracks_in_the_library_and_respect_the_date(tmp_path) -> None:
    _history(
        tmp_path,
        "history_2024y06m01d_20h00m00s.nml",
        [("a", 1, 300.0), ("b", 2, 300.0), ("gone", 3, 300.0), ("c", 4, 300.0)],
    )
    _history(tmp_path, "history_2025y06m01d_20h00m00s.nml", [("c", 1, 300.0), ("a", 2, 300.0)])
    library = {normpath("/m/a.mp3"), normpath("/m/b.mp3"), normpath("/m/c.mp3")}
    every = transitions(tmp_path.glob("*.nml"), library)
    assert [(t.played, t.next) for t in every] == [
        (normpath("/m/a.mp3"), normpath("/m/b.mp3")),
        (normpath("/m/c.mp3"), normpath("/m/a.mp3")),
    ]
    early = transitions(tmp_path.glob("*.nml"), library, before=date(2025, 1, 1))
    assert [(t.played, t.next) for t in early] == [(normpath("/m/a.mp3"), normpath("/m/b.mp3"))]


def test_metrics() -> None:
    r = Result("x", [0, 4, 9, None])
    assert r.recall(1) == 0.25
    assert r.recall(10) == 0.75
    assert r.mrr == pytest.approx((1 + 1 / 5 + 1 / 10) / 4)
    assert r.median_rank == 5


def test_replay_finds_where_the_next_track_ranked(tmp_path, monkeypatch) -> None:
    import core.db as db
    from core.history_replay import Transition
    from core.library_snapshot import snapshot

    monkeypatch.setattr(db, "_db_path", lambda: tmp_path / "t.db")
    db.init_db()
    for path, bpm in (
        (normpath("/m/a.mp3"), 130.0),
        (normpath("/m/b.mp3"), 130.5),
        (normpath("/m/c.mp3"), 90.0),
    ):
        db.upsert_track(path, path, bpm, 1, True, "8A", 300.0)
    snap = snapshot(db.get_conn)
    played = [
        Transition(normpath("/m/a.mp3"), normpath("/m/b.mp3"), "s"),
        Transition(normpath("/m/a.mp3"), normpath("/m/c.mp3"), "s"),
    ]
    result = replay(snap, played, "bpm", [("bpm", 1.0)])
    assert result.ranks == [0, 1]  # b is the closest tempo, c the furthest


def test_each_axis_is_tried_added_or_taken_out() -> None:
    from core.features import FEATURES
    from core.history_replay import configurations

    configs = dict(configurations([("bpm", 1.0), ("genre", 0.5), ("rating", 0.0)]))
    assert configs["Your setup"] == [("bpm", 1.0), ("genre", 0.5)]  # weight 0 is off
    genre = next(f.label for f in FEATURES if f.key == "genre")
    label = next(f.label for f in FEATURES if f.key == "label")
    assert configs[f"− {genre}"] == [("bpm", 1.0)]
    assert configs[f"+ {label}"] == [("bpm", 1.0), ("genre", 0.5), ("label", 1.0)]
    assert len(configs) == 1 + len(FEATURES)
