"""Short labels have to stay guessable.

The rank list and the correlation map both shorten them, and a label that
shortens to the same thing as its neighbour ("Bass range" and "Bass notes"
both becoming "Bass ") tells you nothing about which row you are reading.
"""

from __future__ import annotations

from ui.stats import short_axis_labels


def test_distinct_labels_are_truncated() -> None:
    assert short_axis_labels(["Brightness", "Loudness"], 5) == ["Brigh", "Loudn"]


def test_a_collision_keeps_enough_to_tell_them_apart() -> None:
    got = short_axis_labels(["Bass range", "Bass notes"], 5)
    assert got[0] != got[1]
    assert all(lbl.startswith("Bass") for lbl in got)


def test_only_the_colliding_labels_grow() -> None:
    got = short_axis_labels(["Bass range", "Bass notes", "Loudness"], 5)
    assert got[-1] == "Loudn", "a label with no clash stays short"


def test_three_way_collisions_resolve() -> None:
    got = short_axis_labels(["Bass range", "Bass notes", "Bass jump"], 5)
    assert len(set(got)) == 3


def test_a_label_shorter_than_the_limit_is_untouched() -> None:
    assert short_axis_labels(["BPM", "Key"], 5) == ["BPM", "Key"]


def test_identical_labels_are_left_identical() -> None:
    """Nothing can distinguish them; growing for ever would not help."""
    assert short_axis_labels(["Bass", "Bass"], 5) == ["Bass", "Bass"]


def test_the_real_registry_shortens_without_collisions() -> None:
    from core.features import FEATURES

    labels = [f.short_label for f in FEATURES]
    got = short_axis_labels(labels, 5)
    assert len(set(got)) == len(set(labels)), "two axes read the same in the map"
