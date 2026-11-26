"""SettingsTab — the tabbed settings page.

This module owns the tab's frame: the signals, the tab bar, the AutoMix,
matching and result-column widgets, and restoring saved values. Each page
of settings lives in its own ``ui.settings_*`` module as a mixin that
``SettingsTab`` inherits, so the file stays readable while every page still
shares the one ``self`` (widgets, ``QSettings``, the install picker).

The names other code imports from here (``SettingsTab``,
``SYNC_ALL_ON_CONNECT_KEY``, ``bridge_stats_text``) remain importable from
``ui.settings``.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QSettings, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from core.features import MAX_TRANSPOSE_RANGE, key_range_key
from core.prefs import BPM_WINDOW_PCT, KEEP_DISPLAY_AWAKE, RAMP_BARS, VERBOSE_LOG
from ui.about_page import about_groups
from ui.now_playing import RESULT_COLUMNS, rescol_key
from ui.settings_common import (
    D2_EXPECTED_VERSION,
    ELEVATION_NOTE,
    SYNC_ALL_ON_CONNECT_KEY,
    bridge_stats_text,
)
from ui.settings_d2 import _D2PageMixin
from ui.settings_d2_install import _D2InstallMixin
from ui.settings_discogs import _DiscogsPageMixin
from ui.settings_general import _GeneralPageMixin
from ui.settings_hardware import _HardwarePageMixin
from ui.settings_matching import _MatchingPageMixin
from ui.settings_permissions import _PermissionsPageMixin
from ui.settings_problems import _ProblemsPageMixin
from ui.settings_shortcuts import _ShortcutsPageMixin
from ui.settings_spotify import _SpotifyApiMixin
from ui.settings_traktor import _TraktorPageMixin
from ui.style import T, make_tab_widget, scroll_page
from ui.widgets import TOOLTIP_MS_DEFAULT

__all__ = [
    "D2_EXPECTED_VERSION",
    "ELEVATION_NOTE",
    "SYNC_ALL_ON_CONNECT_KEY",
    "SettingsTab",
    "bridge_stats_text",
]


log = logging.getLogger(__name__)


class SettingsTab(
    _ProblemsPageMixin,
    _GeneralPageMixin,
    _ShortcutsPageMixin,
    _HardwarePageMixin,
    _PermissionsPageMixin,
    _TraktorPageMixin,
    _D2PageMixin,
    _D2InstallMixin,
    _MatchingPageMixin,
    _DiscogsPageMixin,
    _SpotifyApiMixin,
    QWidget,
):
    s4mk2_config_changed = Signal()
    matching_config_changed = Signal()  # BPM-restrict / harmonic filter → re-rank
    columns_config_changed = Signal()  # result-list shown columns changed
    reconnect_requested = Signal()  # rebind the webhook + ask QML to resync
    tooltip_config_changed = Signal()  # result-tooltip lifetime changed
    traktor_config_changed = Signal()  # the selected Traktor install changed

    def __init__(self, parent=None):
        super().__init__(parent)
        self._settings = QSettings()

        # Timer: mark Traktor disconnected after 5 s with no ping
        self._traktor_timeout = QTimer(self)
        self._traktor_timeout.setSingleShot(True)
        self._traktor_timeout.setInterval(5000)
        self._traktor_timeout.timeout.connect(lambda: self.set_traktor_active(False))

        # Timer: re-check macOS permission grants every 3 s while settings
        # are open — both the Accessibility and Screen Recording rows.
        self._perm_poll = QTimer(self)
        self._perm_poll.setInterval(3000)
        self._perm_poll.timeout.connect(self._refresh_permissions)

        self._build_ui()
        self._restore()

    # Tab order. Groups declare which tab they belong to via ``self._tab(...)``;
    # this list fixes the order they appear in regardless of build order.
    # Tagging (Discogs, Spotify) lives in Traktor: seven tabs shortened the
    # labels ("Gene…"); five of TK's own, an add-on's pages before About.
    _TAB_ORDER = ("General", "Controls", "Matching", "Traktor", "About")

    _LAST_TAB_KEY = "settings_last_tab"
    #: Inside Traktor Settings is one band button whose page keeps these tabs (and an add-on's
    #: pages that ask for it); About is a band button of its own (User, 2026-11-24).
    TRAKTOR_TABS = ("Matching", "AutoMix", "Tagging")

    def _tab(self, name: str) -> QVBoxLayout:
        """The content layout for a named tab, created on first use."""
        lay = self._tabs_by_name.get(name)
        if lay is None:
            page = QWidget()
            lay = QVBoxLayout(page)
            lay.setSpacing(12)
            lay.setContentsMargins(16, 16, 16, 16)
            self._tabs_by_name[name] = lay
            self._tab_pages[name] = page
        return lay

    def _build_ui(self) -> None:
        # Settings outgrew a single scrolling column: seven stacked group boxes
        # meant hunting for anything. Each group now declares its tab and the
        # widget is assembled at the end, so adding a group is a one-line
        # change rather than a layout rewrite.
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self._tab_widget = make_tab_widget()
        outer.addWidget(self._tab_widget)
        self._tabs_by_name: dict[str, QVBoxLayout] = {}
        self._tab_pages: dict[str, QWidget] = {}

        # ── AutoMix ───────────────────────────────────────────────────────────
        auto_grp = QGroupBox("AUTOMIX")
        auto_outer = QHBoxLayout(auto_grp)
        auto_outer.setSpacing(16)

        auto_left = QVBoxLayout()
        auto_left.setSpacing(8)

        trans_row = QHBoxLayout()
        trans_row.setSpacing(8)
        trans_lbl = QLabel("Transition length:")
        trans_lbl.setStyleSheet(f"color: {T.TEXT_DIM};")
        trans_row.addWidget(trans_lbl)

        self.ramp_bars_combo = QComboBox()
        for bars in (2, 4, 8, 16, 32):
            self.ramp_bars_combo.addItem(f"{bars} bars", bars)
        self.ramp_bars_combo.setFixedWidth(90)
        self.ramp_bars_combo.setToolTip(
            "Duration of the AutoMix volume ramp, measured in bars\n"
            "and timed to the current master BPM."
        )
        self.ramp_bars_combo.currentIndexChanged.connect(self._on_ramp_bars_changed)
        trans_row.addWidget(self.ramp_bars_combo)
        trans_row.addStretch()
        auto_left.addLayout(trans_row)

        auto_outer.addLayout(auto_left, stretch=1)

        auto_note = QLabel("Select the AutoMix mode with the ▲ button in the main window.")
        auto_note.setWordWrap(True)
        auto_note.setAlignment(Qt.AlignmentFlag.AlignTop)
        auto_note.setStyleSheet(f"color: {T.TEXT_DIM}; font-size: 11px;")
        auto_outer.addWidget(auto_note, stretch=1)

        self._tab("General").addWidget(auto_grp)
        self._automix_group = auto_grp

        # ── Matching ──────────────────────────────────────────────────────────
        match_grp = QGroupBox("MATCHING")
        match_lay = QVBoxLayout(match_grp)
        match_lay.setSpacing(8)

        bpm_row = QHBoxLayout()
        bpm_row.setSpacing(8)
        self.bpm_restrict_cb = QCheckBox("Restrict BPM")
        self.bpm_restrict_cb.stateChanged.connect(self._on_bpm_restrict_changed)
        bpm_row.addWidget(self.bpm_restrict_cb)
        self.bpm_slider = QSlider(Qt.Orientation.Horizontal)
        self.bpm_slider.setRange(1, 20)
        self.bpm_slider.setValue(10)
        self.bpm_slider.setFixedWidth(180)
        self.bpm_slider.setEnabled(False)
        self.bpm_slider.valueChanged.connect(self._on_bpm_slider_changed)
        bpm_row.addWidget(self.bpm_slider)
        self.bpm_pct_lbl = QLabel("—")
        self.bpm_pct_lbl.setStyleSheet(f"color: {T.TEXT_DIM}; min-width: 40px;")
        self.bpm_pct_lbl.setEnabled(False)
        bpm_row.addWidget(self.bpm_pct_lbl)
        bpm_row.addStretch()
        match_lay.addLayout(bpm_row)

        bpm_note = QLabel(
            "Only suggest tracks within this tempo window of the current "
            "track (octave-aware). Off = match any tempo."
        )
        bpm_note.setWordWrap(True)
        # Indented to line up with the checkbox label rather than its indicator.
        bpm_note.setStyleSheet(f"color: {T.TEXT_DIM}; font-size: 11px; margin-left: 24px;")
        match_lay.addWidget(bpm_note)

        self.harmonic_only_cb = QCheckBox("Harmonic keys only")
        self.harmonic_only_cb.stateChanged.connect(self._on_harmonic_only_changed)
        match_lay.addWidget(self.harmonic_only_cb)

        harm_note = QLabel(
            "Hide tracks outside the current track's Camelot mixing set "
            "(same key, ±1 on the wheel, relative major/minor). Off = "
            "every key is suggested, with closer keys ranked higher."
        )
        harm_note.setWordWrap(True)
        harm_note.setStyleSheet(f"color: {T.TEXT_DIM}; font-size: 11px; margin-left: 24px;")
        match_lay.addWidget(harm_note)

        tr_row = QHBoxLayout()
        tr_row.setSpacing(8)
        tr_lbl = QLabel("Transpose up to:")
        tr_lbl.setStyleSheet(f"color: {T.TEXT_DIM};")
        tr_row.addWidget(tr_lbl)
        self.transpose_spin = QSpinBox()
        self.transpose_spin.setRange(1, MAX_TRANSPOSE_RANGE)
        self.transpose_spin.setSuffix(" semitones")
        self.transpose_spin.setValue(int(self._settings.value(key_range_key(), 3)))
        self.transpose_spin.valueChanged.connect(self._on_transpose_range_changed)
        tr_row.addWidget(self.transpose_spin)
        tr_row.addStretch()
        match_lay.addLayout(tr_row)

        self.transpose_note = QLabel(
            "How far the Key card's \"Key-T\" mode may move Traktor's Key knob "
            "to make a track fit. A semitone is seven places round the Camelot "
            "wheel; each one costs a little, so a track that already fits still "
            "wins. Loading a match sets the knob for you."
        )
        self.transpose_note.setWordWrap(True)
        self.transpose_note.setStyleSheet(
            f"color: {T.TEXT_DIM}; font-size: 11px; margin-left: 24px;"
        )
        match_lay.addWidget(self.transpose_note)

        self._tab("Matching").addWidget(match_grp)
        self._compute_group = self._build_compute_group()
        self._tab("Matching").addWidget(self._compute_group)

        # ── Result columns ────────────────────────────────────────────────────
        cols_grp = QGroupBox("RESULT COLUMNS")
        cols_lay = QVBoxLayout(cols_grp)
        cols_lay.setSpacing(8)
        cols_note = QLabel(
            "Informational (non-matching) columns to show in the "
            "results list. Matching dimensions are chosen in the "
            "Metrics dashboard."
        )
        cols_note.setWordWrap(True)
        cols_note.setStyleSheet(f"color: {T.TEXT_DIM}; font-size: 11px;")
        cols_lay.addWidget(cols_note)

        grid = QGridLayout()
        grid.setHorizontalSpacing(28)
        grid.setVerticalSpacing(6)
        self._col_checks: dict = {}
        for i, (cid, hdr, _w, dflt, _grp) in enumerate(RESULT_COLUMNS):
            cb = QCheckBox(hdr.title())
            cb.setChecked(self._settings.value(rescol_key(cid), dflt, type=bool))
            cb.toggled.connect(lambda chk, k=cid: self._on_col_toggled(k, chk))
            self._col_checks[cid] = cb
            grid.addWidget(cb, i // 3, i % 3)
        cols_lay.addLayout(grid)
        self._tab("Matching").addWidget(cols_grp)
        self._result_columns_group = cols_grp

        # ── Keyboard shortcuts ─────────────────────────────────────────────────

        # ── Status ────────────────────────────────────────────────────────────
        tab = self._tab("Traktor")
        tab.addWidget(self._build_install_group())
        tab.addWidget(self._build_traktor_prefs_group())
        tab.addWidget(self._build_connection_group())

        # Accessibility & Screen Recording are macOS TCC grants; they live as
        # two extra rows inside the Traktor tab's CONNECTION group, since they
        # gate acting on Traktor just as much as the mapping does.

        self._tab("General").insertWidget(0, self._build_problems_group())
        self._tab("General").addWidget(self._build_general_group())
        # The S4 lives with the shortcuts it drives: enabling the device and
        # binding its controls used to sit on two different tabs.
        self._hw_group = self._build_hw_control_group()
        self._tab("Controls").addWidget(self._hw_group)
        self._tab("Controls").addWidget(self._build_shortcuts_group())
        self._build_discogs_groups()
        spotify = self._build_spotify_api_group()
        self._metadata_groups.append(spotify)
        self._tab("Traktor").addWidget(spotify)
        self._build_extension_pages()
        for grp in about_groups():
            self._tab("About").addWidget(grp)
        self._assemble_tabs()
        from core.extensions import addon_shown

        if not addon_shown():  # an add-on hidden from the start (its own setting)
            self.show_extension_tabs(False)

    def _build_extension_pages(self) -> None:
        """An add-on's pages (core.extensions), before About, and its rows. One that fails is left
        out."""
        from core.extensions import settings_pages, settings_rows

        for page in settings_pages():
            try:
                widget = page.build(self)
            except Exception:
                log.exception("settings: the %s page could not be built", page.title)
                continue
            self._tab(page.title).addWidget(widget)
        # An add-on's rows: at the bottom of the Traktor tab here, of Tagging inside Traktor
        # (fold_for_traktor moves them).
        self._extension_rows = []
        for build in settings_rows():
            try:
                row = build(self)
            except Exception:
                log.exception("settings: an add-on's row could not be built")
                continue
            self._extension_rows.append(row)
            self._tab("Traktor").addWidget(row)

    def show_extension_tabs(self, shown: bool) -> None:
        """An add-on shown or hidden (core.extensions.addon_shown): its tabs follow at once."""
        from core.extensions import settings_pages

        bar = self._tab_widget
        own = {page.title for page in settings_pages()}
        inside = hasattr(self, "_traktor_tab")
        for i in range(bar.count()):
            name = bar.tabText(i)
            if name in own:
                bar.setTabVisible(i, shown and (not inside or name in self._traktor_tabs()))

    def _tab_order(self) -> tuple[str, ...]:
        from core.extensions import settings_pages

        own = tuple(name for name in self._TAB_ORDER if name != "About")
        return (*own, *(page.title for page in settings_pages()), "About")

    def _traktor_tabs(self) -> tuple[str, ...]:
        from core.extensions import addon_shown, settings_pages

        pages = settings_pages() if addon_shown() else ()
        return (*self.TRAKTOR_TABS, *(page.title for page in pages if page.in_traktor))

    def fold_for_traktor(self) -> None:
        """Inside Traktor there are no General, Controls and Traktor pages (User, 2026-11-24):
        their install, connection, interface and shortcut rows do not apply there. AutoMix and
        the metadata set-up groups (Discogs account, filling Traktor, Spotify) get tabs of their
        own after Matching (User, 2026-11-24). The tab row then shows TRAKTOR_TABS only
        (show_traktor_settings)."""
        # Results show in Traktor's own list there, with Traktor's columns; every feature is
        # computed, the default (core.feature_bits.enabled_mask) (User, 2026-11-24).
        self._result_columns_group.hide()
        self._compute_group.hide()
        after = self._tab_index("Matching") + 1
        for name, groups in (
            ("AutoMix", [self._automix_group]),
            ("Tagging", self._metadata_groups),
        ):
            lay = self._tab(name)
            for grp in groups:
                lay.addWidget(grp)
            if name == "Tagging":  # the last tab: an add-on's rows at its bottom
                for row in self._extension_rows:
                    lay.addWidget(row)
            lay.addStretch()
            self._tab_widget.insertTab(after, scroll_page(self._tab_pages[name]), name)
            after += 1
        self._traktor_tab = "Matching"
        self._left_align_forms()

    def _left_align_forms(self) -> None:
        """Forms flush left, labels too (User, 2026-11-24): macOS centres a form and right-aligns
        its labels by default, which left the rows floating mid-page across Traktor's width."""
        from PySide6.QtWidgets import QFormLayout

        for page in self._tab_pages.values():
            for form in page.findChildren(QFormLayout):
                form.setFormAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
                form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)

    def _tab_index(self, name: str) -> int:
        bar = self._tab_widget
        return next((i for i in range(bar.count()) if bar.tabText(i) == name), -1)

    def show_traktor_settings(self) -> None:
        """The band's Settings button: this page with its tab row, TRAKTOR_TABS only, on the
        tab last used there."""
        bar = self._tab_widget
        # Read first: hiding the current tab (About) moves Qt to a neighbour, which is recorded.
        want = self._tab_index(getattr(self, "_traktor_tab", "Matching"))
        for i in range(bar.count()):
            bar.setTabVisible(i, bar.tabText(i) in self._traktor_tabs())
        bar.tabBar().show()
        bar.setCurrentIndex(want if want >= 0 else self._tab_index("Matching"))

    def show_traktor_about(self) -> None:
        """The band's About button: the About tab alone, without the tab row."""
        bar = self._tab_widget
        about = self._tab_index("About")
        bar.setTabVisible(about, True)
        bar.tabBar().hide()
        bar.setCurrentIndex(about)

    def hide_hardware_group(self) -> None:
        """Inside Traktor the S4 MK2 is Traktor's own controller: no TK hardware control."""
        self._hw_group.hide()

    def show_tab(self, name: str) -> None:
        """Switch to a named tab (used by the problem rows)."""
        for i in range(self._tab_widget.count()):
            if self._tab_widget.tabText(i) == name:
                self._tab_widget.setCurrentIndex(i)
                return

    def _assemble_tabs(self) -> None:
        """Add the built pages to the tab bar in a fixed order."""
        order = self._tab_order()
        for name in order:
            page = self._tab_pages.get(name)
            if page is None:
                continue  # nothing declared this tab
            self._tabs_by_name[name].addStretch()
            self._tab_widget.addTab(scroll_page(page), name)
        # Any tab a future group invents without updating _TAB_ORDER still
        # shows up, rather than silently vanishing.
        for name, page in self._tab_pages.items():
            if name not in order:
                self._tabs_by_name[name].addStretch()
                self._tab_widget.addTab(scroll_page(page), name)

        want = self._settings.value(self._LAST_TAB_KEY, "", type=str)
        for i in range(self._tab_widget.count()):
            if self._tab_widget.tabText(i) == want:
                self._tab_widget.setCurrentIndex(i)
                break
        self._tab_widget.currentChanged.connect(self._on_tab_changed)

    def _on_tab_changed(self, idx: int) -> None:
        name = self._tab_widget.tabText(idx)
        self._settings.setValue(self._LAST_TAB_KEY, name)
        if hasattr(self, "_traktor_tab") and name in self._traktor_tabs():
            self._traktor_tab = name  # inside Traktor: Settings opens on it next time
        if name == "Traktor":
            self._refresh_d2_device()
            self._offer_repatch()

    # ── Logic ─────────────────────────────────────────────────────────────────

    def _restore(self) -> None:
        self.s4mk2_cb.setChecked(self._settings.value("s4mk2_enabled", False, type=bool))
        if self.traktor_autostart_cb is not None:
            self.traktor_autostart_cb.setChecked(
                self._settings.value("traktor_autostart", False, type=bool)
            )
        self.sync_all_cb.setChecked(self._settings.value(SYNC_ALL_ON_CONNECT_KEY, False, type=bool))
        if self.keep_awake_cb is not None:
            self.keep_awake_cb.setChecked(KEEP_DISPLAY_AWAKE.get(self._settings))
        # The collection used to be a free-form path of its own; it now comes
        # from the selected install, so drop any override left in the store.
        self._settings.remove("nml_path")

        self.verbose_log_cb.blockSignals(True)
        self.verbose_log_cb.setChecked(VERBOSE_LOG.get(self._settings))
        self.verbose_log_cb.blockSignals(False)
        from ui.ui_watchdog import SETTING_DEFAULT as _WD_DEFAULT
        from ui.ui_watchdog import SETTING_KEY as _WD_KEY

        self.ui_watchdog_cb.blockSignals(True)
        self.ui_watchdog_cb.setChecked(self._settings.value(_WD_KEY, _WD_DEFAULT, type=bool))
        self.ui_watchdog_cb.blockSignals(False)
        from core.update_check import SETTING_DEFAULT as _UPDATE_DEFAULT

        self.update_check_cb.blockSignals(True)
        self.update_check_cb.setChecked(
            self._settings.value(self._update_key, _UPDATE_DEFAULT, type=bool)
        )
        self.update_check_cb.blockSignals(False)
        tip_ms = int(self._settings.value("results_tooltip_ms", TOOLTIP_MS_DEFAULT))
        tip_idx = self.tooltip_combo.findData(tip_ms)
        self.tooltip_combo.blockSignals(True)
        self.tooltip_combo.setCurrentIndex(
            tip_idx if tip_idx >= 0 else self.tooltip_combo.findData(TOOLTIP_MS_DEFAULT)
        )
        self.tooltip_combo.blockSignals(False)
        bars = RAMP_BARS.get(self._settings)
        idx = next(
            (
                i
                for i in range(self.ramp_bars_combo.count())
                if self.ramp_bars_combo.itemData(i) == bars
            ),
            1,  # default index → 4 bars
        )
        self.ramp_bars_combo.setCurrentIndex(idx)

        pct = BPM_WINDOW_PCT.get(self._settings)
        self.bpm_slider.setValue(max(1, min(20, pct)))
        self.bpm_restrict_cb.setChecked(self._settings.value("bpm_restrict", False, type=bool))
        self._update_bpm_label()
        self.harmonic_only_cb.setChecked(self._settings.value("harmonic_only", False, type=bool))
