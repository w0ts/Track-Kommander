"""ui.band_extensions.buttons_to_hide: which band buttons TK hides inside Traktor."""

from __future__ import annotations

from ui.band_extensions import buttons_to_hide

PAGES = {
    "S_SETTINGS": ("settings", ""),
    "S_ABOUT": ("settings", "About"),
    "L_SCAN": ("library", "Scan"),
    "L_HOTCUES": ("library", "Hotcues"),
}


def test_unused_spare_slots_are_hidden_though_they_are_not_pages():
    hide = buttons_to_hide(PAGES, {"X1", "X2", "X3", "X4"}, {"About"}, {"Scan", "Hotcues"})
    assert hide == ["X1", "X2", "X3", "X4"]


def test_a_page_without_its_tab_is_hidden():
    hide = buttons_to_hide(PAGES, set(), set(), {"Scan"})
    assert hide == ["L_HOTCUES", "S_ABOUT"]


def test_settings_without_a_tab_name_stays():
    assert "S_SETTINGS" not in buttons_to_hide(PAGES, set(), set(), {"Scan", "Hotcues"})


def test_gone_pages_and_missing_pages_together_once_each():
    hide = buttons_to_hide(PAGES, {"L_SCAN", "X4"}, {"About"}, {"Hotcues"})
    assert hide == ["L_SCAN", "X4"]
