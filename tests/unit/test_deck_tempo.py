"""The tempo a deck actually plays at, not the one written on the track.

The info line and the BPM ranking used the master clock, which only arrives
when it *changes* — a post lost on the way (the bridge keeps one request per
endpoint and drops the rest) left the app on the track's own 148 while the
deck played at 128.61. Each deck's ``tempo.true_bpm`` now rides the 1 s
keep-alive, so a missed reading is corrected a second later.
"""

from __future__ import annotations

import json
from pathlib import Path

from ui.now_playing import live_source_bpm
from workers.webhook import WebhookWatcher

ROOT = Path(__file__).resolve().parents[2]


class _Spy:
    def __init__(self) -> None:
        self.seen: list[tuple] = []

    def emit(self, *args) -> None:
        self.seen.append(args)


class _FakeWatcher:
    def __init__(self) -> None:
        for name in (
            "deck_state",
            "deck_position",
            "deck_phase",
            "deck_key_adjust",
            "deck_tempo",
            "deck_on_air",
            "d2_version",
            "layout_changed",
            "traktor_ping",
        ):
            setattr(self, name, _Spy())


def _keepalive(decks: list[dict]) -> _FakeWatcher:
    watcher = _FakeWatcher()
    WebhookWatcher._handle_post(watcher, "/keepAlive", json.dumps({"decks": decks}))
    return watcher


# ── The keep-alive carries it ──────────────────────────────────────────────


def test_the_keepalive_reports_each_deck_tempo() -> None:
    watcher = _keepalive(
        [
            {"deck": "A", "play": True, "tempo": 128.61},
            {"deck": "B", "play": False, "tempo": 124.0},
        ]
    )
    assert watcher.deck_tempo.seen == [("A", 128.61), ("B", 124.0)]


def test_no_reading_is_no_tempo() -> None:
    """An empty deck reports 0, an older bridge nothing, a broken one junk:
    none of them is a tempo to rank by."""
    watcher = _keepalive(
        [
            {"deck": "A", "play": True},
            {"deck": "B", "play": False, "tempo": 0},
            {"deck": "C", "play": False, "tempo": "x"},
            {"deck": "D", "play": False, "tempo": float("nan")},
        ]
    )
    assert watcher.deck_tempo.seen == []


def test_the_bridge_sends_the_deck_tempo() -> None:
    qml = (ROOT / "Traktor/D2/Api/ApiDeck.qml").read_text()
    assert ".tempo.true_bpm" in qml
    assert '"tempo":' in qml


# ── Which tempo the source track is at ─────────────────────────────────────


def test_the_selected_deck_tempo_wins() -> None:
    """Deck A at 128.61 while the master clock follows another deck."""
    assert live_source_bpm("A", {"A": 128.61}, master_bpm=140.0, playing={"A"}) == 128.61


def test_a_paused_deck_still_has_its_tempo() -> None:
    """The pitch fader holds while paused; the master clock is someone else's."""
    assert live_source_bpm("A", {"A": 128.61}, master_bpm=140.0, playing=set()) == 128.61


def test_without_a_deck_reading_the_master_clock_stands_in() -> None:
    """An older bridge sends no deck tempo: keep the old behaviour."""
    assert live_source_bpm("A", {}, master_bpm=128.61, playing={"A"}) == 128.61
    assert live_source_bpm("A", {}, master_bpm=128.61, playing=set()) == 0.0


def test_no_selected_deck_has_no_live_tempo() -> None:
    assert live_source_bpm(None, {"A": 128.0}, master_bpm=128.0, playing={"A"}) == 0.0
