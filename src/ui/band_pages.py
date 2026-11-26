"""TK's pages as buttons in the band inside Traktor (integrated mode).

Inside Traktor, Library has no tab bar: each of its tabs is a button in the
band (libtk), and so is Stats. Settings is one button whose page keeps a tab row
of its own; About is a button of its own. With the band's list button they
form one selector, like Traktor's loop-length buttons: one page at a time, the
playlist being the list button's page. Standalone TK keeps its tabs.
"""

from __future__ import annotations

#: Band button → (which page, which tab). The names are what libtk posts in /uiAction.
#: Settings is ONE button inside Traktor, its page keeping its own tab row (Matching · Tagging and
#: an add-on's pages; SettingsTab.TRAKTOR_TABS) — "" = that page, not one tab (User,
#: 2026-11-24). About stays a button of its own, last. An add-on's buttons are libtk's spare
#: slots (ui.band_extensions), resolved by page_of.
BAND_PAGES: dict[str, tuple[str, str]] = {
    "S_SETTINGS": ("settings", ""),
    "S_ABOUT": ("settings", "About"),
    "L_SCAN": ("library", "Scan"),
    "L_HOTCUES": ("library", "Hotcues"),
    "L_TAGGING": ("library", "Tagging"),
    "L_PLAYLISTS": ("library", "Playlists"),
    "L_EXPORT": ("library", "Export"),
    "STATS": ("stats", ""),
}

#: The page a standalone TK pops up for a band button (it has whole pages, with their tabs).
POPUP_FOR = {"settings": "SETTINGS", "library": "LIB", "stats": "STATS"}


def page_of(button: str) -> tuple[str, str] | None:
    """(page, tab) for a band button: TK's own, or a slot an add-on took; None for the rest."""
    page = BAND_PAGES.get(button)
    if page is not None:
        return page
    from ui.band_extensions import page_of as extension_page

    return extension_page(button)


def all_pages() -> dict[str, tuple[str, str]]:
    """Every band page button now: TK's and the add-on's slots."""
    from ui.band_extensions import assigned

    return {**BAND_PAGES, **{slot: ("library", b.tab) for slot, b in assigned().items()}}


def popup_key(button: str) -> str:
    """The standalone popup a band page button stands for ("SETTINGS", "LIB" or "STATS")."""
    kind, _tab = page_of(button) or ("", "")
    return POPUP_FOR.get(kind, button)
