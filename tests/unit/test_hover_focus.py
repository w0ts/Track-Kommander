"""Docked, focus follows the pointer between this window and Traktor (macOS).

The first click on an inactive Traktor only activated it; Traktor has to be
active already when the click lands.
"""

from __future__ import annotations

import pytest

from ui.hover_focus import decide


def _d(**kw):
    base = dict(in_us=False, in_traktor=False, front="us", buttons_down=False)
    base.update(kw)
    return decide(**base)


def test_pointer_onto_traktor_activates_it() -> None:
    assert _d(in_traktor=True, front="us") == "traktor"


def test_pointer_back_onto_us_activates_us() -> None:
    assert _d(in_us=True, front="traktor") == "us"


def test_our_window_wins_where_the_two_overlap() -> None:
    """Docked, this window sits over Traktor's library: that area is ours."""
    assert _d(in_us=True, in_traktor=True, front="traktor") == "us"
    assert _d(in_us=True, in_traktor=True, front="us") is None


def test_already_active_means_nothing_to_do() -> None:
    assert _d(in_traktor=True, front="traktor") is None
    assert _d(in_us=True, front="us") is None


@pytest.mark.parametrize(
    "where", [dict(in_us=True, front="traktor"), dict(in_traktor=True, front="us")]
)
def test_nothing_switches_while_a_button_is_held(where) -> None:
    """A knob being turned, a track being dragged onto a deck."""
    assert _d(buttons_down=True, **where) is None


@pytest.mark.parametrize("where", [dict(in_us=True), dict(in_traktor=True)])
def test_another_app_in_front_is_left_alone(where) -> None:
    assert _d(front="other", **where) is None


def test_pointer_elsewhere_does_nothing() -> None:
    assert _d(front="us") is None
    assert _d(front="traktor") is None
