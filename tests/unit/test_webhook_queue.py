"""Commands wait for the bridge, but not for ever.

A command is an instruction for *now*: load this, play, mute. The queue had
no sense of time, so whatever was queued while Traktor was not listening was
delivered the moment it came back — a play or a mute arriving minutes after
it was asked for, in the middle of a set.
"""

from __future__ import annotations

import json
import logging

import pytest
from PySide6.QtCore import QCoreApplication

import workers.webhook as webhook
from workers.webhook import COMMAND_MAX_AGE_S, WebhookWatcher


@pytest.fixture(scope="module")
def qapp():
    yield QCoreApplication.instance() or QCoreApplication([])


@pytest.fixture
def clock(monkeypatch):
    now = {"t": 1000.0}
    monkeypatch.setattr(webhook, "_now", lambda: now["t"])
    return now


@pytest.fixture
def watcher(qapp, clock):
    return WebhookWatcher(port=0)


def test_a_command_is_delivered_as_it_was_queued(watcher) -> None:
    watcher.queue_play("A")

    got = watcher._pop_command()

    assert got == {"action": "play", "deck": "A"}
    assert json.loads(json.dumps(got)) == {"action": "play", "deck": "A"}


def test_commands_come_out_in_the_order_they_went_in(watcher) -> None:
    watcher.queue_play("A")
    watcher.queue_stop("B")

    assert watcher._pop_command()["action"] == "play"
    assert watcher._pop_command()["action"] == "stop"
    assert watcher._pop_command() is None


def test_a_command_just_inside_its_time_is_delivered(watcher, clock) -> None:
    watcher.queue_play("A")
    clock["t"] += COMMAND_MAX_AGE_S - 0.1

    assert watcher._pop_command() == {"action": "play", "deck": "A"}


def test_a_command_that_waited_too_long_is_dropped(watcher, clock, caplog) -> None:
    watcher.queue_play("A")
    clock["t"] += COMMAND_MAX_AGE_S + 1

    with caplog.at_level(logging.WARNING):
        assert watcher._pop_command() is None

    assert "play" in caplog.text


def test_a_fresh_command_behind_a_stale_one_is_delivered(watcher, clock) -> None:
    watcher.queue_play("A")
    clock["t"] += COMMAND_MAX_AGE_S + 1
    watcher.queue_stop("B")

    assert watcher._pop_command() == {"action": "stop", "deck": "B"}
    assert watcher._pop_command() is None


def test_a_long_poll_does_not_hand_out_a_stale_command(watcher, clock) -> None:
    watcher.queue_play("A")
    clock["t"] += COMMAND_MAX_AGE_S + 1

    assert watcher._wait_command(0.05) is None


def test_a_resync_is_not_a_command_and_does_not_expire(watcher, clock) -> None:
    """It asks for the state as it is when it arrives, so it is never late."""
    watcher.request_resync()
    clock["t"] += COMMAND_MAX_AGE_S * 10

    assert watcher._pop_command() == {"action": "resync"}


def test_a_command_put_back_is_first_in_line(watcher) -> None:
    watcher.queue_play("A")
    taken = watcher._pop_command()
    watcher.queue_stop("B")

    watcher._requeue(taken)

    assert watcher._pop_command()["action"] == "play"
    assert watcher._pop_command()["action"] == "stop"


# ── Stopping ────────────────────────────────────────────────────────────────


def test_stopping_a_watcher_that_never_served_does_nothing(watcher) -> None:
    watcher.stop()


def test_stop_survives_the_server_going_away_under_it(watcher) -> None:
    """``run()`` clears the server when it leaves. ``stop()`` looked, then
    looked again to use it, and found nothing the second time."""

    class _Vanishing:
        def __init__(self, owner) -> None:
            self.owner = owner
            self.shut = 0

        def __bool__(self) -> bool:
            self.owner._server = None  # run() got there first
            return True

        def shutdown(self) -> None:
            self.shut += 1

    server = _Vanishing(watcher)
    watcher._server = server

    watcher.stop()

    assert server.shut == 1
