"""An add-on's band buttons inside Traktor (core.extensions.BandButton) on libtk's spare slots.

libtk has a few neutral buttons, X1..X4, in a group of their own between Stats and Settings.
Each button an add-on registers takes the next slot, in order: its page (a Library tab), icon,
tooltip and busy state. Slots nobody took are hidden (ui.mw_native).
"""

from __future__ import annotations

import ctypes
import logging

from core.extensions import BandButton, addon_shown, band_buttons
from workers.native_lib import process_symbol

log = logging.getLogger(__name__)

#: libtk's spare band buttons (tk_inject.mm), in band order.
SLOTS = ("X1", "X2", "X3", "X4")


def assigned() -> dict[str, BandButton]:
    """Slot → the add-on's button on it."""
    buttons = band_buttons()
    if len(buttons) > len(SLOTS):
        log.warning(
            "band: %d add-on buttons, %d slots: the rest are left out", len(buttons), len(SLOTS)
        )
    return dict(zip(SLOTS, buttons, strict=False))


def button_for(slot: str) -> BandButton | None:
    return assigned().get(slot)


def page_of(slot: str) -> tuple[str, str] | None:
    """("library", tab) for a slot an add-on took; None for any other name."""
    button = button_for(slot)
    return ("library", button.tab) if button is not None else None


def unused_slots() -> list[str]:
    return [slot for slot in SLOTS if slot not in assigned()]


def slot_visibility() -> dict[str, bool]:
    """Slot → hidden. libtk starts every spare slot hidden; TK shows the ones an add-on took,
    unless the add-on is hidden (core.extensions.addon_shown)."""
    taken = assigned() if addon_shown() else {}
    return {slot: slot not in taken for slot in SLOTS}


def buttons_to_hide(
    pages: dict[str, tuple[str, str]],
    gone: set[str],
    settings_tabs: set[str],
    library_tabs: set[str],
) -> list[str]:
    """The band buttons to hide inside Traktor, in a stable order.

    *gone* is hidden whether or not it is a page: the spare slots nobody took are not pages,
    and walking only the pages left them on the band (four empty gears on Windows, where no
    add-on was installed). A page goes too when this TK has no tab for it.
    """
    hide = set(gone)
    for button, (kind, tab) in pages.items():
        missing = (kind == "settings" and bool(tab) and tab not in settings_tabs) or (
            kind == "library" and tab not in library_tabs
        )
        if missing:
            hide.add(button)
    return sorted(hide)


def busy_states() -> dict[str, bool]:
    """{slot: busy} for the buttons that say. Slow (files, processes): never on the main thread."""
    states = {}
    for slot, button in assigned().items():
        if button.busy is None:
            continue
        try:
            states[slot] = bool(button.busy())
        except Exception as exc:  # an add-on's state is not worth TK's band
            log.info("band: %s busy state unreadable (%s)", slot, exc)
    return states


def has_busy_states() -> bool:
    return any(button.busy is not None for button in assigned().values())


def push_tooltips() -> int:
    """Each taken slot's tooltip to libtk. Returns how many it took."""
    fn = process_symbol("tk_band_set_tooltip")  # macOS and Windows alike
    if fn is None:
        log.warning("libtk has no tk_band_set_tooltip: the add-on's buttons have no tooltip")
        return 0
    fn.argtypes = [ctypes.c_char_p, ctypes.c_char_p]
    fn.restype = ctypes.c_int
    return sum(
        1 for slot, button in assigned().items() if fn(slot.encode(), button.tooltip.encode()) == 0
    )
