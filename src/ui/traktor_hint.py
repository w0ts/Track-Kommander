"""A short hint under one of the band's buttons inside Traktor (User, 2026-11-24).

TK code asks for it (``show("AUTO", "AutoMix")``); traktor-native's embed_tk draws it, under the
button, styled like Traktor's tooltip, for a moment. Outside Traktor nothing registers a drawer
and :func:`show` does nothing.
"""

from __future__ import annotations

from collections.abc import Callable

_drawer: Callable[[str, str], None] | None = None


def set_drawer(drawer: Callable[[str, str], None] | None) -> None:
    global _drawer
    _drawer = drawer


def show(button: str, text: str) -> bool:
    """Show *text* under band button *button*; False when there is no band (not in Traktor)."""
    if _drawer is None:
        return False
    _drawer(button, text)
    return True
