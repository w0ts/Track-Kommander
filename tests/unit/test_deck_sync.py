"""Arming Traktor's Sync on every deck.

Traktor's Sync is a per-deck button. There is no "sync everything" switch in
Preferences and none exposed to a mapping, so the first thing a four-deck set
starts with is four button presses. The bridge can write each deck's
``sync.enabled``, which is exactly what the button does — these tests pin the
three pieces that make that reach Traktor:

* the command the app queues,
* the QML that acts on it,
* the version handshake that tells the user to reinstall the QML, because a
  bridge still on v1 would swallow the new command in silence.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from workers.webhook import WebhookWatcher

REPO = Path(__file__).resolve().parents[2]
API_DIR = REPO / "Traktor" / "D2" / "Api"


@pytest.fixture
def watcher() -> WebhookWatcher:
    """A watcher that is never started — only its command queue is exercised."""
    return WebhookWatcher(port=0)


# ── The queued command ──────────────────────────────────────────────────────


def test_sync_all_decks_is_queued_without_a_deck(watcher) -> None:
    watcher.queue_sync()
    assert watcher._pop_command() == {"action": "sync", "enabled": True}


def test_one_deck_can_be_armed_on_its_own(watcher) -> None:
    watcher.queue_sync("C")
    assert watcher._pop_command() == {
        "action": "sync",
        "enabled": True,
        "deck": "C",
    }


def test_sync_can_be_turned_off(watcher) -> None:
    watcher.queue_sync(enabled=False)
    assert watcher._pop_command()["enabled"] is False


def test_sync_does_not_displace_other_pending_commands(watcher) -> None:
    watcher.queue_play("A")
    watcher.queue_sync()
    assert watcher._pop_command()["action"] == "play"
    assert watcher._pop_command()["action"] == "sync"
    assert watcher._pop_command() is None


# ── The QML that receives it ────────────────────────────────────────────────


def test_the_qml_handles_the_sync_action() -> None:
    qml = (API_DIR / "ApiModule.qml").read_text(encoding="utf-8")
    assert 'cmd.action === "sync"' in qml, "the bridge would drop the command the app sends"
    assert "function setAllDeckSync" in qml
    for deck in (1, 2, 3, 4):
        assert f'path: "app.traktor.decks.{deck}.sync.enabled"' in qml


def test_the_qml_version_matches_what_the_app_expects() -> None:
    """A bridge on the old version ignores ``sync`` without saying so.

    The version handshake is the only thing that turns that silence into
    "reinstall", so the two constants have to move together.
    """
    js = (API_DIR / "ApiClient.js").read_text(encoding="utf-8")
    declared = js.split('var D2_VERSION = "')[1].split('"')[0]

    expected = _expected_version()
    assert declared == expected, (
        f"ApiClient.js declares v{declared} but the app expects v{expected} — "
        "bump both when the QML API changes"
    )


def _expected_version() -> str:
    """Read the constant out of ``ui.settings_common`` without importing Qt widgets."""
    src = (REPO / "src" / "ui" / "settings_common.py").read_text(encoding="utf-8")
    line = next(ln for ln in src.splitlines() if ln.startswith("D2_EXPECTED_VERSION"))
    return line.split('"')[1]
