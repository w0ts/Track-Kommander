"""Inside Traktor, every Library tab is a button in the band; Settings is one button whose page
keeps its own tab row (Matching · Tagging · an add-on's pages), and About one of its own."""

from __future__ import annotations

from ui.band_pages import BAND_PAGES, popup_key
from ui.library import LibraryTab
from ui.native_icons import BUTTON_GLYPHS
from ui.settings import SettingsTab
from workers.webhook_posts import UI_ACTIONS


def test_settings_is_one_button_and_every_library_tab_has_one() -> None:
    settings = {button: tab for button, (kind, tab) in BAND_PAGES.items() if kind == "settings"}
    library = {tab for kind, tab in BAND_PAGES.values() if kind == "library"}
    assert settings == {"S_SETTINGS": "", "S_ABOUT": "About"}  # User, 2026-11-24
    assert library == set(LibraryTab._TAB_ORDER)


def test_the_band_may_post_every_page_and_each_has_an_icon() -> None:
    assert set(BAND_PAGES) <= UI_ACTIONS
    assert set(BAND_PAGES) <= set(BUTTON_GLYPHS)


def test_standalone_tk_pops_up_the_whole_page() -> None:
    assert popup_key("S_SETTINGS") == "SETTINGS"
    assert popup_key("L_EXPORT") == "LIB"
    assert popup_key("STATS") == "STATS"
    assert popup_key("SETTINGS") == "SETTINGS"  # the old names still work


def _settings_in_traktor(tmp_path) -> SettingsTab:
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QCoreApplication, QSettings
    from PySide6.QtWidgets import QApplication

    QApplication.instance() or QApplication([])
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path))
    QCoreApplication.setApplicationName("tk-band-pages-test")
    tab = SettingsTab()
    tab.fold_for_traktor()
    return tab


def _shown_tabs(tab: SettingsTab) -> list[str]:
    bar = tab._tab_widget
    return [bar.tabText(i) for i in range(bar.count()) if bar.isTabVisible(i)]


def test_inside_traktor_settings_keeps_matching_automix_tagging_tabs(tmp_path) -> None:
    from PySide6.QtWidgets import QGroupBox

    tab = _settings_in_traktor(tmp_path)
    assert tab._automix_group.parent() is tab._tab_pages["AutoMix"]  # a tab of its own (User)
    tagging = tab._tab_pages["Tagging"]  # the Discogs / Spotify set-up, out of Library
    titles = {g.title() for g in tagging.findChildren(QGroupBox)}
    assert any("SPOTIFY" in t for t in titles) and any("DISCOGS" in t for t in titles)
    tab.show_traktor_settings()
    shown = _shown_tabs(tab)
    assert shown[:3] == ["Matching", "AutoMix", "Tagging"]  # an add-on's pages follow
    assert set(shown) <= set(SettingsTab.TRAKTOR_TABS)
    assert not tab._tab_widget.tabBar().isHidden()


def test_inside_traktor_the_settings_forms_are_flush_left(tmp_path) -> None:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QFormLayout

    tab = _settings_in_traktor(tmp_path)
    forms = [f for page in tab._tab_pages.values() for f in page.findChildren(QFormLayout)]
    assert forms  # Discogs / Spotify set-up rows
    left = Qt.AlignmentFlag.AlignLeft
    assert all(f.formAlignment() & left and f.labelAlignment() & left for f in forms)


def test_about_is_its_own_page_and_settings_comes_back_on_its_last_tab(tmp_path) -> None:
    tab = _settings_in_traktor(tmp_path)
    tab.show_traktor_settings()
    tab.show_tab("Tagging")
    tab.show_traktor_about()
    bar = tab._tab_widget
    assert bar.tabText(bar.currentIndex()) == "About" and bar.tabBar().isHidden()
    tab.show_traktor_settings()
    assert bar.tabText(bar.currentIndex()) == "Tagging"
    assert "About" not in _shown_tabs(tab)


def test_without_an_add_on_every_spare_slot_stays_hidden() -> None:
    """User, 2026-11-25: no add-on, no add-on icons (libtk starts the slots hidden; TK shows only
    the ones an add-on took)."""
    from ui.band_extensions import SLOTS, slot_visibility

    assert slot_visibility() == dict.fromkeys(SLOTS, True)


def test_an_add_ons_buttons_are_shown_and_only_those() -> None:
    from core.extensions import BandButton, add_band_button
    from ui.band_extensions import slot_visibility

    add_band_button(BandButton("Extra", "Extra\nIts page.", lambda p: None))
    assert slot_visibility() == {"X1": False, "X2": True, "X3": True, "X4": True}
