"""Reading Traktor's Key knob back, so the info line can show what a deck
actually sounds like.

We already send the knob a value when loading a transposed match. The DJ can
also turn it by hand, and either way the written key stops being the key you
hear — the same way the pitch fader makes the written BPM stop being the
tempo. This is the return path: the bridge reports the knob, the app reads
semitones out of it.
"""

from __future__ import annotations

import json

from workers.webhook import key_adjust_value, semitones_from_adjust


def test_a_zeroed_knob_is_no_shift() -> None:
    assert semitones_from_adjust(0.0) == 0


def test_it_undoes_what_we_send() -> None:
    """The two directions must agree, or loading a +2 match would make the
    info line claim something else."""
    for semitones in range(-12, 13):
        assert semitones_from_adjust(key_adjust_value(semitones)) == semitones


def test_the_full_range_is_an_octave() -> None:
    assert semitones_from_adjust(1.0) == 12
    assert semitones_from_adjust(-1.0) == -12


def test_a_knob_between_steps_reads_as_the_nearest() -> None:
    """Traktor sends a float; a value a hair off a semitone is that
    semitone, not a rounding-down to the one below."""
    assert semitones_from_adjust(0.0832) == 1
    assert semitones_from_adjust(0.0834) == 1
    assert semitones_from_adjust(-0.166) == -2


def test_nothing_outside_an_octave_is_believed() -> None:
    assert semitones_from_adjust(5.0) == 12
    assert semitones_from_adjust(-5.0) == -12


def test_junk_is_no_shift() -> None:
    """The bridge is external data: an old D2 build sends no field at all,
    and a broken one could send anything."""
    assert semitones_from_adjust(None) == 0
    assert semitones_from_adjust("x") == 0
    assert semitones_from_adjust(float("nan")) == 0


# ── The keep-alive carries it ──────────────────────────────────────────────


class _Spy:
    """Stands in for a Qt signal: records what was emitted."""

    def __init__(self) -> None:
        self.seen: list[tuple] = []

    def emit(self, *args) -> None:
        self.seen.append(args)


class _FakeWatcher:
    """Every signal ``_handle_post`` touches on the keep-alive path."""

    def __init__(self) -> None:
        for name in (
            "deck_state",
            "deck_position",
            "deck_phase",
            "deck_key_adjust",
            "deck_on_air",
            "d2_version",
            "layout_changed",
            "traktor_ping",
        ):
            setattr(self, name, _Spy())


def _keepalive(decks: list[dict]) -> _FakeWatcher:
    from workers.webhook import WebhookWatcher

    watcher = _FakeWatcher()
    WebhookWatcher._handle_post(watcher, "/keepAlive", json.dumps({"decks": decks}))
    return watcher


def test_the_keepalive_reports_each_deck() -> None:
    watcher = _keepalive(
        [
            {"deck": "A", "play": True, "keyAdjust": key_adjust_value(2)},
            {"deck": "B", "play": False, "keyAdjust": 0.0},
        ]
    )
    assert watcher.deck_key_adjust.seen == [("A", 2), ("B", 0)]


def test_a_deck_with_no_reading_is_left_alone() -> None:
    """An older bridge sends no keyAdjust. Emitting a zero for it would
    wipe a shift the app itself set moments earlier."""
    watcher = _keepalive([{"deck": "A", "play": True}])
    assert watcher.deck_key_adjust.seen == []
