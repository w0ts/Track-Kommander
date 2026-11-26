"""With no source deck, a random selection in the list's own order, drawn once per launch (User,
2026-11-26: the same tracks showed at every start)."""

from __future__ import annotations

from core import browse_sample


def test_a_short_list_is_shown_whole() -> None:
    items = list(range(50))
    assert browse_sample.sample(items, ranked=False) == items


def test_a_long_list_gives_n_items_in_their_own_order() -> None:
    items = list(range(5000))
    picked = browse_sample.sample(items, ranked=False, n=200, seed=1)
    assert len(picked) == 200
    assert picked == sorted(picked)
    assert picked != items[:200]  # not simply the first ones


def test_the_same_launch_draws_the_same_selection() -> None:
    items = list(range(5000))
    assert browse_sample.sample(items, ranked=False) == browse_sample.sample(items, ranked=False)


def test_another_launch_draws_another_selection() -> None:
    items = list(range(5000))
    assert browse_sample.sample(items, ranked=False, seed=1) != browse_sample.sample(
        items, ranked=False, seed=2
    )


def test_a_ranked_list_is_drawn_from_its_best_only() -> None:
    items = list(range(5000))  # best first
    picked = browse_sample.sample(items, ranked=True, n=200, seed=3)
    assert len(picked) == 200 and max(picked) < browse_sample.RANKED_POOL
