"""Where Auto DJ starts the transition: at the track's last hotcue (User, 2026-11-24), else so that
it ends with the track, as before."""

from __future__ import annotations

import pytest

from core.auto_dj_timing import last_hotcue_s, mix_start_s

# (exists, type, start_pos s) per slot, as Traktor's deck properties give them.
DECK_A = [
    (True, 4, 0.08),  # grid marker: not a mix point
    (True, 2, 93.2),
    (True, 0, 100.4),
    (True, 2, 172.0),
    (True, 0, 186.3),
    (True, 0, 272.3),
    (False, -1, 329.6),  # an empty slot keeps a stale position
    (False, -1, 0.0),
]


def test_the_last_hotcue_is_the_latest_real_one_in_any_slot() -> None:
    assert last_hotcue_s(DECK_A) == 272.3
    assert last_hotcue_s(list(reversed(DECK_A))) == 272.3


def test_grid_and_load_markers_and_empty_slots_are_not_hotcues() -> None:
    assert last_hotcue_s([(True, 4, 0.1), (True, 3, 200.0), (False, 0, 300.0)]) is None
    assert last_hotcue_s([]) is None


def test_the_transition_starts_at_the_last_hotcue() -> None:
    assert mix_start_s(332.0, 272.3, transition_s=16.0) == 272.3


def test_without_a_hotcue_it_ends_with_the_track_as_before() -> None:
    assert mix_start_s(332.0, None, transition_s=16.0) == pytest.approx(316.0)


def test_a_last_hotcue_too_close_to_the_end_still_lets_the_fade_finish() -> None:
    assert mix_start_s(332.0, 325.0, transition_s=16.0) == pytest.approx(316.0)


def test_a_last_hotcue_in_the_first_half_is_no_outro() -> None:
    assert mix_start_s(332.0, 60.0, transition_s=16.0) == pytest.approx(316.0)
