"""Key has a spread too — it is categorical, not relational.

The Camelot wheel has 24 slots. "How much does key separate this crate"
is how evenly they are used: a library that is all 8A has nothing to match
on, one spread over the wheel has plenty. IQR is meaningless on a wheel,
so this is the normalised entropy of the distribution.
"""

from __future__ import annotations

import pytest

from core.stats import key_spread


def test_a_single_key_has_no_spread() -> None:
    assert key_spread(["8A"] * 100) == pytest.approx(0.0, abs=0.5)


def test_every_slot_used_equally_is_full_spread() -> None:
    wheel = [f"{n}{ab}" for n in range(1, 13) for ab in ("A", "B")]
    assert key_spread(wheel * 5) == pytest.approx(100.0, abs=0.5)


def test_a_real_looking_library_lands_between() -> None:
    """Most crates favour a handful of keys without collapsing to one."""
    values = ["7A"] * 88 + ["7B"] * 66 + ["8A"] * 66 + ["6A"] * 65 + ["3A"] * 48
    score = key_spread(values)
    assert 40 < score < 95


def test_two_keys_beat_one() -> None:
    assert key_spread(["8A"] * 50 + ["9A"] * 50) > key_spread(["8A"] * 100)


def test_missing_keys_are_ignored_not_counted() -> None:
    assert key_spread(["8A", None, "", "9A"]) == key_spread(["8A", "9A"])


def test_an_empty_library_is_not_an_error() -> None:
    assert key_spread([]) == 0.0
    assert key_spread([None, None]) == 0.0
