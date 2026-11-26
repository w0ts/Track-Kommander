"""The Camelot wheel as a card: 24 slots, in wheel order, coloured by key.

A histogram of key *numbers* would be a lie — 12A and 1A are neighbours on
the wheel, not eleven apart — so this is a bar per slot, labelled, with the
empty ones left visible because those are the keys a set cannot move into.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from ui.stats import _KeyCard


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


def _card(qapp, camelots: list) -> _KeyCard:
    from core.stats import key_distribution, key_spread

    w = _KeyCard(key_distribution(camelots), key_spread(camelots))
    w.resize(260, 166)
    return w


def test_it_paints_without_data(qapp) -> None:
    _card(qapp, []).grab()


def test_it_paints_a_real_library(qapp) -> None:
    _card(qapp, ["7A"] * 88 + ["8A"] * 66 + ["5B"] * 20).grab()


def test_the_busiest_slot_is_reported(qapp) -> None:
    card = _card(qapp, ["7A"] * 88 + ["8A"] * 66)
    assert card.busiest == ("7A", 88)


def test_an_empty_library_has_no_busiest_slot(qapp) -> None:
    assert _card(qapp, []).busiest is None


def test_every_slot_is_drawn_even_when_empty(qapp) -> None:
    """The gaps are the point: they are the keys you cannot mix into."""
    card = _card(qapp, ["7A"])
    assert len(card.dist) == 24


def test_the_spread_badge_comes_from_the_wheel(qapp) -> None:
    wheel = [f"{n}{ab}" for n in range(1, 13) for ab in ("A", "B")]
    assert _card(qapp, wheel).spread == pytest.approx(100.0, abs=0.5)
    assert _card(qapp, ["8A"] * 50).spread == pytest.approx(0.0, abs=0.5)
