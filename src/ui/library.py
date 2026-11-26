"""LibraryTab — scan and analyse tracks from Traktor NML.

The tab is one widget over five areas, each in its own module as a mixin so
the widgets, ``QSettings`` and worker references they share stay on one
object: scan (``library_scan``), hotcues (``library_hotcues``), beat-grid
fixes (``library_grid_fixes``), automatic playlists (``library_playlists``)
and the USB export (``library_export`` and ``library_export_selection``).
What stays here is the tab scaffolding, the collection path and the guards
every collection write goes through.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import (
    QSettings,
    QTimer,
    Signal,
)
from PySide6.QtWidgets import (
    QVBoxLayout,
    QWidget,
)

from core.traktor import (
    find_traktor_nml,
    is_traktor_running,
)
from ui.library_discogs import _DiscogsMixin
from ui.library_export import _ExportMixin
from ui.library_export_selection import _ExportSelectionMixin
from ui.library_grid_fixes import _GridFixMixin
from ui.library_hotcues import _HotcueMixin
from ui.library_playlists import _AutoPlaylistsMixin
from ui.library_scan import _ScanMixin
from ui.style import make_tab_widget, scroll_page

if TYPE_CHECKING:
    from workers.analysis import AnalysisWorker
    from workers.device_export import DeviceExportWorker
    from workers.hotcue_worker import HotcueWorker

__all__ = ["LibraryTab"]

log = logging.getLogger(__name__)


class LibraryTab(
    _ScanMixin,
    _HotcueMixin,
    _GridFixMixin,
    _DiscogsMixin,
    _AutoPlaylistsMixin,
    _ExportMixin,
    _ExportSelectionMixin,
    QWidget,
):
    # Emitted when the user clicks "Check Library". The list of audio paths
    # comes from the currently-selected NML. MainWindow handles the dialog
    # because it owns the WebhookWatcher.
    check_library_requested = Signal(list)
    # A scan finished and wrote features to the database, so anything showing
    # library-wide figures (the Stats page) is out of date.
    library_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._worker: AnalysisWorker | None = None
        self._hc_worker: HotcueWorker | None = None
        self._export_worker: DeviceExportWorker | None = None
        self._export_ctx: dict = {}
        self._n_tracks: int = 0
        self._settings = QSettings()
        self._analysis_start: float | None = None  # wall time when audio analysis began
        self._analysis_done: int = 0  # tracks completed in current analysis phase
        self._build_ui()
        self._restore_settings()
        self._update_stats()
        # Reading the playlists parses the whole collection.nml. Deferred one
        # event-loop turn so the window paints first instead of waiting on
        # it at startup; tied to this widget so it never runs after it is
        # gone.
        QTimer.singleShot(0, self, self.reload_playlists)

    # Same pattern as SettingsTab: groups declare their tab, the widget is
    # assembled at the end. Scan / Hotcues / Export were one long column.
    _TAB_ORDER = ("Scan", "Hotcues", "Tagging", "Playlists", "Export")
    #: Display names of the playlists ticked for the last export.
    _PLAYLISTS_KEY = "usb_export_playlists"
    _LAST_TAB_KEY = "library_last_tab"
    #: Old tab names a saved last tab may still carry.
    _RENAMED_TABS = {"Rekordbox": "Export", "Discogs": "Tagging"}

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

    def hide_grid_fixes(self) -> None:
        """Inside Traktor a deck's grid is fixed live, from the deck (align phase with master),
        not from offsets captured for a later collection.nml rewrite (User, 2026-11-24)."""
        self._grid_fix_box.hide()

    def hide_auto_playlists(self) -> None:
        """Inside Traktor the Kommander date playlists keep themselves: TK refreshes them as plain
        playlists through libtk when it starts there (workers/native_playlists.py, User 2026-11-24)."""
        self._auto_playlists_box.hide()

    def hide_collection_fill(self) -> None:
        """Inside Traktor: Discogs' "Fill Traktor" plans from collection.nml and writes it back,
        and TK never reads or writes that file there (User, 2026-11-24). The lookup stays."""
        self.discogs_fill_btn.hide()

    def _add_pinned(self, name: str, widget: QWidget) -> None:
        """Add a group to a tab that ends in a stretch, so it sits at the top.

        Without the trailing stretch (added in _assemble_tabs) a lone group box
        grows over the whole page and its rows drift apart. Tabs ending in a
        deliberately expanding widget — the Scan tab's timing box, the Export
        tab's playlist list — add their widgets directly instead. Mirrors SettingsTab, which stretches every tab.
        """
        self._tab(name).addWidget(widget)
        self._pinned_tabs.add(name)

    def show_tab(self, name: str) -> None:
        """Switch to a named tab (the band's page buttons inside Traktor)."""
        name = self._RENAMED_TABS.get(name, name)
        for i in range(self._tab_widget.count()):
            if self._tab_widget.tabText(i) == name:
                self._tab_widget.setCurrentIndex(i)
                return

    def _assemble_tabs(self) -> None:
        for name in self._pinned_tabs:
            self._tab(name).addStretch()
        for name in self._TAB_ORDER:
            page = self._tab_pages.get(name)
            if page is not None:
                self._tab_widget.addTab(scroll_page(page), name)
        for name, page in self._tab_pages.items():
            if name not in self._TAB_ORDER:
                self._tab_widget.addTab(scroll_page(page), name)

        want = self._settings.value(self._LAST_TAB_KEY, "", type=str)
        want = self._RENAMED_TABS.get(want, want)
        for i in range(self._tab_widget.count()):
            if self._tab_widget.tabText(i) == want:
                self._tab_widget.setCurrentIndex(i)
                break
        self._tab_widget.currentChanged.connect(
            lambda i: self._settings.setValue(self._LAST_TAB_KEY, self._tab_widget.tabText(i))
        )

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self._tab_widget = make_tab_widget()
        outer.addWidget(self._tab_widget)
        self._tabs_by_name: dict[str, QVBoxLayout] = {}
        self._tab_pages: dict[str, QWidget] = {}
        self._pinned_tabs: set[str] = set()
        root = self._tab("Scan")

        self._build_scan_top(root)
        self._build_hotcue_box()
        self._build_grid_fix_box()
        self._build_discogs_box()
        self._auto_playlists_box = self._build_auto_playlists_group()
        self._add_pinned("Playlists", self._auto_playlists_box)
        self._build_timing_box(root)

        # Not pinned: the export group carries the playlist list, which should
        # take the height a taller window gives it.
        self._tab("Export").addWidget(self._build_export_group(), 1)

        self._build_extension_tabs()
        self._assemble_tabs()
        from core.extensions import addon_shown

        if not addon_shown():  # an add-on hidden from the start (its own setting)
            self.show_extension_tabs(False)

    def _build_extension_tabs(self) -> None:
        """An add-on's tabs (core.extensions), after TK's own. One that fails is left out."""
        from core.extensions import library_tabs

        for tab in library_tabs():
            try:
                widget = tab.build(self)
            except Exception:
                log.exception("library: the %s tab could not be built", tab.title)
                continue
            lay = self._tab(tab.title)
            lay.addWidget(widget, 1)
            if tab.flush_top:
                m = lay.contentsMargins()
                lay.setContentsMargins(m.left(), 0, m.right(), m.bottom())

    def show_extension_tabs(self, shown: bool) -> None:
        """An add-on shown or hidden (core.extensions.addon_shown): its tabs follow at once."""
        from core.extensions import library_tabs

        own = {tab.title for tab in library_tabs()}
        for i in range(self._tab_widget.count()):
            if self._tab_widget.tabText(i) in own:
                self._tab_widget.setTabVisible(i, shown)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.refresh_export_destinations()
        # Captures happen in the NOW PLAYING panel, usually with this window
        # closed, and they outlive a restart — so the list is read again on
        # every show rather than kept from construction.
        self.refresh_grid_fixes()

    def _nml(self) -> str:
        """The collection.nml of the selected Traktor, or "" if there is none.

        There is no separate collection setting: ``find_traktor_nml()``
        resolves the library of whichever install Settings is pointing at, so
        the picker is the only thing that can change it.
        """
        found = find_traktor_nml()
        return str(found) if found else ""

    def refresh_traktor_config(self) -> None:
        """Re-read the collection path after Settings changed it."""
        nml = self._nml()
        self.nml_label.setText(nml or "No collection.nml configured.")
        self._reload_playlists_from(nml)
        self._update_stats()

    def _restore_settings(self) -> None:
        self.nml_label.setText(self._nml() or "No collection.nml configured.")

    def _warn_traktor_open(self, *, key: str, verb: str, noun: str) -> bool:
        """Warn that *noun* reads a possibly stale collection. True to proceed.

        Traktor keeps its collection in memory and writes ``collection.nml``
        when it quits, so anything added or edited in this Traktor session —
        new tracks, changed BPM/key/rating, new cue points — is not on disk yet
        and simply won't be seen.

        Dismissal is per *key*: silencing it for the scan, which is harmless
        to redo, must not silence it for an export that writes a stick.
        Inside Traktor nothing is stale: TK reads Traktor's memory there.
        """
        from core.runtime import in_traktor

        if in_traktor() or not is_traktor_running():
            return True
        from ui import dialogs

        return dialogs.confirm_dismissible(
            self,
            "Traktor is running",
            "Traktor appears to be running.\n\n"
            "It keeps its collection in memory and only writes collection.nml "
            "when it quits, so the file on disk may be out of date — tracks "
            "you added or edited in this Traktor session would be missed by "
            f"the {noun}.\n\n"
            f"Quit Traktor first for a complete {noun}.\n\n"
            f"{verb} anyway?",
            key=key,
            yes_text=f"{verb} anyway",
        )

    def _warn_traktor_open_for_scan(self) -> bool:
        return self._warn_traktor_open(key="scan_traktor_running", verb="Scan", noun="scan")

    def _warn_traktor_open_for_export(self) -> bool:
        return self._warn_traktor_open(key="export_traktor_running", verb="Export", noun="export")

    def _confirm_traktor_closed(self, action: str) -> bool:
        """Hold a collection write until Traktor is closed. True to go ahead.

        No "continue anyway": Traktor rewrites the collection when it quits,
        so a write while it runs was always lost. Called as the last step
        before writing — after any confirmation — because Traktor quit from
        here is started again by :meth:`_collection_written`.
        """
        from core.runtime import in_traktor
        from ui.traktor_guard import wait_for_traktor_closed

        if in_traktor():
            # TK runs inside Traktor: Traktor cannot be closed for the write (TK would close
            # with it), and a write while it runs is lost when it quits. Refused, not attempted.
            from ui.dialogs import warn

            warn(
                self,
                f"{action}: not inside Traktor",
                "This writes collection.nml, which Traktor overwrites when it quits. Inside "
                "Traktor it is not available yet: use the standalone Track Kommander with "
                "Traktor closed.",
            )
            log.info("%s refused inside Traktor (collection.nml write)", action)
            return False
        go, relaunch = wait_for_traktor_closed(self, action, running=is_traktor_running)
        if relaunch:
            self._relaunch_traktor = True
        return go

    def _collection_written(self) -> None:
        """A write is over: start Traktor again if it was quit for it."""
        if getattr(self, "_relaunch_traktor", False):
            self._relaunch_traktor = False
            from core.traktor import launch_traktor

            launch_traktor()
            log.info("Traktor started again after writing the collection")
