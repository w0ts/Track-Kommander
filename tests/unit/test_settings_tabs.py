"""Structure tests for the tabbed Settings screen.

Settings used to be seven group boxes stacked in one scrolling column. Moving
them onto tabs is easy to get subtly wrong: a group can end up on no tab at
all and simply vanish, and `MainWindow` reaches into this widget by attribute
and method name, so a reshuffle can break the wiring silently.

These tests pin both — every group is reachable, and the public surface
`MainWindow` uses still exists and still works.
"""

from __future__ import annotations

import os
import sys

import pytest

pytest.importorskip("PySide6.QtWidgets")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QSettings, Qt
from PySide6.QtWidgets import QApplication, QGroupBox, QScrollArea


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def settings_tab(qapp, tmp_path, monkeypatch):
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path))
    QCoreApplication.setApplicationName("tk-settings-tabs-test")
    QCoreApplication.setOrganizationDomain("settings-tabs.invalid")
    from ui.settings import SettingsTab

    w = SettingsTab()
    yield w
    w.deleteLater()


def _groups(tab) -> set[str]:
    tw = tab._tab_widget
    found: set[str] = set()
    for i in range(tw.count()):
        found |= {g.title() for g in tw.widget(i).findChildren(QGroupBox)}
    return found


EXPECTED_GROUPS = {
    "PROBLEMS",
    "AUTOMIX",
    "MATCHING",
    "RESULT COLUMNS",
    "SHORTCUTS",
    # The Traktor tab, in order: which Traktor, its two preferences, then
    # everything about the connection. This replaced one STATUS box that had
    # all of it stacked together.
    "TRAKTOR",
    "PREFERENCES",
    "CONNECTION",
    "HARDWARE CONTROL",
    "INTERFACE",
}


def test_every_group_is_on_a_tab(settings_tab) -> None:
    missing = EXPECTED_GROUPS - _groups(settings_tab)
    assert not missing, f"groups built but not placed on any tab: {missing}"


def test_no_separate_permissions_group(settings_tab) -> None:
    """The grants moved into the Traktor tab's CONNECTION checklist; a
    leftover PERMISSIONS box would mean the same state is reported twice."""
    assert "PERMISSIONS" not in _groups(settings_tab)


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS-only TCC grants")
def test_permission_checks_are_connection_rows(settings_tab) -> None:
    """The three TCC grants read as more D2-style checks.

    Full Disk Access is one of them because it is the grant that decides the
    D2 install: the write happens in the elevated helper, and the permission
    macOS *names* for bundle edits does not reach it.
    """
    from PySide6.QtWidgets import QGroupBox, QLabel

    page = settings_tab._tab_pages["Traktor"]
    conn = next(g for g in page.findChildren(QGroupBox) if g.title() == "CONNECTION")
    titles = {lbl.text() for lbl in conn.findChildren(QLabel)}
    assert {"Accessibility", "Screen Recording", "Full Disk Access"} <= titles

    assert len(settings_tab._perm_rows) == 3
    for _dot, value, btn, _granted_fn, _what in settings_tab._perm_rows:
        assert value.text(), "a check row must say what its state is"
        assert btn.text() in ("Grant…", "Granted")


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS-only TCC grants")
def test_permission_poll_repaints_without_error(settings_tab) -> None:
    settings_tab._refresh_permissions()


def test_permission_problems_point_at_the_traktor_tab(settings_tab) -> None:
    """The PROBLEMS rows' "Go to" button has to follow the grants' new home."""
    for action in ("accessibility", "screen_recording"):
        assert settings_tab._NOTICE_TAB[action] == "Traktor"


def test_tabs_are_in_the_declared_order(settings_tab) -> None:
    tw = settings_tab._tab_widget
    shown = [tw.tabText(i) for i in range(tw.count())]
    declared = [n for n in settings_tab._TAB_ORDER if n in shown]
    assert shown == declared, "tab order must follow _TAB_ORDER"


def test_no_empty_tabs(settings_tab) -> None:
    tw = settings_tab._tab_widget
    for i in range(tw.count()):
        assert tw.widget(i).findChildren(QGroupBox), f"tab {tw.tabText(i)!r} has no content"


# ── The surface MainWindow depends on ───────────────────────────────────────

MAIN_WINDOW_METHODS = (
    "set_status",
    "set_traktor_active",
    "set_bridge_stats",
    "set_d2_version",
    "set_s4mk2_status",
)
MAIN_WINDOW_SIGNALS = (
    "s4mk2_config_changed",
    "matching_config_changed",
    "columns_config_changed",
    "reconnect_requested",
    "tooltip_config_changed",
    "traktor_config_changed",
)


@pytest.mark.parametrize("name", MAIN_WINDOW_METHODS + MAIN_WINDOW_SIGNALS)
def test_public_surface_exists(settings_tab, name: str) -> None:
    assert hasattr(settings_tab, name)


def test_public_setters_run(settings_tab) -> None:
    settings_tab.set_status(True, "Webhook server listening on port 12345")
    settings_tab.set_traktor_active(True)
    settings_tab.set_bridge_stats(42, 3.0)
    settings_tab.set_d2_version("1")
    settings_tab.set_s4mk2_status(False, "Not connected")
    assert "42" in settings_tab.bridge_stats.text()


# ── New controls ────────────────────────────────────────────────────────────


def test_sync_all_checkbox_persists(settings_tab) -> None:
    """Traktor cannot arm Sync on every deck; the switch that does lives here."""
    from ui.settings import SYNC_ALL_ON_CONNECT_KEY

    assert not settings_tab.sync_all_cb.isChecked(), "must be opt-in"
    settings_tab.sync_all_cb.setChecked(True)
    assert settings_tab._settings.value(SYNC_ALL_ON_CONNECT_KEY, False, type=bool) is True


def test_update_check_is_on_by_default_and_persists(settings_tab) -> None:
    """Testers ran old builds without knowing a fix had shipped."""
    from core.update_check import SETTING_KEY

    assert settings_tab.update_check_cb.isChecked(), "must be on by default"
    settings_tab.update_check_cb.setChecked(False)
    assert settings_tab._settings.value(SETTING_KEY, True, type=bool) is False


def test_tooltip_combo_offers_off_and_a_capped_range(settings_tab) -> None:
    from ui.widgets import TOOLTIP_MS_MAX

    values = [
        settings_tab.tooltip_combo.itemData(i) for i in range(settings_tab.tooltip_combo.count())
    ]
    assert 0 in values, "there must be a way to turn tooltips off"
    assert max(values) <= TOOLTIP_MS_MAX


def test_tooltip_choice_is_persisted_and_announced(settings_tab) -> None:
    seen = []
    settings_tab.tooltip_config_changed.connect(lambda: seen.append(1))
    idx = settings_tab.tooltip_combo.findData(0)  # "Off"
    settings_tab.tooltip_combo.setCurrentIndex(idx)
    assert seen, "changing the lifetime must tell NowPlaying to re-read it"
    assert int(QSettings().value("results_tooltip_ms")) == 0


def test_reset_warnings_button_clears_dismissals(settings_tab) -> None:
    from ui.dialogs import DISMISSED_GROUP, is_warning_dismissed

    s = QSettings()
    s.setValue(f"{DISMISSED_GROUP}/scan_traktor_running", True)
    s.sync()
    assert is_warning_dismissed("scan_traktor_running")

    settings_tab.reset_warnings_btn.click()
    assert not is_warning_dismissed("scan_traktor_running")


# ── Controls tab ───────────────────────────────────────────────────────────


def test_every_action_gets_an_editor(settings_tab) -> None:
    from core import actions

    ids = {a.id for a in actions.ACTIONS}
    assert set(settings_tab._shortcut_buttons) == ids
    assert set(settings_tab._s4_buttons) == ids


def test_no_conflict_warning_with_the_defaults(settings_tab) -> None:
    assert not settings_tab.shortcut_conflicts.isVisible()


def test_tabs_stay_few_enough_to_read(settings_tab) -> None:
    """Seven tabs made Qt elide the labels to "Gene…" at the real window
    width. Keep the count down rather than re-learn that."""
    assert settings_tab._tab_widget.count() <= 5


def test_tab_labels_are_never_elided(settings_tab) -> None:
    from PySide6.QtCore import Qt

    assert settings_tab._tab_widget.tabBar().elideMode() == Qt.TextElideMode.ElideNone


def test_a_clash_is_reported(settings_tab) -> None:
    from core import actions

    clash = actions.binding_for("results.up")
    actions.set_binding("results.down", clash)
    try:
        settings_tab._refresh_conflicts()
        text = settings_tab.shortcut_conflicts.text()
        assert "Select previous result" in text
        assert "Select next result" in text
    finally:
        actions.reset_all()


def test_reset_restores_the_editors(settings_tab) -> None:
    from core import actions
    from core.actions import Binding

    actions.set_binding("results.up", Binding("key", 0x5A, 0, "Z"))
    settings_tab._shortcut_buttons["results.up"].set_binding(actions.binding_for("results.up"))

    settings_tab.reset_shortcuts_btn.click()
    assert actions.binding_for("results.up") == actions.BY_ID["results.up"].default


def test_identify_without_hardware_does_not_crash(settings_tab) -> None:
    settings_tab.s4_identify_btn.setChecked(True)
    assert settings_tab.s4_identify_btn.isChecked() is False
    assert "not connected" in settings_tab.s4_learn_lbl.text().lower()


def test_last_tab_is_remembered(settings_tab, tmp_path) -> None:
    tw = settings_tab._tab_widget
    target = tw.count() - 1
    tw.setCurrentIndex(target)
    name = tw.tabText(target)

    from ui.settings import SettingsTab

    reopened = SettingsTab()
    try:
        rw = reopened._tab_widget
        assert rw.tabText(rw.currentIndex()) == name
    finally:
        reopened.deleteLater()


def test_the_traktor_tab_is_in_sections(settings_tab) -> None:
    """Ordered: which Traktor -> its preferences -> the connection."""
    from PySide6.QtWidgets import QGroupBox

    page = settings_tab._tab_pages["Traktor"]
    order = [g.title() for g in page.findChildren(QGroupBox)]
    wanted = ["TRAKTOR", "PREFERENCES", "CONNECTION"]
    assert [g for g in order if g in wanted] == wanted


def test_the_s4_sits_with_the_shortcuts_it_drives(settings_tab) -> None:
    """Enabling the S4 and binding its controls belong on one tab: the
    S4 MK2 column of the shortcut table is dead until the device is on."""
    from PySide6.QtWidgets import QGroupBox

    traktor = settings_tab._tab_pages["Traktor"]
    assert "HARDWARE CONTROL" not in [g.title() for g in traktor.findChildren(QGroupBox)]

    page = settings_tab._tab_pages["Controls"]
    order = [g.title() for g in page.findChildren(QGroupBox)]
    assert [g for g in order if g in ("HARDWARE CONTROL", "SHORTCUTS")] == [
        "HARDWARE CONTROL",
        "SHORTCUTS",
    ]


def test_the_d2_controller_readout_exists(settings_tab) -> None:
    """Whether Traktor has the D2 device is the thing that silently breaks
    the bridge, so the screen has to say."""
    assert settings_tab.d2_device_msg.text()
    assert settings_tab.d2_patch_btn.isEnabled()
    assert settings_tab.d2_restore_btn is not None


# ── Verbose logging ─────────────────────────────────────────────────────────


def test_verbose_logging_is_off_by_default(settings_tab) -> None:
    assert not settings_tab.verbose_log_cb.isChecked()


def test_verbose_logging_applies_at_once_and_persists(settings_tab, monkeypatch) -> None:
    import logging

    from PySide6.QtCore import QSettings

    monkeypatch.setattr(logging.root, "setLevel", lambda lvl: levels.append(lvl))
    levels: list[int] = []
    settings_tab.verbose_log_cb.setChecked(True)
    assert levels[-1] == logging.DEBUG
    assert QSettings().value("verbose_log", False, type=bool) is True
    settings_tab.verbose_log_cb.setChecked(False)
    assert levels[-1] == logging.INFO


# ── Level 1: which features a scan computes ─────────────────────────────────


def test_the_matching_tab_lists_every_computed_feature(settings_tab) -> None:
    from core.scan_plan import computable_features

    assert set(settings_tab.compute_boxes) == {f.key for f in computable_features()}


def test_a_feature_is_computed_by_default(settings_tab) -> None:
    assert all(cb.isChecked() for cb in settings_tab.compute_boxes.values())


def test_turning_one_off_persists(settings_tab) -> None:
    from PySide6.QtCore import QSettings

    from core.features import axis_compute_key

    key = next(iter(settings_tab.compute_boxes))
    settings_tab.compute_boxes[key].setChecked(False)
    assert QSettings().value(axis_compute_key(key), True, type=bool) is False


def test_the_group_shows_what_a_scan_would_cost(settings_tab) -> None:
    """Switching a heavy feature off is the point — the label has to say so."""
    text = settings_tab.compute_summary.text().lower()
    assert "feature" in text


def test_traktor_metadata_is_not_offered_as_computable(settings_tab) -> None:
    """BPM, key, rating and genre are read from the NML on every scan. There
    is nothing to switch off, and switching them off would suggest a track
    could lack them."""
    for key in ("bpm", "key", "rating", "genre"):
        assert key not in settings_tab.compute_boxes


def test_the_transpose_range_is_settable(settings_tab) -> None:
    from PySide6.QtCore import QSettings

    from core.features import key_range_key

    settings_tab.transpose_spin.setValue(2)
    assert int(QSettings().value(key_range_key(), 3)) == 2


def test_the_transpose_range_starts_at_three(settings_tab) -> None:
    assert settings_tab.transpose_spin.value() == 3


def test_the_range_row_explains_what_it_is_for(settings_tab) -> None:
    text = settings_tab.transpose_note.text().lower()
    assert "key" in text and "semitone" in text


def test_every_tab_scrolls_vertically_when_needed(settings_tab) -> None:
    """Opened inside the docked window, a tab can be taller than the space."""
    tw = settings_tab._tab_widget
    for i in range(tw.count()):
        area = tw.widget(i)
        assert isinstance(area, QScrollArea), tw.tabText(i)
        assert area.widgetResizable()
        assert area.verticalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAsNeeded
        assert area.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff


# ── UI lag logging (watchdog) ───────────────────────────────────────────────


def test_ui_lag_logging_is_off_by_default(settings_tab) -> None:
    assert not settings_tab.ui_watchdog_cb.isChecked()


def test_ui_lag_logging_applies_at_once_and_persists(settings_tab, monkeypatch) -> None:
    from PySide6.QtCore import QSettings

    from ui import ui_watchdog

    monkeypatch.delenv("TK_WATCHDOG", raising=False)
    calls: list[bool] = []
    monkeypatch.setattr(ui_watchdog, "set_ui_watchdog", calls.append)
    settings_tab.ui_watchdog_cb.setChecked(True)
    assert calls == [True]
    assert QSettings().value(ui_watchdog.SETTING_KEY, False, type=bool) is True
    settings_tab.ui_watchdog_cb.setChecked(False)
    assert calls == [True, False]
