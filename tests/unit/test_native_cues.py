"""Hotcues written live into Traktor (integrated mode), through libtk's tk_cue_* calls."""

from __future__ import annotations

import unicodedata

from core.cue_points import Cue, TrackCues
from workers.native_cues import NativeCues, apply_detected, describe, remove_tk_cues


class FakeLib:
    """libtk's cue calls: knows tracks by the path spelling Traktor stores."""

    def __init__(self, stored: set[str]) -> None:
        self.stored = stored
        self.calls: list[tuple] = []
        lib = self

        class _Fn:
            def __init__(self, fn) -> None:
                self.fn, self.argtypes, self.restype = fn, None, None

            def __call__(self, *a):
                return self.fn(*a)

        self.tk_cue_write_path = _Fn(
            lambda p, slot, start, t, name: lib._write(p, slot, start, t, name)
        )
        self.tk_cue_remove_path = _Fn(lambda p, prefix: lib._remove(p, prefix))

    def _write(self, path: bytes, slot: int, start: float, t: int, name: bytes) -> int:
        if path.decode() not in self.stored:
            return -1
        self.calls.append(("write", path.decode(), slot, start, t, name.decode()))
        return 0

    def _remove(self, path: bytes, prefix: bytes) -> int:
        if path.decode() not in self.stored:
            return -1
        self.calls.append(("remove", path.decode(), prefix.decode()))
        return 2


GRID = Cue(4, -1, 10.0, "AutoGrid")


def test_detected_cues_go_in_by_the_nml_writers_rules() -> None:
    lib = FakeLib({"/m/a.mp3", "/m/b.mp3"})
    tracks = [
        TrackCues("/m/a.mp3", 120.0, (GRID, Cue(0, 1, 5000.0, "tk: Drop"))),  # ours: replaced
        TrackCues("/m/b.mp3", 120.0, (GRID, Cue(0, 2, 5000.0, "my cue"))),  # the user's: kept
    ]
    detected = {"/m/a.mp3": [(60000.0, "tk: Drop", 0)], "/m/b.mp3": [(60000.0, "tk: Drop", 0)]}
    changed = apply_detected(NativeCues(lib), detected, tracks, force=False)
    assert changed == [("/m/a.mp3", [(1, 60000.0, "tk: Drop")])]  # what the page lists
    assert describe(changed) == "a\n    2 1:00 Drop"  # Traktor's hotcue 2 = slot 1
    assert lib.calls == [
        ("remove", "/m/a.mp3", "tk: "),
        ("write", "/m/a.mp3", 1, 60000.0, 0, "tk: Drop"),
    ]


def test_a_path_traktor_stores_decomposed_is_still_found() -> None:
    nfc = unicodedata.normalize("NFC", "/m/Café.mp3")
    lib = FakeLib({unicodedata.normalize("NFD", nfc)})
    assert NativeCues(lib).write(nfc, 1, 1000.0, "tk: Drop", 0) is True


def test_tks_cues_are_removed_from_the_tracks_that_have_them() -> None:
    lib = FakeLib({"/m/a.mp3", "/m/b.mp3"})
    tracks = [
        TrackCues("/m/a.mp3", 120.0, (GRID, Cue(0, 1, 5000.0, "tk: Drop"))),
        TrackCues("/m/b.mp3", 120.0, (GRID, Cue(0, 2, 5000.0, "my cue"))),
    ]
    assert remove_tk_cues(NativeCues(lib), tracks) == (1, 2)
    assert lib.calls == [("remove", "/m/a.mp3", "tk: ")]


def test_outside_traktor_there_is_no_cue_writer() -> None:
    assert NativeCues.attach() is None
