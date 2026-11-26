"""Auto-pick: within each cluster of correlated dimensions, keep the most
discriminative one and drop the rest.

Pulled out of the button so the slider can show what it *would* do while
you drag it — at a high threshold nothing correlates, and a button that
silently does nothing reads as broken.
"""

from __future__ import annotations

from ui.stats import auto_pick_plan


def _matrix(pairs: dict, keys: list[str]) -> list[list[float]]:
    n = len(keys)
    m = [[0.0] * n for _ in range(n)]
    for i in range(n):
        m[i][i] = 1.0
    for (a, b), r in pairs.items():
        i, j = keys.index(a), keys.index(b)
        m[i][j] = m[j][i] = r
    return m


def test_the_most_discriminative_of_a_pair_survives() -> None:
    keys = ["loud", "bass"]
    plan = auto_pick_plan(
        keys,
        _matrix({("loud", "bass"): 0.95}, keys),
        spread={"loud": 30.0, "bass": 80.0},
        locked=set(),
        threshold=0.9,
    )
    assert plan.keep == ["bass"]
    assert plan.drop == ["loud"]


def test_a_chain_is_one_cluster() -> None:
    """A↔B↔C with no direct A↔C still leaves only one survivor."""
    keys = ["a", "b", "c"]
    plan = auto_pick_plan(
        keys,
        _matrix({("a", "b"): 0.95, ("b", "c"): 0.95}, keys),
        spread={"a": 10.0, "b": 50.0, "c": 20.0},
        locked=set(),
        threshold=0.9,
    )
    assert plan.keep == ["b"]
    assert sorted(plan.drop) == ["a", "c"]


def test_uncorrelated_features_are_all_kept() -> None:
    keys = ["a", "b"]
    plan = auto_pick_plan(
        keys,
        _matrix({("a", "b"): 0.2}, keys),
        spread={"a": 10.0, "b": 50.0},
        locked=set(),
        threshold=0.9,
    )
    assert plan.drop == []
    assert plan.keep == []  # nothing to decide — no cluster


def test_a_locked_feature_is_never_dropped() -> None:
    keys = ["bpm", "bass"]
    plan = auto_pick_plan(
        keys,
        _matrix({("bpm", "bass"): 0.99}, keys),
        spread={"bpm": 10.0, "bass": 90.0},
        locked={"bpm"},
        threshold=0.9,
    )
    assert "bpm" not in plan.drop


def test_a_locked_feature_keeps_its_cluster_mate_out() -> None:
    """If the core dimension is staying, the correlated one is redundant."""
    keys = ["bpm", "bass"]
    plan = auto_pick_plan(
        keys,
        _matrix({("bpm", "bass"): 0.99}, keys),
        spread={"bpm": 10.0, "bass": 90.0},
        locked={"bpm"},
        threshold=0.9,
    )
    assert plan.drop == ["bass"]


def test_negative_correlation_counts_too() -> None:
    keys = ["a", "b"]
    plan = auto_pick_plan(
        keys,
        _matrix({("a", "b"): -0.97}, keys),
        spread={"a": 10.0, "b": 50.0},
        locked=set(),
        threshold=0.9,
    )
    assert plan.drop == ["a"]


def test_a_threshold_nothing_reaches_changes_nothing() -> None:
    """The reported bug: at 0.99 no pair correlates, so the button did
    nothing and looked broken."""
    keys = ["a", "b"]
    plan = auto_pick_plan(
        keys,
        _matrix({("a", "b"): 0.95}, keys),
        spread={"a": 10.0, "b": 50.0},
        locked=set(),
        threshold=0.99,
    )
    assert plan.drop == []
    assert plan.is_empty


def test_a_plan_that_drops_something_is_not_empty() -> None:
    keys = ["a", "b"]
    plan = auto_pick_plan(
        keys,
        _matrix({("a", "b"): 0.95}, keys),
        spread={"a": 10.0, "b": 50.0},
        locked=set(),
        threshold=0.9,
    )
    assert not plan.is_empty


def test_too_few_features_is_not_an_error() -> None:
    assert auto_pick_plan([], [], spread={}, locked=set(), threshold=0.9).is_empty
