"""When the display is kept awake: only while Traktor is playing something.

The lock used to be held for as long as Traktor was *running*, which kept the
screen lit all evening for a Traktor left open with nothing on the decks —
"the screensaver killer is working too well". Playback is what a DJ is
reading the screen for, so playback is the condition.

Pure state machine, no Qt, driven with an explicit clock. Three details are
deliberate and each has a test:

* a short **grace** after the last deck stops. On macOS the idle timer keeps
  counting while an assertion is held, so dropping it the instant playback
  stops would blank the screen *immediately* on a set that has been running
  hands-off for twenty minutes — the moment the DJ pauses to talk.
* **staleness**: the bridge reports deck state about once a second; if it
  has gone quiet, what it last said cannot vouch for what is playing now.
* **Traktor gone** releases at once, grace or not: nothing is being read.
"""

from __future__ import annotations

from core.idle import GRACE_AFTER_STOP_S, STALE_AFTER_S, PlaybackWakePolicy

T0 = 1_000.0


def playing(policy: PlaybackWakePolicy, now: float, *decks: str) -> None:
    for deck in decks:
        policy.deck(deck, True, now)


def test_nothing_playing_nothing_held() -> None:
    p = PlaybackWakePolicy()
    assert p.wanted(T0) is False
    p.deck("A", False, T0)
    assert p.wanted(T0) is False


def test_a_playing_deck_holds_the_display() -> None:
    p = PlaybackWakePolicy()
    playing(p, T0, "A")
    assert p.wanted(T0) is True
    assert p.wanted(T0 + 2) is True  # still fresh


def test_the_preference_switches_it_off_regardless() -> None:
    p = PlaybackWakePolicy()
    playing(p, T0, "A")
    assert p.wanted(T0, enabled=False) is False


def test_one_deck_stopping_does_not_release_while_another_plays() -> None:
    p = PlaybackWakePolicy()
    playing(p, T0, "A", "B")
    p.deck("A", False, T0 + 1)
    assert p.wanted(T0 + 1) is True


def test_grace_after_the_last_deck_stops() -> None:
    """A pause to talk must not blank the screen on the spot."""
    p = PlaybackWakePolicy()
    playing(p, T0, "A")
    p.deck("A", False, T0 + 10)
    assert p.wanted(T0 + 10) is True
    assert p.wanted(T0 + 10 + GRACE_AFTER_STOP_S - 1) is True
    assert p.wanted(T0 + 10 + GRACE_AFTER_STOP_S + 1) is False


def test_play_again_within_grace_simply_continues() -> None:
    p = PlaybackWakePolicy()
    playing(p, T0, "A")
    p.deck("A", False, T0 + 10)
    playing(p, T0 + 30, "B")
    assert p.wanted(T0 + 30 + GRACE_AFTER_STOP_S + 60) is False, (
        "stale: nothing reported for a long time"
    )
    playing(p, T0 + 30 + GRACE_AFTER_STOP_S + 60, "B")
    assert p.wanted(T0 + 30 + GRACE_AFTER_STOP_S + 60) is True


def test_a_quiet_bridge_cannot_vouch_for_playback() -> None:
    """Last word was "playing", then silence: held through the grace, no
    longer — a Traktor that crashed mid-track must not pin the display."""
    p = PlaybackWakePolicy()
    playing(p, T0, "A")
    assert p.wanted(T0 + STALE_AFTER_S - 1) is True
    assert p.wanted(T0 + STALE_AFTER_S + 1) is True  # grace, not freshness
    assert p.wanted(T0 + GRACE_AFTER_STOP_S + 1) is False


def test_traktor_gone_releases_immediately() -> None:
    p = PlaybackWakePolicy()
    playing(p, T0, "A")
    p.traktor_gone()
    assert p.wanted(T0 + 1) is False


def test_starts_clean_after_traktor_comes_back() -> None:
    p = PlaybackWakePolicy()
    playing(p, T0, "A")
    p.traktor_gone()
    p.deck("A", False, T0 + 5)  # a keepAlive from the new Traktor
    assert p.wanted(T0 + 5) is False
