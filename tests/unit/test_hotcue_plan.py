"""Where detected hotcues go on a track: the collection.nml writer's rules, for live writes."""

from __future__ import annotations

from core.cue_points import Cue
from core.hotcue_plan import HotcuePlan, plan_hotcues

GRID = Cue(4, -1, 100.0, "AutoGrid")
AUTOCUE = Cue(0, 0, 100.0, "AutoGrid")
DROP = (60_000.0, "tk: Drop", 0)
BREAK = (90_000.0, "tk: Break", 2)


def test_a_fresh_track_gets_the_cues_in_slots_one_on() -> None:
    plan = plan_hotcues([GRID, AUTOCUE], [DROP, BREAK], 120.0, force=False)
    assert plan == HotcuePlan(False, ((1, 60_000.0, "tk: Drop", 0), (2, 90_000.0, "tk: Break", 2)))


def test_the_users_own_hotcues_are_left_alone_unless_forced() -> None:
    mine = Cue(0, 3, 30_000.0, "my drop")
    assert plan_hotcues([GRID, mine], [DROP], 120.0, force=False) is None
    forced = plan_hotcues([GRID, mine], [DROP], 120.0, force=True)
    assert forced is not None and forced.add == ((1, 60_000.0, "tk: Drop", 0),)


def test_tks_earlier_cues_are_replaced() -> None:
    old = Cue(0, 1, 10_000.0, "tk: Drop")
    plan = plan_hotcues([GRID, old], [DROP], 120.0, force=False)
    assert plan == HotcuePlan(True, ((1, 60_000.0, "tk: Drop", 0),))


def test_a_cue_within_a_beat_of_one_that_stays_is_dropped() -> None:
    mine = Cue(0, 5, 60_200.0, "mine")  # 200 ms from DROP; a beat at 120 BPM is 500 ms
    plan = plan_hotcues([GRID, mine], [DROP, BREAK], 120.0, force=True)
    assert plan is not None and [a[2] for a in plan.add] == ["tk: Break"]


def test_grid_markers_never_block_a_cue_and_slots_run_out_at_seven() -> None:
    near_grid = (100.0, "tk: Drop", 0)
    many = [near_grid] + [(10_000.0 * i, "tk: Drop", 0) for i in range(1, 9)]
    plan = plan_hotcues([GRID], many, 120.0, force=False)
    assert plan is not None and [a[0] for a in plan.add] == [1, 2, 3, 4, 5, 6, 7]
    assert plan.add[0][1] == 100.0
