"""Ranking, search filter and paging of the Now Playing results table.

Re-ranks the library against the source track and the active axes, applies
the search box, and loads rows into the table a page at a time as the user
scrolls. These methods drive the table the tab owns (``table``, the cached
result list, the debounce timers), so they are a mixin the tab inherits
rather than a separate object. ``NowPlayingTab`` in ``ui.now_playing``
provides those attributes.
"""

from __future__ import annotations

import json

from core import browse_sample
from core.db import list_all_tracks_for_browsing
from core.matcher import rank_matches_absolute, rank_matches_axes
from core.prefs import BPM_WINDOW_PCT
from ui.native_match_cells import match_cells
from ui.np_constants import _PAGE, _PREFETCH_ROWS
from workers.native_columns import MatchCells


class _ResultsMixin:
    """Refresh, search filter, selection restore and paged loading."""

    def _schedule_refresh(self) -> None:
        """Coalesce a burst of slider ticks into one re-rank (restarts the timer).

        Only for continuous gestures — discrete actions (toggling an axis, a deck
        change) still re-rank immediately so they feel instant.
        """
        self._rerank_debounce.start()

    def _max_bpm_pct(self) -> float:
        """BPM-restrict window (±%), or 0 when disabled. The control now lives in
        Settings; this reads the persisted value each time we re-rank.

        Lifted while a search is typed: "FKA" once found only "Puffka",
        because the FKA twigs track at 166 BPM had been cut in SQL before the
        search box saw it. What was asked for by name must be findable.
        """
        if self._filter_text:
            return 0
        if self._settings.value("bpm_restrict", False, type=bool):
            return BPM_WINDOW_PCT.get(self._settings)
        return 0

    def _harmonic_only(self) -> bool:
        """Hard Camelot filter (Settings). Independent of the Key axis's weight:
        the axis ranks, this hides. Read fresh on every re-rank, and lifted
        while a search is typed, like the BPM window."""
        if self._filter_text:
            return False
        return self._settings.value("harmonic_only", False, type=bool)

    def refresh_matching(self) -> None:
        """Re-rank against the current track (e.g. after Settings changes the
        BPM-restrict window). Public entry point for MainWindow wiring."""
        self._refresh()

    def _on_search_changed(self, text: str) -> None:
        """A keystroke in the filter box: apply it once typing pauses."""
        self._pending_filter_text = text.strip().lower()
        self._filter_debounce.start()  # restarts while the user keeps typing

    def _apply_search_text(self) -> None:
        """Apply the filter box's text (the typing debounce ran out)."""
        text = self._pending_filter_text
        if text == self._filter_text:
            return  # typed and erased again, or only whitespace changed
        had_query = bool(self._filter_text)
        self._filter_text = text
        # A search is an explicit ask for a track; the hard filters exist to
        # trim browsing noise. So the candidate set changes when a search
        # starts or ends — re-rank then, and only then: each keystroke in
        # between only narrows the same ranked list.
        if bool(self._filter_text) != had_query and self._source is not None:
            self._refresh()
        else:
            self._apply_filter()

    @property
    def _all_results(self) -> list[dict]:
        return self._all_results_list

    @_all_results.setter
    def _all_results(self, results: list[dict]) -> None:
        # A new ranking may carry re-analysed rows, so the cached search
        # text only holds for the list it was built from.
        self._all_results_list = results
        self._search_text_cache = {}

    def _search_text(self, r: dict) -> str:
        """_row_search_text of a result, built once per result list."""
        path = r["row"]["path"]
        text = self._search_text_cache.get(path)
        if text is None:
            text = self._row_search_text(r["row"])
            self._search_text_cache[path] = text
        return text

    @staticmethod
    def _row_search_text(row) -> str:
        """Everything searchable about a track, lower-cased: artist, title,
        album, genre tag, label, release/import dates, full path (so file name
        matches too), and the detected style labels."""

        def g(k: str) -> str:
            try:
                v = row[k]
                return str(v) if v is not None else ""
            except (IndexError, KeyError):
                return ""

        parts = [
            g("artist"),
            g("title"),
            g("album"),
            g("genre"),
            g("label"),
            g("release_date"),
            g("import_date"),
            g("path"),
        ]
        raw = g("genres")
        if raw:
            try:
                parts.extend(x.get("name", "") for x in json.loads(raw))
            except Exception:
                pass
        return " ".join(parts).lower()

    def _apply_filter(
        self, *, to_top: bool = False, select_path: str | None = None, min_rows: int = 0
    ) -> None:
        """Rebuild the table from _all_results through the search filter.

        *select_path* picks the row to select afterwards instead of the current
        selection; *min_rows* loads at least that many rows, so a rebuild can
        keep a row the user had scrolled down to within reach.
        """
        # Columns track the active axes and their weight order.
        self._maybe_rebuild_columns()
        q = self._filter_text
        if q:
            # Space-separated terms must all match (AND), searched everywhere.
            terms = q.split()
            self._filtered_results = [
                r for r in self._all_results if all(t in self._search_text(r) for t in terms)
            ]
        elif self._source is None:
            # No source deck: a random selection, drawn once per launch (core.browse_sample);
            # a search still looks through everything.
            ranked = bool(self._all_results) and self._all_results[0].get("composite") is not None
            self._filtered_results = browse_sample.sample(self._all_results, ranked=ranked)
        else:
            self._filtered_results = self._all_results

        # Remember what was selected so a re-rank / re-filter doesn't yank the
        # row out from under the user (the S4 browse encoder and the keyboard
        # load shortcuts both act on the selection). *to_top* (a new source
        # track) drops it: the list starts over at the best match.
        sel_path = select_path
        if sel_path is None and not to_top:
            sel_rows = self.table.selectionModel().selectedRows()
            if sel_rows:
                sel_path = self.table._row_paths.get(sel_rows[0].row())

        # The whole rebuild is programmatic. blockSignals covers the table,
        # but the selection model is a separate object: emptying the table
        # moves its current row, which would otherwise read as the user
        # picking a row — firing the Traktor pre-search, which stops the deck
        # loaded with Enter.
        was_restoring = self._restoring_selection
        self._restoring_selection = True
        try:
            self._loaded = 0
            self.table.blockSignals(True)
            self.table.setRowCount(0)
            self.table._row_paths.clear()
            self.table.blockSignals(False)
            self._load_more()
            self._load_until(min_rows)
            self._fill_viewport()
            self._restore_selection(sel_path)
        finally:
            self._restoring_selection = was_restoring
        self.results_changed.emit()

    def result_paths(self) -> list[str]:
        """File paths of the results as listed, best first (filter applied)."""
        return [r["row"]["path"] for r in self._filtered_results]

    def result_scores(self) -> dict[str, MatchCells]:
        """Each listed result's score (0–100) and BPM/key match: TK's SCORE, BPM and KEY columns
        in Traktor's list, inside Traktor."""
        return match_cells(self._filtered_results, *self._source_bpm_cam())

    def _source_bpm_cam(self) -> tuple[float, str]:
        """What the results are measured against: the playing track's BPM and Camelot key."""
        if self._source is None:
            return 0.0, ""
        return self._src_bpm_effective or self._source["bpm"] or 0, self._source["camelot"] or ""

    def _restore_selection(self, path: str | None) -> None:
        """Select *path* again after a table rebuild, else the first row.

        Rows load lazily in pages, so a track that survived the rebuild but now
        sits past the loaded window falls back to the first row rather than
        forcing the whole result set to render. Programmatic, like the rebuild
        itself: no Traktor pre-search fires.
        """
        if self.table.rowCount() == 0:
            return
        row = next((r for r, p in self.table._row_paths.items() if p == path), 0)
        # Keep an outer caller's guard (_apply_filter holds it for the whole
        # rebuild) rather than dropping it on the way out.
        was_restoring = self._restoring_selection
        self._restoring_selection = True
        try:
            self.table.selectRow(row)
        finally:
            self._restoring_selection = was_restoring
        self.table.scrollTo(self.table.model().index(row, 0))

    def _load_until(self, n_rows: int) -> None:
        """Load pages until *n_rows* rows are in the table (or all are)."""
        while self._loaded < len(self._filtered_results) and self.table.rowCount() < n_rows:
            before = self.table.rowCount()
            self._load_more()
            if self.table.rowCount() == before:
                break

    def _fill_viewport(self) -> None:
        """Load enough rows to fill the visible table plus a small margin.

        More pages otherwise load only when the scrollbar nears its end — and
        a first page that fits the viewport has no scrollbar, which left the
        rest of the results unreachable by mouse.
        """
        self._load_until(self._visible_rows() + _PREFETCH_ROWS)

    def _show_library_browse(self) -> None:
        """No track loaded in Traktor — rank on the absolute axes, or browse.

        The axes set to an absolute target (Dynamics at 90%, Rating at 5★, …)
        still say which tracks fit without a current track, so they keep
        ranking the library. Only when nothing can rank — every active axis is
        relational or synced to the (absent) current track — does this fall
        back to a plain browse sorted by rating then most-recently-analysed,
        with the score column showing '—'.
        """
        self._src_bpm_effective = 0.0
        ranked = rank_matches_absolute(self._collect_axes())
        if ranked:
            self._all_results = ranked
            self._apply_filter()
            return
        rows = list_all_tracks_for_browsing()
        self._all_results = [
            {
                "row": r,
                "composite": None,
                "bpm_score": 0.0,
                "key_score": 0.0,
                "vibe_score": 0.0,
                "rating_score": 0.0,
                "effective_bpm": r["bpm"] or 0.0,
                "bpm_factor": 1,
            }
            for r in rows
        ]
        self._apply_filter()

    def _refresh(self, *, to_top: bool = False) -> None:
        self._rerank_debounce.stop()  # an immediate rank supersedes a pending one
        if self._source is None:
            self._show_library_browse()
            return
        max_bpm_pct = self._max_bpm_pct()
        src_bpm_override = self._live_bpm() or None
        self._src_bpm_effective = src_bpm_override or (self._source["bpm"] or 0)
        try:
            # Re-sync synced axes and repaint card fills for the (new) source.
            for key in list(self._axis_widgets):
                if self._axis_sync(key):
                    self._sync_axis_value_from_source(key)
                self._update_card_background(key)
            self._all_results = rank_matches_axes(
                self._source,
                self._collect_axes(),
                max_bpm_pct,
                src_bpm_override,
                harmonic_only=self._harmonic_only(),
            )
        except Exception as exc:
            from ui.dialogs import warn

            warn(self, "Matcher error", str(exc))
            return
        self._apply_filter(to_top=to_top)

    def _load_more(self) -> None:
        batch = self._filtered_results[self._loaded : self._loaded + _PAGE]
        if not batch:
            return
        src_bpm, src_cam = self._source_bpm_cam()
        start = self.table.rowCount()
        self.table.blockSignals(True)
        try:
            self.table.setRowCount(start + len(batch))
            for i, r in enumerate(batch):
                self._populate_row(start + i, r, src_bpm, src_cam)
        finally:
            self.table.blockSignals(False)
        self._loaded += len(batch)
