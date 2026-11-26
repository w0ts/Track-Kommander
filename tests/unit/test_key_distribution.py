"""How a library sits on the Camelot wheel.

Numbers alone would be meaningless — 12A and 1A are neighbours, not ten
apart — so the distribution is reported slot by slot, in wheel order, with
minor and major kept apart the way a DJ reads them.
"""

from __future__ import annotations

from core.stats import key_distribution


def test_every_slot_appears_even_when_empty() -> None:
    dist = key_distribution(["8A"] * 3)
    assert len(dist) == 24
    assert all(count == 0 for slot, count in dist if slot != "8A")
    assert dict(dist)["8A"] == 3


def test_minor_comes_first_in_wheel_order() -> None:
    slots = [slot for slot, _ in key_distribution([])]
    assert slots[:12] == [f"{n}A" for n in range(1, 13)]
    assert slots[12:] == [f"{n}B" for n in range(1, 13)]


def test_counts_are_tallied() -> None:
    dist = dict(key_distribution(["8A", "8A", "5B", None, "", "junk"]))
    assert dist["8A"] == 2
    assert dist["5B"] == 1


def test_an_unrecognised_key_is_ignored() -> None:
    """Traktor writes a key for everything it has analysed; anything else is
    not a Camelot slot and must not invent a bar."""
    assert sum(c for _s, c in key_distribution(["13A", "0B", "x"])) == 0


def test_case_and_spacing_are_tolerated() -> None:
    dist = dict(key_distribution([" 8a ", "8A"]))
    assert dist["8A"] == 2


def test_an_empty_library_gives_empty_slots() -> None:
    assert sum(c for _s, c in key_distribution([])) == 0
