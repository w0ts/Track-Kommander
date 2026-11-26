"""Docked, focus follows the pointer between this window and Traktor (macOS).

macOS spends the first click on an inactive app activating it, and Traktor
does not pass that click on to the control under it: with this window
active, the first click on a Traktor knob did nothing. So Traktor is made
active as the pointer moves onto it — the click then lands on an active app
— and this window is made active again as the pointer comes back, so its
keyboard shortcuts work. Windows passes first clicks through already.

Nothing switches while a mouse button is held (a knob being turned, a track
being dragged onto a deck) or while some other app is in front.
"""

from __future__ import annotations

from typing import Literal

Front = Literal["traktor", "us", "other"]
Target = Literal["traktor", "us"]


def decide(*, in_us: bool, in_traktor: bool, front: Front, buttons_down: bool) -> Target | None:
    """Which app should become active, or None to leave things as they are."""
    if buttons_down or front == "other":
        return None
    if in_us:
        return "us" if front == "traktor" else None
    if in_traktor and front == "us":
        return "traktor"
    return None
