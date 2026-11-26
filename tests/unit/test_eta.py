"""An export's "about 3 min left" — per phase, since analysing, copying and
writing analysis run at rates that have nothing to do with each other."""

from __future__ import annotations

from core.eta import Eta, format_eta


class _Clock:
    def __init__(self) -> None:
        self.t = 100.0

    def __call__(self) -> float:
        return self.t


def test_nothing_is_promised_before_there_is_a_rate() -> None:
    clock = _Clock()
    eta = Eta(now=clock)
    eta.start(10)
    assert eta.remaining(0) is None
    clock.t += 1
    assert eta.remaining(1) is None, "one sample is not a rate"


def test_remaining_is_the_rate_so_far_over_what_is_left() -> None:
    clock = _Clock()
    eta = Eta(now=clock)
    eta.start(10)
    clock.t += 4
    assert eta.remaining(2) == 16  # 2 s each, 8 to go


def test_a_new_phase_starts_from_scratch() -> None:
    clock = _Clock()
    eta = Eta(now=clock)
    eta.start(10)
    clock.t += 100
    eta.remaining(10)
    eta.start(4)
    clock.t += 2
    assert eta.remaining(2) == 2, "the slow phase must not colour the fast one"


def test_done_means_nothing_left() -> None:
    clock = _Clock()
    eta = Eta(now=clock)
    eta.start(3)
    clock.t += 3
    assert eta.remaining(3) == 0


def test_format() -> None:
    assert format_eta(None) == ""
    assert format_eta(0) == "almost done"
    assert format_eta(20) == "under a minute left"
    assert format_eta(59) == "under a minute left"
    assert format_eta(90) == "about 2 min left"
    assert format_eta(60 * 45) == "about 45 min left"
    assert format_eta(3600 * 1.5) == "about 1 h 30 left"
    assert format_eta(3600 * 2) == "about 2 h left"


def test_label_appends_the_estimate() -> None:
    clock = _Clock()
    eta = Eta(now=clock)
    eta.start(10)
    assert eta.label("Copying 0/10 — a.mp3", 0) == "Copying 0/10 — a.mp3"
    clock.t += 4
    assert eta.label("Copying 2/10 — b.mp3", 2) == ("Copying 2/10 — b.mp3  ·  under a minute left")
