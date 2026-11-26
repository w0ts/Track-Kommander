"""Unit tests for the connection/setup health notices.

``core.health.evaluate_health`` is a pure function — sampled runtime state in,
tuple of user-facing notices out — so it's tested directly, no Qt required.
"""

from __future__ import annotations

import pytest

from core.drag import DragOutcome
from core.health import (
    AUTOSTART_RAISE_MAX_S,
    AUTOSTART_RAISE_SETTLE_S,
    PING_STALE_S,
    STARTUP_GRACE_S,
    autostart_raise_done,
    evaluate_health,
)

# A healthy baseline: Traktor up for a while, webhook bound, fresh ping,
# permissions granted. Individual tests override one dimension at a time.
HEALTHY = dict(
    traktor_up=True,
    traktor_up_for_s=STARTUP_GRACE_S + 60.0,
    webhook_ok=True,
    webhook_error=None,
    ping_age_s=1.0,
    accessibility=True,
    screen_recording=True,
)


def keys(notices) -> set[str]:
    return {n.key for n in notices}


# ── silent cases ───────────────────────────────────────────────────────────────


def test_all_healthy_yields_no_notices() -> None:
    assert evaluate_health(**HEALTHY) == ()


def test_traktor_not_running_is_always_silent() -> None:
    """Product decision: no Traktor → no nagging, whatever else is broken."""
    broken = {
        **HEALTHY,
        "traktor_up": False,
        "webhook_ok": False,
        "webhook_error": "port busy",
        "ping_age_s": None,
        "accessibility": False,
        "screen_recording": False,
    }
    assert evaluate_health(**broken) == ()


def test_startup_grace_suppresses_bridge_warning() -> None:
    """Traktor just appeared — give its QML controllers time to load."""
    state = {**HEALTHY, "traktor_up_for_s": 5.0, "ping_age_s": None}
    assert "bridge-silent" not in keys(evaluate_health(**state))


def test_fresh_ping_means_connected() -> None:
    state = {**HEALTHY, "ping_age_s": PING_STALE_S - 1.0}
    assert "bridge-silent" not in keys(evaluate_health(**state))


# ── warning cases ─────────────────────────────────────────────────────────────


def test_never_pinged_after_grace_warns_bridge_silent() -> None:
    state = {**HEALTHY, "ping_age_s": None}
    found = keys(evaluate_health(**state))
    assert "bridge-silent" in found


def test_stale_ping_warns_bridge_silent() -> None:
    state = {**HEALTHY, "ping_age_s": PING_STALE_S + 1.0}
    assert "bridge-silent" in keys(evaluate_health(**state))


def test_never_and_stale_produce_different_wording() -> None:
    """'Never connected' points at install; 'stopped' points at the session."""
    never = evaluate_health(**{**HEALTHY, "ping_age_s": None})
    stale = evaluate_health(**{**HEALTHY, "ping_age_s": PING_STALE_S + 1.0})
    text_never = next(n.text for n in never if n.key == "bridge-silent")
    text_stale = next(n.text for n in stale if n.key == "bridge-silent")
    assert text_never != text_stale
    assert "installed" in text_never


def test_webhook_down_is_error_and_masks_bridge_warning() -> None:
    """No server → 'no ping' is implied; only the server error is shown."""
    state = {
        **HEALTHY,
        "webhook_ok": False,
        "webhook_error": "[Errno 48] Address already in use",
        "ping_age_s": None,
    }
    notices = evaluate_health(**state)
    assert keys(notices) == {"webhook-down"}
    (n,) = notices
    assert n.severity == "error"
    assert "Address already in use" in n.text


def test_missing_permissions_warn_while_traktor_runs() -> None:
    state = {**HEALTHY, "accessibility": False, "screen_recording": False}
    found = keys(evaluate_health(**state))
    assert {"perm-accessibility", "perm-screen-recording"} <= found


def test_notice_actions_point_somewhere_actionable() -> None:
    state = {
        **HEALTHY,
        "webhook_ok": False,
        "accessibility": False,
        "screen_recording": False,
    }
    for n in evaluate_health(**state):
        assert n.action is not None
        assert n.action_label


# ── DragOutcome contract ───────────────────────────────────────────────────────


def test_drag_outcome_truthiness_matches_ok() -> None:
    assert DragOutcome(True)
    assert not DragOutcome(False, "reason")


def test_drag_outcome_failure_carries_reason() -> None:
    out = DragOutcome(False, "Deck B could not be located")
    assert not out
    assert out.reason == "Deck B could not be located"


def test_drag_outcome_is_immutable() -> None:
    out = DragOutcome(True)
    with pytest.raises(AttributeError):
        out.ok = False  # type: ignore[misc]


# ── autostart keep-on-top watchdog ─────────────────────────────────────────────


def test_raise_watch_runs_while_bridge_silent() -> None:
    """No ping yet → keep re-raising (Traktor is still loading)."""
    assert not autostart_raise_done(elapsed_s=30.0, first_ping_age_s=None)


def test_raise_watch_survives_settle_window_after_first_ping() -> None:
    """Traktor's final focus grab lands around the first ping — hold on."""
    assert not autostart_raise_done(elapsed_s=60.0, first_ping_age_s=AUTOSTART_RAISE_SETTLE_S - 1.0)


def test_raise_watch_stops_after_settle() -> None:
    assert autostart_raise_done(elapsed_s=60.0, first_ping_age_s=AUTOSTART_RAISE_SETTLE_S + 1.0)


def test_raise_watch_stops_at_hard_cap_without_ping() -> None:
    """Bridge never connected → give up at the cap, don't re-raise forever."""
    assert autostart_raise_done(elapsed_s=AUTOSTART_RAISE_MAX_S + 1.0, first_ping_age_s=None)


# ── inside Traktor: never a D2 bridge notice ───────────────────────────────────


def test_inside_traktor_a_silent_link_never_mentions_the_d2_bridge() -> None:
    state = {**HEALTHY, "ping_age_s": None, "inside_traktor": True}
    notices = evaluate_health(**state)
    assert keys(notices) == {"native-silent"}
    assert all("D2" not in n.text and "bridge" not in n.text for n in notices)


def test_inside_traktor_a_taken_port_is_said_without_the_d2_bridge() -> None:
    state = {**HEALTHY, "webhook_ok": False, "inside_traktor": True}
    notices = evaluate_health(**state)
    assert keys(notices) == {"webhook-down"}
    assert "D2" not in notices[0].text


def test_inside_traktor_no_drag_permission_is_asked_for() -> None:
    assert evaluate_health(**{**HEALTHY, "inside_traktor": True}) == ()
    missing = evaluate_health(
        **{**HEALTHY, "inside_traktor": True, "accessibility": False, "screen_recording": False}
    )
    assert keys(missing) == set()  # loads are direct calls there: no synthesized drag
