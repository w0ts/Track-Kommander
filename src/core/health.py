"""Connection/setup health checks surfaced to the user as notices.

The decision logic is a pure function (:func:`evaluate_health`) so it can be
unit-tested without Qt: the caller (``MainWindow``) samples the runtime state
— is Traktor running, is the webhook server bound, how long since the last
D2 ping, are the macOS permissions granted — and this module decides which
warnings the user should see.

Policy (per product decision):

* Traktor NOT running → no notices at all. The user isn't DJing; nagging
  about ports or permissions would be noise.
* Traktor running but the D2 bridge has never pinged / stopped pinging →
  warn: the user thinks they're connected but automation and deck state are
  dead.
* Traktor running but our webhook server couldn't bind its port → warn with
  the error.
* Traktor running on macOS without Accessibility / Screen Recording → warn:
  deck loading (synthesized drags) and deck auto-detection (screenshots)
  silently no-op without them. On Windows both checks report granted, so
  these notices never fire there.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass

from core import traktor_process

_log = logging.getLogger(__name__)

_IS_WIN = sys.platform == "win32"
_IS_MAC = sys.platform == "darwin"

# The D2 QML posts /keepAlive every ~1 s while Traktor runs. Settings' green
# dot uses a 5 s watchdog; the banner is more conservative so a GC pause or a
# busy Traktor doesn't flash a scary warning.
PING_STALE_S = 15.0

# Grace period after Traktor is first seen running before we complain about a
# missing bridge connection: Traktor takes a while to load its QML controllers,
# and the very first keepAlive can lag well behind the window appearing.
STARTUP_GRACE_S = 20.0


@dataclass(frozen=True)
class Notice:
    """One user-facing warning. ``key`` is stable for dedupe/dismissal."""

    key: str
    severity: str  # "info" | "warning" | "error"
    text: str
    # "settings" | "accessibility" | "screen_recording" | "update" | "addon_update"
    action: str | None = None
    action_label: str | None = None


def traktor_running() -> bool:
    """Return True if a Traktor window exists on this machine.

    Unlike :func:`core.drag.is_traktor_visible` (which errs on the side of
    True so it never blocks a feature), this errs on the side of **False**:
    it gates warnings, and a broken check must yield silence, not nagging.
    """
    try:
        if _IS_WIN:
            return traktor_process.window_bounds() is not None
        if _IS_MAC:
            return bool(traktor_process.running_apps())
        return False
    except Exception:
        _log.debug("traktor_running: check failed", exc_info=True)
        return False


def traktor_frontmost() -> bool:
    """Return True when Traktor is the frontmost (active) application.

    Errs on the side of **False**: it gates the post-autostart re-raise of
    Kommander's window, and a broken check must never make Kommander fight
    the user for focus.
    """
    return traktor_process.front_app() == "traktor"


# Post-autostart keep-on-top watchdog bounds: stop re-raising Kommander a
# short settle time after the D2 bridge's first ping (= Traktor finished
# loading and made its final focus grab), or after a hard cap when the
# bridge never connects.
AUTOSTART_RAISE_MAX_S = 180.0
AUTOSTART_RAISE_SETTLE_S = 10.0


def autostart_raise_done(elapsed_s: float, first_ping_age_s: float | None) -> bool:
    """Decide when the keep-Kommander-on-top watchdog should stop.

    ``elapsed_s`` is seconds since Traktor was auto-started;
    ``first_ping_age_s`` is seconds since the D2 bridge's first ping
    (``None`` = bridge has not connected yet).
    """
    if elapsed_s > AUTOSTART_RAISE_MAX_S:
        return True
    return first_ping_age_s is not None and first_ping_age_s > AUTOSTART_RAISE_SETTLE_S


def _inside_traktor_notices(
    traktor_up_for_s: float, webhook_ok: bool, ping_age_s: float | None
) -> tuple[Notice, ...]:
    """Inside Traktor: no D2 bridge to install, no controller to add. What can
    still fail is libtk's own link: its band posts (the webhook port) and its
    deck-state callbacks (their ping)."""
    if not webhook_ok:
        text = (
            "Kommander's local port is taken, so the buttons in Traktor's band "
            "cannot reach it. Another app may be using the port."
        )
        return (Notice(key="webhook-down", severity="error", text=text),)
    if traktor_up_for_s >= STARTUP_GRACE_S and (ping_age_s is None or ping_age_s > PING_STALE_S):
        text = (
            "Kommander is not getting Traktor's deck state. Deck status and "
            "automation won't work. Quit and restart Traktor."
        )
        return (Notice(key="native-silent", severity="warning", text=text),)
    return ()


def evaluate_health(
    *,
    traktor_up: bool,
    traktor_up_for_s: float,
    webhook_ok: bool,
    webhook_error: str | None,
    ping_age_s: float | None,
    accessibility: bool,
    screen_recording: bool,
    inside_traktor: bool = False,
) -> tuple[Notice, ...]:
    """Decide which notices to show for the sampled runtime state.

    ``ping_age_s`` is seconds since the last valid POST from the D2 QML
    (``None`` = never). ``traktor_up_for_s`` is how long Traktor has been
    continuously seen running — connection warnings are suppressed during
    the first :data:`STARTUP_GRACE_S` so Traktor can finish loading its
    controllers. ``inside_traktor``: TK runs in the patched Traktor, where
    deck state comes from libtk (its own ping), never from the D2 bridge.
    """
    if not traktor_up:
        return ()

    notices: list[Notice] = []

    if inside_traktor:
        notices.extend(_inside_traktor_notices(traktor_up_for_s, webhook_ok, ping_age_s))
    elif not webhook_ok:
        detail = f" ({webhook_error})" if webhook_error else ""
        notices.append(
            Notice(
                key="webhook-down",
                severity="error",
                text=(
                    "Traktor is running but Kommander's webhook server is not — "
                    f"Traktor updates cannot arrive{detail}. Another app may be "
                    "using the port."
                ),
                action="settings",
                action_label="Open Settings",
            )
        )
    elif traktor_up_for_s >= STARTUP_GRACE_S and (ping_age_s is None or ping_age_s > PING_STALE_S):
        never = ping_age_s is None
        notices.append(
            Notice(
                key="bridge-silent",
                severity="warning",
                text=(
                    "Traktor is running but not sending updates"
                    + (
                        " — the D2 QML bridge may not be installed, or the "
                        "Traktor Kontrol D2 controller isn't added in Traktor's "
                        "Controller Manager."
                        if never
                        else " anymore — it may have been closed mid-session, or the "
                        "D2 bridge stopped."
                    )
                    + " Deck status and automation won't work."
                ),
                action="settings",
                action_label="Open Settings",
            )
        )

    # Both permissions serve the synthesized drag that loads a deck standalone; inside Traktor
    # loads are direct calls (libtk), so neither is asked for there (User, 2026-11-24).
    if not accessibility and not inside_traktor:
        notices.append(
            Notice(
                key="perm-accessibility",
                severity="warning",
                text=(
                    "Accessibility permission is missing — loading tracks onto "
                    "decks (synthesized drag-and-drop) will silently do nothing."
                ),
                action="accessibility",
                action_label="Grant…",
            )
        )

    if not screen_recording and not inside_traktor:
        notices.append(
            Notice(
                key="perm-screen-recording",
                severity="warning",
                text=(
                    "Screen Recording permission is missing — deck positions "
                    "can't be auto-detected, so deck loading may aim wrong."
                ),
                action="screen_recording",
                action_label="Grant…",
            )
        )

    return tuple(notices)
