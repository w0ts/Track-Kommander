"""NowPlayingTab — shows current Traktor track and best harmonic matches.

The tab was one file of about 3,500 lines; it is now the widget itself (its
signals, ``__init__`` and ``_build_ui``, the deck picker and the source track)
plus mixins that hold the concerns sharing its state: ``np_auto`` (automation
and Auto DJ), ``np_axis_cards`` / ``np_axis_state`` (matching axes),
``np_results`` / ``np_columns`` (ranking, filter, paging, columns),
``np_actions`` (row menu and loading) and ``np_deck_api`` (deck updates from
Traktor). Widgets and pure helpers moved to the other ``np_*`` modules.

Every name that moved is imported back here and listed in ``__all__``, so
``from ui.now_playing import X`` keeps working. Tests that monkeypatch a moved
name must patch it in the module whose code reads it, not here.
"""

from __future__ import annotations

import logging
import sqlite3
import sys

from PySide6.QtCore import QSettings, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from core.grid_fix import GridQueue, PhaseTracker, grid_queue_path
from core.matcher import find_track, find_track_by_path
from core.prefs import AUTOMATION_MODE
from ui.icons import glyph_icon
from ui.np_actions import _ActionsMixin
from ui.np_auto import _AutoModeMixin
from ui.np_axis_cards import _AxisCardsMixin
from ui.np_axis_state import _AxisStateMixin
from ui.np_columns import _ColumnsMixin
from ui.np_constants import (
    _AUTO_MODES,
    _DECK_LETTERS,
    _DEFAULT_LOAD_METHOD,
    _FILTER_DEBOUNCE_MS,
    _PAGE,
    _SIDE_ICON_PX,
    NO_TRANSPOSE,
)
from ui.np_cover_handle import _DragHandle, _load_album_art
from ui.np_deck_api import _DeckApiMixin
from ui.np_decks import AutoModeButton, DeckButton, _DeckGrid
from ui.np_flow_layout import _FlowLayout
from ui.np_format import (
    _COL_TIPS,
    RESULT_COLUMNS,
    _beats,
    _bpm_display,
    _flow_columns,
    _get_f,
    _get_s,
    _set_qss,
    _set_tip,
    _to_openkey,
    info_line_html,
    key_cell_text,
    live_source_bpm,
    rescol_key,
    sounding_key,
    still_snapped,
    transposed_key_text,
    widen_stored_columns,
)
from ui.np_results import _ResultsMixin
from ui.np_tooltip import _row_tooltip, _tooltip_duration_ms
from ui.style import T
from ui.widgets import MetricCellDelegate, ResultsTable, ScoreBarDelegate
from workers.analysis import AnalysisWorker
from workers.webhook import traktor_search

_log = logging.getLogger(__name__)

__all__ = [
    "NowPlayingTab",
    # np_constants
    "NO_TRANSPOSE",
    "_PAGE",
    # np_format
    "RESULT_COLUMNS",
    "_COL_TIPS",
    "_beats",
    "_bpm_display",
    "_flow_columns",
    "_get_f",
    "_get_s",
    "_set_qss",
    "_set_tip",
    "_to_openkey",
    "info_line_html",
    "key_cell_text",
    "live_source_bpm",
    "rescol_key",
    "sounding_key",
    "still_snapped",
    "transposed_key_text",
    "widen_stored_columns",
    # np_flow_layout
    "_FlowLayout",
    # np_cover_handle
    "_DragHandle",
    "_load_album_art",
    # np_decks
    "AutoModeButton",
    "DeckButton",
    "_DeckGrid",
    # np_tooltip
    "_row_tooltip",
    "_tooltip_duration_ms",
]


class _EscapeLineEdit(QLineEdit):
    """QLineEdit that releases keyboard focus on Escape."""

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.clearFocus()
            event.accept()
            return
        super().keyPressEvent(event)


class NowPlayingTab(
    _AutoModeMixin,
    _AxisCardsMixin,
    _AxisStateMixin,
    _ResultsMixin,
    _ColumnsMixin,
    _ActionsMixin,
    _DeckApiMixin,
    QWidget,
):
    # path, deck, skip_load, transpose (semitones, -99 = leave the knob alone)
    load_to_deck_requested = Signal(str, str, bool, int)
    stop_deck_requested = Signal(str)  # deck letter
    play_deck_requested = Signal(str)  # deck letter
    cancel_automix_requested = Signal()  # abort any in-progress transition
    hotcue_requested = Signal(str, int)  # deck letter, hotcue index 1–8
    beatjump_requested = Signal(str, int)  # deck letter, JumpSize enum value
    open_library_requested = Signal()
    open_settings_requested = Signal()
    open_stats_requested = Signal()
    recompute_hotcues_requested = Signal(str)  # path — redo one track's hotcues
    discogs_lookup_requested = Signal(str)  # path — ask Discogs about one track
    grid_fix_captured = Signal()  # a beat-grid offset was queued
    notice_requested = Signal(str, str)  # text, severity ("warning"/"error")
    start_traktor_requested = Signal()  # cover double-clicked, Traktor closed
    snap_changed = Signal()  # docked geometry recorded/cleared
    minimize_requested = Signal()  # shrink to the floating button
    results_changed = Signal()  # the ranked/filtered result list was rebuilt
    source_deck_changed = Signal(str)  # "A".."D", or "" when no deck is the source
    auto_state_changed = Signal(str, bool)  # automation mode key, Auto DJ transition running

    def __init__(self, parent=None):
        super().__init__(parent)
        self._source: sqlite3.Row | None = None
        # (deck, file_path) the source track was last resolved from. On-air and
        # play-state pings re-run _auto_select_active_deck several times a
        # second; without this key an unchanged deck would redo the DB lookup
        # (and, for a track that isn't in the library, rebuild the whole table)
        # on every ping.
        self._source_key: tuple[str, str] | None = None
        #: Load the source track's cover for the drag handle. Off inside Traktor, where TK's
        #: window (and the handle) never shows: reading the first cover imports PyAV, which
        #: froze Traktor's UI for ~0.7 s at the first play (measured 2026-11-24).
        self.show_cover_art = True
        # Query that put the table into "browse the whole library" mode, so a
        # repeated miss for the same track is a no-op instead of a rebuild.
        self._browse_query: str | None = None
        # True while _apply_filter re-selects the previously selected row —
        # suppresses the Traktor pre-search that a user-driven selection fires.
        self._restoring_selection: bool = False
        self._playing_decks: set[str] = set()
        self._deck_info: dict[str, dict] = {}  # deck → {title, artist, bpm, key, file_path, …}
        self._deck_volume: dict[str, float] = {}  # deck → last known fader volume
        self._deck_progress: dict[str, float] = {}  # deck → playhead position 0.0–1.0
        # Beat-grid capture: the phase readings the bridge sends, and the
        # offsets the user has captured from them. See :mod:`core.grid_fix`.
        self._phases = PhaseTracker()
        self._grid_queue = GridQueue(grid_queue_path())
        self._auto_loaded_deck: str | None = None  # deck we loaded via Enter
        self._auto_loaded_path: str | None = None  # path we sent to that deck
        self._auto_selected_deck: str | None = None  # last deck chosen by auto-select
        # Last deck the user (or Auto DJ) loaded a track into. Hotcue-key
        # shortcuts (1-8) target this deck.
        self._last_loaded_deck: str | None = None
        self._master_bpm: float = 0.0  # live master clock BPM from Traktor
        # Key-knob position per deck, in semitones, as reported by the bridge.
        self._deck_key_adjust: dict[str, int] = {}
        self._deck_tempo: dict[str, float] = {}  # BPM each deck plays at
        self._src_bpm_effective: float = 0.0  # master BPM if playing, else DB BPM
        # Lower-cased search text per track path, valid for the current
        # _all_results only (the property setter clears it).
        self._search_text_cache: dict[str, str] = {}
        self._all_results: list[dict] = []
        self._filtered_results: list[dict] = []
        self._filter_text: str = ""
        # What the filter box holds but has not been applied yet (debounced).
        self._pending_filter_text: str = ""
        self._reanalyse_worker: AnalysisWorker | None = None
        self._loaded: int = 0
        self._settings = QSettings()
        # Key now carries "12m+1 3" and BPM "133 +11.1%"; both defaults went
        # up, which only reaches an existing install through this.
        widen_stored_columns(self._settings, {"m:key": 104, "m:bpm": 124}, flag="key-distance")
        # Flush settings to disk shortly after the last change, and on quit, so
        # matching slider values survive an abrupt quit (macOS can otherwise
        # drop un-synced preferences when the app is force-closed).
        self._persist_timer = QTimer(self)
        self._persist_timer.setSingleShot(True)
        self._persist_timer.setInterval(600)
        self._persist_timer.timeout.connect(self._settings.sync)
        from PySide6.QtWidgets import QApplication

        _app = QApplication.instance()
        if _app is not None:
            _app.aboutToQuit.connect(self._settings.sync)
        self._drag_pos = None

        self._auto_mode_idx: int = 1  # default: play_cue

        # Auto DJ state
        self._session_played: set[str] = set()  # paths with ≥60s of play this session
        self._auto_loading: bool = False  # transition in progress
        self._deck_play_start: dict[str, float] = {}  # deck → wall-clock start time

        # Per-track cumulative play seconds in this session. A track crosses the
        # PLAYED_THRESHOLD_S threshold → added to _session_played → row dims.
        self._track_play_seconds: dict[str, float] = {}

        self._auto_timer = QTimer(self)
        self._auto_timer.setInterval(1000)
        self._auto_timer.timeout.connect(self._auto_mode_tick)

        # Tick every 5s to accumulate playtime for each playing deck's loaded track.
        self._play_tick = QTimer(self)
        self._play_tick.setInterval(5000)
        self._play_tick.timeout.connect(self._tick_play_seconds)
        self._play_tick.start()

        # Releases the Auto DJ lock once a transition has had time to finish.
        # One timer, restarted per transition and stopped on cancel, so the
        # deadline of a cancelled transition cannot unlock the next one early.
        self._auto_unlock_timer = QTimer(self)
        self._auto_unlock_timer.setSingleShot(True)
        self._auto_unlock_timer.timeout.connect(self._on_auto_transition_done)

        self._search_debounce = QTimer(self)
        self._search_debounce.setSingleShot(True)
        self._search_debounce.setInterval(200)
        self._search_debounce.timeout.connect(self._fire_traktor_search)

        # Typing in the filter box: filter once per pause, not per keystroke.
        self._filter_debounce = QTimer(self)
        self._filter_debounce.setSingleShot(True)
        self._filter_debounce.setInterval(_FILTER_DEBOUNCE_MS)
        self._filter_debounce.timeout.connect(self._apply_search_text)

        self._tempo_debounce = QTimer(self)
        self._tempo_debounce.setSingleShot(True)
        self._tempo_debounce.setInterval(5000)
        self._tempo_debounce.timeout.connect(self._refresh)

        # Dragging an axis slider emits valueChanged per tick, and a re-rank now
        # scores the whole library rather than a pre-filtered handful — on a 10k
        # collection that is ~90 ms, which would drop the drag to ~10 fps. Coalesce
        # the ticks so at most one rank runs per pause in the gesture.
        self._rerank_debounce = QTimer(self)
        self._rerank_debounce.setSingleShot(True)
        self._rerank_debounce.setInterval(90)
        self._rerank_debounce.timeout.connect(self._refresh)
        self._build_ui()
        self._restore_bpm_settings()
        self._show_library_browse()

    # ── Background drag (frameless window) ───────────────────────────────────

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = (
                event.globalPosition().toPoint() - self.window().frameGeometry().topLeft()
            )
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._drag_pos is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.window().move(event.globalPosition().toPoint() - self._drag_pos)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._drag_pos = None
        super().mouseReleaseEvent(event)

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(8)
        root.setContentsMargins(16, 16, 16, 16)

        # ── Deck grid: A/B on top, C/D below (mirrors Traktor layout) ─────────
        _icon_btn_style = (
            f"QPushButton {{ font-size: 22px; padding: 0;"
            f"  background: transparent; border: none; color: {T.TEXT_DIM}; }}"
            f"QPushButton:hover {{ color: {T.TEXT}; background: {T.BTN_HOVER};"
            f"  border-radius: 6px; }}"
            f"QPushButton:pressed {{ color: #fff; background: {T.BLUE};"
            f"  border-radius: 6px; }}"
        )
        # The strip's glyphs come from different fonts — a colour emoji, a
        # dingbat, a plain × — so no font size lines them up. Each is rendered
        # into the same box instead; the hover colour is the icon's Active state.
        _side_btn_style = (
            "QPushButton { padding: 0; background: transparent; border: none; }"
            f"QPushButton:hover {{ background: {T.BTN_HOVER}; border-radius: 6px; }}"
            f"QPushButton:pressed {{ background: {T.BLUE}; border-radius: 6px; }}"
        )

        def _side_button(glyph: str, tooltip: str, *, family: str = "") -> QPushButton:
            btn = QPushButton()
            btn.setFixedSize(36, 28)
            btn.setIcon(glyph_icon(glyph, T.TEXT_DIM, T.TEXT, box=_SIDE_ICON_PX, family=family))
            btn.setIconSize(QSize(_SIDE_ICON_PX, _SIDE_ICON_PX))
            btn.setToolTip(tooltip)
            btn.setStyleSheet(_side_btn_style)
            return btn

        lib_btn = _side_button("♫", "Library")
        lib_btn.clicked.connect(self.open_library_requested.emit)

        # Merged Metrics + Library-stats button: opens the stats dashboard, whose
        # "discriminative features" list also enables/disables matching metrics.
        metrics_btn = _side_button(
            "▦",
            "Metrics & library stats — distributions, correlations, "
            "and which dimensions to match on (⌘⇧S)",
        )
        metrics_btn.clicked.connect(self.open_stats_requested.emit)

        # The gear (U+2699) is drawn from the colour-emoji font (Segoe UI Emoji)
        # on Windows, clashing with the monochrome ♫ / × siblings. Pin a
        # non-colour symbol font that carries the glyph so it renders monochrome.
        # macOS already draws it monochrome, so this only applies on Windows.
        cfg_btn = _side_button(
            "⚙",
            f"Settings ({QKeySequence(QKeySequence.StandardKey.Preferences).toString(QKeySequence.SequenceFormat.NativeText)})",
            family="Segoe UI Symbol" if sys.platform == "win32" else "",
        )
        cfg_btn.clicked.connect(self.open_settings_requested.emit)

        close_btn = _side_button("×", "Close")
        close_btn.setIcon(glyph_icon("×", T.TEXT_DIM, "#ffffff", box=_SIDE_ICON_PX))
        close_btn.setStyleSheet(
            "QPushButton { padding: 0; background: transparent; border: none; }"
            "QPushButton:hover { background: #c0392b; border-radius: 6px; }"
            "QPushButton:pressed { background: #962d22; border-radius: 6px; }"
        )
        close_btn.clicked.connect(lambda: self.window().close())

        # Minimise to one small floating button, uncovering Traktor's library.
        min_btn = _side_button(
            "−",
            "Minimise — shrink to a small button and "
            "uncover Traktor's library. Click the button "
            "to bring the window back.",
        )
        min_btn.clicked.connect(self.minimize_requested.emit)

        self._deck_btns: dict[str, DeckButton] = {}
        for letter in _DECK_LETTERS:
            btn = DeckButton(f"  {letter}")
            btn.setCheckable(True)
            btn.setStyleSheet(self._deck_style(False, False, False, False))
            btn.clicked.connect(lambda checked, d=letter: self._on_deck_clicked(d, checked))
            btn.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            btn.customContextMenuRequested.connect(lambda p, d=letter: self._show_deck_menu(d, p))
            # Hovering a deck shows the same rich tooltip as a result row, for
            # that deck's loaded track (compared against the current track).
            btn._tip_provider = lambda d=letter: self._deck_tooltip(d)
            self._deck_btns[letter] = btn

        # Deck grid: [A][B] over [C][D]. It self-positions so the A│B / C│D
        # split sits on the window centre line (see _DeckGrid).
        self._deck_grid = _DeckGrid(self._deck_btns)
        self._apply_deck_rows()

        # Automation mode button — cycles off → play_cue → automix on each click.
        self.auto_btn = AutoModeButton()
        self.auto_btn.setFixedSize(36, 28)
        self.auto_btn.setIconSize(QSize(_SIDE_ICON_PX, _SIDE_ICON_PX))
        self.auto_btn.clicked.connect(self._cycle_auto_mode)
        self.auto_btn.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.auto_btn.customContextMenuRequested.connect(self._show_auto_btn_menu)

        # Current-track BPM / Key readout (sits under the cover art, left column).
        self.info_lbl = QLabel("")
        self.info_lbl.setTextFormat(Qt.TextFormat.RichText)
        self.info_lbl.setStyleSheet("font-size: 12px; padding: 2px 0;")

        # Filter box (under the BPM/Key readout, left column).
        self.search_edit = _EscapeLineEdit()
        self.search_edit.setPlaceholderText("Filter…")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self._on_search_changed)

        # ── Matching-axis cards (one framed card per enabled dimension, flowed
        #    onto as many lines as the window width allows) ──
        self._axis_widgets: dict[str, dict] = {}
        self._axis_panel = QWidget()
        # max_item_width=0 → cards stretch to fill the row edge-to-edge (no cap),
        # so the panel always uses the full available width. fill_height=True →
        # the card rows also stretch vertically to consume any slack left by a
        # taller side column (cover art / icon strip), closing the dead gap that
        # would otherwise open between the cards and the results table.
        self._axis_flow = _FlowLayout(
            self._axis_panel,
            hspacing=6,
            vspacing=6,
            min_item_width=self._CARD_MIN_W,
            fill_height=True,
        )
        _sp = self._axis_panel.sizePolicy()
        _sp.setHeightForWidth(True)
        _sp.setVerticalPolicy(QSizePolicy.Policy.Expanding)
        self._axis_panel.setSizePolicy(_sp)
        self._rebuild_axis_panel()

        self._drag_handle = _DragHandle()
        self._drag_handle.start_traktor_requested.connect(self.start_traktor_requested)
        self._drag_handle.snap_changed.connect(self.snap_changed)
        # Right-click on the cover: the app's menu on every platform. On
        # Windows the menu bar is hidden, so this is the only one there.
        self._drag_handle.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._drag_handle.customContextMenuRequested.connect(self._show_cover_menu)

        # ── LEFT column: cover art · BPM/Key · filter (cover-art width) ──
        # The filter is pinned to the BOTTOM (stretch above it) so it sits flush
        # with the results table. Any slack from a taller centre column (cards
        # wrapped onto extra rows) collects under the cover art instead of
        # opening a dead gap between the filter and the result list.
        left_col = QVBoxLayout()
        left_col.setContentsMargins(0, 0, 0, 0)
        left_col.setSpacing(6)
        left_col.addWidget(self._drag_handle)
        left_col.addWidget(self.info_lbl)
        left_col.addStretch(1)
        left_col.addWidget(self.search_edit)

        # ── CENTER column: deck grid + matching-axis cards. No trailing stretch
        #    — the axis panel itself expands to fill the column height (its rows
        #    grow vertically), so the cards reach down to the results table
        #    instead of a stretch opening a dead gap below them. ──
        center_col = QVBoxLayout()
        center_col.setSpacing(8)
        center_col.addWidget(self._deck_grid)
        center_col.addWidget(self._axis_panel, stretch=1)
        # The matching popup (ui.matching_popup) borrows the axis panel and
        # puts it back here.
        self._axis_panel_home = center_col

        # ── RIGHT column: a narrow icon strip hugging the right edge —
        # × · − · ⚙ · ♫ · auto · ▦. Kept at its natural width so the deck grid
        # spreads across the rest (A│C and B│D need not be symmetrical).
        right_col = QVBoxLayout()
        right_col.setContentsMargins(0, 0, 0, 0)
        right_col.setSpacing(6)
        for _b in (close_btn, min_btn, cfg_btn, lib_btn, self.auto_btn, metrics_btn):
            right_col.addWidget(_b, alignment=Qt.AlignmentFlag.AlignRight)
        right_col.addStretch(1)

        header = QHBoxLayout()
        header.setSpacing(12)
        header.addLayout(left_col)
        header.addLayout(center_col, stretch=1)
        header.addLayout(right_col)

        root.addLayout(header)

        # ── Results table — dynamic columns: ARTIST · ALBUM · TITLE · SCORE ·
        #    <scoring metrics, ordered by weight> · GENRE. Rebuilt whenever the
        #    active axis set or its weight order changes. ──
        self.table = ResultsTable()
        self.table.set_tooltip_duration(_tooltip_duration_ms())
        self._score_bar_delegate = ScoreBarDelegate(self.table)
        # Metric bars and genre labels are painted, not per-cell widgets.
        self.table.setItemDelegate(MetricCellDelegate(self.table))
        self.table.row_tooltip_provider = self._row_tooltip_at
        self.table.setColumnCount(0)
        self._col_model: list[dict] = []
        self._col_sig: tuple = ()
        self._building_cols = False
        hh = self.table.horizontalHeader()
        hh.setStretchLastSection(False)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setDragEnabled(True)
        self.table.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_context_menu)
        self.table.verticalScrollBar().valueChanged.connect(self._on_scroll)
        self.table.resized.connect(self._fill_viewport)
        hh.sectionResized.connect(self._on_col_resized)
        self._rebuild_table_columns()
        root.addWidget(self.table, stretch=1)

        # The per-track ML features are surfaced as a styled hover tooltip on
        # each result row (see _row_tooltip), not a bottom detail panel.

        # ── Keyboard shortcuts ────────────────────────────────────────────────
        # Load / deck / hotcue keys are no longer QShortcuts: they are bindings
        # in core.actions, dispatched from MainWindow's event filter. A
        # QShortcut resolves before the KeyPress is delivered, which is how
        # Shift+Left ended up silently shadowing beat-jump-16 — with one
        # registry, a collision like that shows up as a reported conflict
        # instead of dead code.

        # Use the selection model's signal — unlike itemSelectionChanged it is
        # not suppressed by table.blockSignals(True) used during row population.
        self.table.selectionModel().currentRowChanged.connect(
            lambda cur, _prev: self._on_result_selection_changed()
        )

    # ── Result-table selection ────────────────────────────────────────────────

    def _on_result_selection_changed(self) -> None:
        """Restart the debounce timer; actual search fires after 200 ms of quiet."""
        if self._restoring_selection:
            return  # same row re-selected after a rebuild — not a user action
        self._search_debounce.start()  # restarts if already running

    def _fire_traktor_search(self) -> None:
        """Called by the debounce timer — stops any auto-loaded deck then pre-searches Traktor."""
        self._stop_auto_deck()
        from PySide6.QtCore import QSettings

        if QSettings().value("load_method", _DEFAULT_LOAD_METHOD) == "drag":
            return  # drag mode: no keyboard injection into Traktor's search field
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return
        path = self.table._row_paths.get(rows[0].row())
        if path:
            traktor_search(f'"{path}"')  # quoted full path → exact match in Traktor browser

    # ── Deck picker helpers ───────────────────────────────────────────────────

    @staticmethod
    def _deck_style(playing: bool, on_air: bool, loaded: bool, selected: bool) -> str:
        """Build QPushButton stylesheet for a deck button.

        playing  → green text + ▶ indicator
        on_air   → dimmed orange background glow
        selected → orange border
        """
        bg = "#5a2e00" if on_air else T.BTN_BG
        hover = "#6e3800" if on_air else T.BTN_HOVER
        color = T.GREEN if playing else (T.TEXT_DIM if loaded else T.TEXT_VDIM)
        border = f"2px solid {T.ORANGE}" if selected else f"1px solid {T.TEXT_VDIM}"
        return (
            f"QPushButton {{ color: {color}; background: {bg}; "
            f"border: {border}; border-radius: 4px; "
            f"padding: 6px 10px; text-align: left; }}"
            f"QPushButton:hover {{ background: {hover}; }}"
        )

    def _apply_deck_rows(self) -> None:
        """Show the C│D row only while C or D holds or plays a track.

        Traktor exposes no 2/4-deck layout, and reading it off a screenshot
        flickered. Nothing on C and D is a 2-deck set, whatever Traktor shows.
        """
        lower_in_use = any(
            (self._deck_info.get(d) or {}).get("file_path") or d in self._playing_decks
            for d in ("C", "D")
        )
        self._deck_grid.set_rows(2 if lower_in_use else 1)

    def _refresh_deck_buttons(self) -> None:
        self._apply_deck_rows()
        # Runs several times a second: tell listeners only when the source deck changed.
        source = self._selected_deck() or ""
        if source != getattr(self, "_announced_source", None):
            self._announced_source = source
            self.source_deck_changed.emit(source)
        for letter, btn in self._deck_btns.items():
            info = self._deck_info.get(letter)
            playing = letter in self._playing_decks
            on_air = playing and self._deck_volume.get(letter, 0.0) > 0.1
            selected = btn.isChecked()
            loaded = bool(info and info.get("file_path"))
            # Webhook pings land here several times a second with nothing
            # new; only a real change of look is worth a re-polish.
            _set_qss(btn, self._deck_style(playing, on_air, loaded, selected))

            if info:
                artist = info.get("artist", "") or ""
                title = info.get("title", "") or ""
                album = info.get("album", "") or ""
                parts = [x for x in (artist, title, album) if x]
                label = "  —" if not parts else "  " + "  ·  ".join(parts)
                if len(label) > 52:
                    label = label[:49] + "…"
                indicator = "▶ " if playing else "○ " if loaded else "  "
                text = f"{indicator}{letter}{label}"
            else:
                text = f"  {letter}  —"
            if btn.text() != text:
                btn.setText(text)

    def _flash_deck_load_failed(self, deck: str) -> None:
        """Briefly flash a deck button red to signal the load couldn't start.

        Used when a drag is rejected before it begins — typically because the
        deck couldn't be detected in Traktor's window. The normal style is
        restored by ``_refresh_deck_buttons`` after a short delay.
        """
        btn = self._deck_btns.get(deck)
        if btn is None:
            return
        # Set directly: _refresh_deck_buttons compares against the button's
        # current stylesheet, so this flash is still replaced when it runs.
        btn.setStyleSheet(
            "QPushButton { color: #ffffff; background: #c0392b; "
            "border: 2px solid #e74c3c; border-radius: 4px; "
            "padding: 6px 10px; text-align: left; }"
        )
        btn.setText(f"  {deck}  ✕ not detected")
        QTimer.singleShot(1600, self._refresh_deck_buttons)

    def _on_deck_clicked(self, deck: str, checked: bool = True) -> None:
        """A deck button press is always authoritative — drop the cached
        resolution so the track is looked up again even if nothing about the
        deck changed (e.g. it has since been analysed into the library).

        Clicking the selected deck again unchecks it: no deck is selected."""
        self._source_key = None
        self._browse_query = None
        if not checked:
            self._deselect_decks()
            return
        self._select_deck(deck)

    def _deselect_decks(self) -> None:
        """No source deck: the library is browsed on the absolute axes."""
        for btn in self._deck_btns.values():
            btn.setChecked(False)
        self._source = None
        self._drag_handle.set_art(None)
        self._refresh_deck_buttons()
        self._refresh_info_line()
        self._show_library_browse()

    def _select_deck(self, deck: str) -> None:
        """Set a deck as the source track for matching."""
        # Already the selected deck, still holding the same track: keepalive
        # pings and fader moves land here continuously, and there is nothing
        # to restyle or re-resolve. (The callers refresh the deck buttons
        # themselves; a click clears _source_key so it always re-resolves.)
        info = self._deck_info.get(deck)
        key = (deck, info.get("file_path", "") if info else "")
        checked = [letter for letter, b in self._deck_btns.items() if b.isChecked()]
        if self._source_key == key and checked == [deck]:
            return
        for letter, btn in self._deck_btns.items():
            btn.setChecked(letter == deck)
        self._refresh_deck_buttons()
        self._refresh_info_line()

        if not info:
            if self._source_key == (deck, ""):
                return
            self._source_key = (deck, "")
            self._drag_handle.set_art(None)
            # An empty deck is no source at all — not the previous deck's
            # track carried over. Nothing relational (BPM window, harmonic
            # keys, synced axes) has a value to work from, so only the
            # absolute axes rank; the search text stays.
            self._source = None
            self._refresh_info_line()  # painted above, with the old source
            self._show_library_browse()
            return

        # Nothing to redo when this deck still holds the same track: keepalive
        # pings and fader moves land here continuously, and re-resolving would
        # rebuild the results table under the user's selection.
        file_path = info.get("file_path", "")
        if self._source_key == (deck, file_path):
            return
        self._source_key = (deck, file_path)

        # Try to find track in DB by exact path first
        if self.show_cover_art:
            self._drag_handle.set_art(_load_album_art(file_path) if file_path else None)
        if file_path:
            row = find_track_by_path(file_path)
            if row:
                self._set_source_row(row)
                return

        # Fallback: fuzzy search by artist + title
        artist = info.get("artist", "") or ""
        title = info.get("title", "") or ""
        query = f"{artist} - {title}" if artist and title else title or artist
        if query:
            self.set_track(query)

    # ── Track display ─────────────────────────────────────────────────────────

    def set_track(self, query: str) -> None:
        source = find_track(query)
        if source:
            self._set_source_row(source)
            return
        self.info_lbl.setText(
            f"<span style='color:#fff'>{query}</span>"
            f"&nbsp;&nbsp;<span style='color:#886644'>— not in library, "
            f"run a scan first</span>"
        )
        self._source = None
        # Already browsing the library because of this same miss — rebuilding
        # would throw away the user's selection for no gain.
        if self._browse_query == query:
            return
        self._browse_query = query
        self._show_library_browse()

    def _set_source_row(self, source: sqlite3.Row) -> None:
        # Only rebuild the results table when the source track actually changes.
        # Keepalive pings re-trigger _auto_select_active_deck every few seconds
        # with the same track; skipping _refresh() here preserves the selection.
        same_track = self._source is not None and self._source["path"] == source["path"]
        self._source = source
        self._browse_query = None  # a real source track — no longer browsing
        self._refresh_info_line()
        if not same_track:
            # A new track's matches are a fresh list: start at the best one.
            self._refresh(to_top=True)

    def _restore_bpm_settings(self) -> None:
        # BPM-restrict now lives in Settings; only the automation mode is
        # restored here (BPM-restrict state is read on demand in _refresh).
        mode_key = AUTOMATION_MODE.get(self._settings)
        self._auto_mode_idx = next((i for i, m in enumerate(_AUTO_MODES) if m[0] == mode_key), 1)
        self._apply_auto_mode()

    def is_snapped(self) -> bool:
        """True while the window sits where the library snap put it."""
        h = self._drag_handle
        if h._snapped_geom is None:
            return False
        cur = self.window().geometry()
        return still_snapped(h._snapped_geom, (cur.x(), cur.y(), cur.width(), cur.height()))

    def _show_cover_menu(self, pos) -> None:
        """The app menu, from a right-click on the cover art."""
        self._build_cover_menu().exec(self._drag_handle.mapToGlobal(pos))

    def _build_cover_menu(self) -> QMenu:
        from ui import log_actions

        menu = QMenu(self)
        menu.addAction("Settings…", self.open_settings_requested.emit)
        menu.addAction("Library…", self.open_library_requested.emit)
        menu.addAction("Library Stats…", self.open_stats_requested.emit)
        menu.addSeparator()
        menu.addAction("Show Log Folder", lambda: log_actions.show_log_folder(self))
        menu.addAction("Share Log…", lambda: log_actions.share_log(self))
        return menu

    def cover_top_left(self):
        """Global position of the cover art, where the minimised button goes."""
        return self._drag_handle.mapToGlobal(self._drag_handle.rect().topLeft())

    def mark_snapped(self) -> None:
        """The window was just snapped from outside the cover: record it."""
        self._drag_handle.mark_snapped()

    def set_docked_frame(self, on: bool) -> None:
        """Show whether the window is docked, by framing the cover."""
        self._drag_handle.set_framed(on)

    def resnap_library_overlay(self) -> None:
        """Re-anchor the library overlay if it is currently snapped.

        Invoked when Traktor's deck layout changes (2↔4 decks) so a window
        snapped over the library re-computes its top edge and keeps C/D visible.
        """
        self._drag_handle.resnap_if_snapped()

    def focus_search(self) -> None:
        """Move keyboard focus to the filter box and select existing text."""
        self.search_edit.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self.search_edit.selectAll()

    def navigate_results(self, step: int) -> None:
        """Move table selection by *step* rows (negative = up, positive = down)."""
        n = self.table.rowCount()
        if n == 0:
            return
        rows = self.table.selectionModel().selectedRows()
        current = rows[0].row() if rows else -1
        new_row = max(0, min(n - 1, current + step))
        self.table.selectRow(new_row)
        self.table.scrollTo(self.table.model().index(new_row, 0))

    def _visible_rows(self) -> int:
        """How many result rows fit on screen — a page, for Page Up/Down.

        Measured rather than fixed, because the panel is resized constantly:
        snapped over Traktor's library it is a few rows tall, full-screen it
        is forty. Never fewer than two, so a very short window still pages.
        """
        row_h = self.table.verticalHeader().defaultSectionSize() or 1
        return max(2, self.table.viewport().height() // row_h)

    def navigate_results_page(self, direction: int) -> None:
        """Move the selection a screenful at a time (negative = up)."""
        rows_per_page = self._visible_rows()
        # More rows may exist than are loaded: the table fills in pages as it
        # is scrolled, so pull the next batch in before jumping into it.
        while (
            self._loaded < len(self._filtered_results)
            and self.table.rowCount() < self._selected_row() + rows_per_page + 1
        ):
            before = self.table.rowCount()
            self._load_more()
            if self.table.rowCount() == before:
                break
        self.navigate_results(direction * rows_per_page)

    def _selected_row(self) -> int:
        rows = self.table.selectionModel().selectedRows()
        return rows[0].row() if rows else 0
