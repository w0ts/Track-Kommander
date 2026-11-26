"""Inside Traktor the library follows the collection by itself, only while Traktor is idle."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from PySide6.QtWidgets import QApplication

from workers.auto_library import AutoLibrary


@pytest.fixture(scope="module")
def app() -> Iterator[QApplication]:
    yield QApplication.instance() or QApplication([])


class FakeWorker:
    def __init__(self, paths: list[str]) -> None:
        self.paths, self.running = paths, True

    def isRunning(self) -> bool:
        return self.running

    def stop(self) -> None:
        self.running = False


class Rig:
    def __init__(self, collection: list[str], analysed: set[str]) -> None:
        self.collection, self.analysed = collection, analysed
        self.playing = False
        self.pruned_with: list[list[str]] = []
        self.started: list[FakeWorker] = []
        self.lib = AutoLibrary(
            lambda: self.collection,
            lambda: self.analysed,
            lambda keep: self.pruned_with.append(list(keep)) or 0,
            lambda: self.playing,
            self._start,
            exists=lambda p: not p.endswith("gone.mp3"),
        )

    def _start(self, paths: list[str]) -> FakeWorker:
        worker = FakeWorker(paths)
        self.started.append(worker)
        return worker


def test_only_new_tracks_that_exist_are_analysed_and_the_library_follows(app) -> None:
    rig = Rig(["/a.mp3", "/b.mp3", "/gone.mp3"], {"/a.mp3"})
    rig.lib.check()
    assert rig.started[0].paths == ["/b.mp3"]
    assert rig.pruned_with == [["/a.mp3", "/b.mp3", "/gone.mp3"]]


def test_nothing_runs_while_a_deck_plays_and_a_start_stops_analysis(app) -> None:
    rig = Rig(["/a.mp3", "/b.mp3"], set())
    rig.playing = True
    rig.lib.check()
    assert rig.started == []
    rig.playing = False
    rig.lib.check()
    worker = rig.started[0]
    rig.lib.on_deck_state("A", True)
    assert not worker.running
    rig.lib.check()  # idle again: the same tracks are picked up
    assert rig.started[1].paths == ["/a.mp3", "/b.mp3"]


def test_an_empty_read_never_prunes_or_starts_anything(app) -> None:
    rig = Rig([], {"/a.mp3"})
    rig.lib.check()
    assert rig.pruned_with == [] and rig.started == []


def test_a_track_that_failed_is_not_retried_every_minute(app) -> None:
    rig = Rig(["/bad.mp3"], set())
    rig.lib.check()
    rig.started[0].running = False  # finished, but /bad.mp3 still not analysed
    rig.lib._on_finished()
    rig.lib.check()
    assert len(rig.started) == 1
