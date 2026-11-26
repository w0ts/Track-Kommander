"""Dynamic result columns and cell painting for the Now Playing table.

Works out which columns the table shows (the always-on BPM/Key/Rating, one
per active axis, and the optional display columns from Settings), rebuilds the
table header when that set changes, and fills each cell: text, score bar,
painted metric bars and the row tooltip. These methods configure the table
the tab owns and read its axis and column settings, so they are a mixin the
tab inherits. ``NowPlayingTab`` in ``ui.now_playing`` provides those
attributes.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QTableWidgetItem

from core.features import BY_KEY, FEATURES
from ui.np_constants import _PREFETCH_ROWS, _SCORE_BAR_COLS
from ui.np_format import (
    _COL_TIPS,
    RESULT_COLUMNS,
    _bpm_display,
    _get_f,
    _get_s,
    key_cell_text,
    rescol_key,
)
from ui.np_tooltip import _row_tooltip, _tooltip_duration_ms
from ui.style import key_score_color, score_color, score_color_smooth
from ui.widgets import BAR_ROLE, FILL_COLOR_ROLE, FILL_FRAC_ROLE, PARTS_ROLE


class _ColumnsMixin:
    """Column model, header rebuild and per-cell population."""

    # ── Dynamic results columns ─────────────────────────────────────────────
    # Core metric columns shown in the results at all times, in this fixed
    # order, regardless of whether they're active matching axes. When a column
    # isn't an active axis its cell still shows the track's value, just in a
    # neutral (un-scored) colour.
    _ALWAYS_SHOWN_METRICS = ("bpm", "key", "rating")

    @staticmethod
    def _metric_header(spec) -> str:
        return spec.label.replace("/", " ").split()[0].upper()

    @staticmethod
    def _metric_width(spec) -> int:
        # Key holds "12m+1 3" and BPM "133 +11.1%" — both were sized for
        # less than they now carry.
        return {"bpm": 124, "key": 104, "similarity": 78, "rating": 86}.get(spec.key, 64)

    def _metric_col(self, spec) -> dict:
        return {
            "id": f"m:{spec.key}",
            "header": self._metric_header(spec),
            "tip": f"{spec.label}: {spec.poles[0]} ←→ {spec.poles[1]}"
            + ("\n" + textwrap.fill(spec.desc, 72) if spec.desc else "")
            + "\nColour shows how well each track matches this dimension.",
            "w": self._metric_width(spec),
            "metric": spec,
        }

    def _metric_axes_in_weight_order(self) -> list:
        """Active value axes (mode≠off, strength>0), heaviest first; ties keep
        registry order. Core columns (bpm/key/rating) are excluded here — they
        are placed separately so they always show (see _build_col_model)."""
        order = {f.key: i for i, f in enumerate(FEATURES)}
        items = []
        for key, refs in self._axis_widgets.items():
            if key in self._ALWAYS_SHOWN_METRICS:
                continue
            if not refs.get("active", True):
                continue
            s = refs["strength"].value()
            if s <= 0:
                continue
            items.append((s, order.get(key, 99), refs["spec"]))
        items.sort(key=lambda t: (-t[0], t[1]))
        return [spec for _, _, spec in items]

    def _enabled_display_cols(self, group: str) -> list:
        """Enabled (id, header, width) display columns for a group, in registry
        order. Toggled from Settings ▸ Result columns (persisted per id)."""
        return [
            {"id": cid, "header": hdr, "tip": _COL_TIPS.get(cid, hdr), "w": w, "metric": None}
            for (cid, hdr, w, dflt, grp) in RESULT_COLUMNS
            if grp == group and self._settings.value(rescol_key(cid), dflt, type=bool)
        ]

    def _build_col_model(self) -> list:
        cols: list[dict] = list(self._enabled_display_cols("lead"))
        # Always-shown core metrics first, in fixed order.
        for key in self._ALWAYS_SHOWN_METRICS:
            spec = BY_KEY.get(key)
            if spec is not None:
                cols.append(self._metric_col(spec))
        # Then the active value-metric axes, heaviest first.
        for spec in self._metric_axes_in_weight_order():
            cols.append(self._metric_col(spec))
        # Trailing display columns (genre, release date, file, …).
        cols.extend(self._enabled_display_cols("trail"))
        return cols

    @staticmethod
    def _col_signature(model: list) -> tuple:
        return tuple(c["metric"].key if c["metric"] else c["id"] for c in model)

    def _maybe_rebuild_columns(self) -> None:
        model = self._build_col_model()
        if self._col_signature(model) != self._col_sig:
            self._rebuild_table_columns(model)

    def _rebuild_table_columns(self, model: list | None = None) -> None:
        self._building_cols = True
        try:
            if model is None:
                model = self._build_col_model()
            self._col_model = model
            self._col_sig = self._col_signature(model)
            self.table.setColumnCount(len(model))
            self.table.setHorizontalHeaderLabels([c["header"] for c in model])
            hh = self.table.horizontalHeader()
            for i, c in enumerate(model):
                hi = self.table.horizontalHeaderItem(i)
                if hi is not None:
                    hi.setToolTip(c["tip"])
                    if c["metric"] is not None:
                        # Tint each metric header with its dimension's hue.
                        hi.setForeground(QColor.fromHslF(c["metric"].hue / 360.0, 0.5, 0.62))
                hh.setSectionResizeMode(i, QHeaderView.ResizeMode.Interactive)
                w = self._settings.value(f"results_colw/{c['id']}", c["w"], type=int)
                self.table.setColumnWidth(i, max(20, int(w)))
                # The composite-score fill lives behind the lead text cells; it
                # must be painted by a delegate because the app stylesheet's
                # QTableWidget::item rule suppresses per-item background brushes.
                if c["id"] in _SCORE_BAR_COLS:
                    self.table.setItemDelegateForColumn(i, self._score_bar_delegate)
                else:
                    self.table.setItemDelegateForColumn(i, None)
            self._score_bar_delegate.set_span_columns(
                ci for ci, c in enumerate(model) if c["id"] in _SCORE_BAR_COLS
            )
        finally:
            self._building_cols = False

    def _populate_row(self, ri: int, r: dict, src_bpm: float, src_cam: str) -> None:
        row = r["row"]
        composite = r["composite"]
        axis_scores = r.get("axis_scores", {})
        dim = (composite is not None and composite < 40) or row["path"] in self._session_played
        self.table._row_paths[ri] = row["path"]
        # The row's tooltip is built on hover (_row_tooltip_at), not here.

        def _item(text: str, *, left: bool = False, color=None) -> QTableWidgetItem:
            it = QTableWidgetItem(text)
            it.setTextAlignment(
                (Qt.AlignmentFlag.AlignLeft if left else Qt.AlignmentFlag.AlignCenter)
                | Qt.AlignmentFlag.AlignVCenter
            )
            if color is not None:
                it.setForeground(color)
            return it

        dim_color = QColor("#666688")
        for ci, c in enumerate(self._col_model):
            cid = c["id"]
            spec = c["metric"]
            if cid in _SCORE_BAR_COLS:
                key = cid if cid in row.keys() else None
                t = (row[key] or "") if key else ""
                it = _item(t, left=True, color=dim_color if dim else None)
                self.table.setItem(ri, ci, it)
            elif cid == "style_labels":
                self._set_genre_cell(ri, ci, row, dim)
            elif spec is not None:
                self._set_metric_cell(ri, ci, spec, row, r, axis_scores, src_bpm, src_cam)
            else:
                # Plain informational text column (genre tag, dates, file, …).
                it = _item(
                    self._display_text(cid, row),
                    left=True,
                    color=dim_color if dim else QColor("#9aa6b6"),
                )
                self.table.setItem(ri, ci, it)
        self._apply_score_bar(ri, composite)

    @staticmethod
    def _display_text(cid: str, row) -> str:
        """Text for an informational (non-matching) result column."""

        def g(k: str) -> str:
            try:
                v = row[k]
                return str(v).strip() if v is not None else ""
            except (IndexError, KeyError):
                return ""

        if cid == "genre_tag":
            return g("genre")
        if cid in ("release_date", "import_date", "label", "path"):
            return g(cid)
        if cid == "filename":
            return g("filename") or Path(g("path")).name
        if cid == "analysed":
            return g("analyzed_at")[:10]
        if cid == "duration":
            try:
                d = int(row["duration"] or 0)
            except (IndexError, KeyError, TypeError, ValueError):
                d = 0
            return f"{d // 60}:{d % 60:02d}" if d else ""
        if cid in ("bitrate", "play_count"):
            v = g(cid)
            return v
        return ""

    def reload_tooltip_duration(self) -> None:
        """Apply a changed tooltip lifetime from Settings. Public entry point."""
        self.table.set_tooltip_duration(_tooltip_duration_ms())

    def reload_result_columns(self) -> None:
        """Rebuild the results table after the shown-columns selection changes."""
        self._rebuild_table_columns()
        self._apply_filter()

    # Cap on perceived (Rec.709) luminance for a fill colour. Yellow/green hues
    # are intrinsically far brighter than blue/red at the same HSL lightness, so
    # without this the dynamics (yellow-green) and similar columns glare while a
    # blue bar of equal value looks calm. Bright hues are scaled down to this.
    _BAR_LUMA_CAP = 0.30

    def _bar_color(self, frac: float, hue: int) -> QColor:
        """The fill colour of a metric bar, painted by MetricCellDelegate.

        Only the value is drawn: a single flat colour bar whose width is
        ``frac`` of the cell, the rest left empty. Brightness is tamed two
        ways: the colour ramps up over the low end of ``frac`` (so the "off"
        end is muted) and every hue is clamped to a common perceived-luminance
        ceiling (so green/yellow stop glaring relative to blue/red at the same
        value).
        """
        frac = max(0.0, min(1.0, frac))
        k = min(1.0, frac / 0.45)  # low-end ramp; 1.0 once frac ≥ 0.45
        light = 0.24 + 0.16 * k  # 0.24 (dim) → 0.40
        sat = 0.45 + 0.15 * k  # 0.45 → 0.60
        c = QColor.fromHslF(hue / 360.0, sat, light)
        # Equalise perceived brightness: only ever darken (never brighten) so
        # already-dim hues are untouched and luminous ones are pulled down.
        luma = 0.2126 * c.redF() + 0.7152 * c.greenF() + 0.0722 * c.blueF()
        if luma > self._BAR_LUMA_CAP:
            s = self._BAR_LUMA_CAP / luma
            c = QColor.fromRgbF(c.redF() * s, c.greenF() * s, c.blueF() * s)
        return c

    def _row_tooltip_at(self, ri: int) -> str:
        """The rich tooltip of result row *ri*, built when it is hovered."""
        if not 0 <= ri < min(self._loaded, len(self._filtered_results)):
            return ""
        return _row_tooltip(self._filtered_results[ri], self._source)

    def _apply_score_bar(self, ri: int, composite) -> None:
        """Set the composite score as one fill bar spanning the ARTIST·ALBUM·TITLE
        cells (behind the text), length and colour from the score. The fill is
        carried on custom item roles and rendered by ScoreBarDelegate — item
        background brushes are dropped by the stylesheet's QTableWidget::item rule.
        Only the fraction is stored: the delegate turns it into pixels from the
        column widths when it paints, so a column resize can't leave it stale."""
        if composite is None:
            color, frac = None, None
        else:
            # Darken the score colour so the wash sits behind the text. The
            # delegate applies the alpha + gradient fade; here we only pick
            # the (dark) hue.
            color = QColor(score_color_smooth(composite)).darker(340)
            frac = max(0.0, min(1.0, composite / 100.0))
        for ci, c in enumerate(self._col_model):
            if c["id"] not in _SCORE_BAR_COLS:
                continue
            it = self.table.item(ri, ci)
            if it is not None:
                it.setData(FILL_COLOR_ROLE, color)
                it.setData(FILL_FRAC_ROLE, frac)

    def _set_metric_cell(
        self, ri: int, ci: int, spec, row, r: dict, axis_scores: dict, src_bpm: float, src_cam: str
    ) -> None:
        """One metric cell. Value-style dimensions render as a coloured fill bar;
        BPM/Key stay as text and Rating as stars (coloured by match quality)."""
        sub = axis_scores.get(spec.key)  # 0–100 match sub-score, or None (no data)

        # Bar dimensions — fill length = the candidate's value (relational axes
        # like similarity/style use their match score), in the axis colour.
        # Genre renders as text (the Traktor tag) below, tinted by match score.
        if spec.kind in ("similarity", "style"):
            frac = (sub / 100.0) if sub is not None else None
        elif spec.kind == "scalar" and spec.key != "rating":
            v = _get_f(row, spec.column)
            frac = spec.normalize(v) if (v is not None and spec.normalize) else None
        else:
            frac = None  # text/stars below

        if frac is not None:
            it = QTableWidgetItem()
            it.setData(BAR_ROLE, (max(0.0, min(1.0, frac)), self._bar_color(frac, spec.hue)))
            self.table.setItem(ri, ci, it)
            return

        # Text dimensions.
        if spec.key == "bpm":
            bpm = row["bpm"] or 0
            text = _bpm_display(bpm, r.get("effective_bpm", bpm), r.get("bpm_factor", 1), src_bpm)
        elif spec.key == "key":
            text = key_cell_text(row["camelot"], r.get("transpose"), src_cam)
        elif spec.key == "rating":
            rt = int(row["rating"] or 0) if "rating" in row.keys() else 0
            text = ("★" * rt + "☆" * (5 - rt)) if rt > 0 else "—"
        elif spec.key == "genre":
            text = _get_s(row, "genre") or "—"
        elif spec.key == "label":
            text = _get_s(row, "label") or "—"
        elif spec.key == "year":
            text = (_get_s(row, "release_date") or "")[:4] or "—"
        elif spec.key == "imported":
            text = _get_s(row, "import_date") or "—"
        else:
            text = "—"
        it = QTableWidgetItem(text)
        # Genre is free text — left-align like the lead columns; others centre.
        align = (
            Qt.AlignmentFlag.AlignLeft
            if spec.key in ("genre", "label")
            else Qt.AlignmentFlag.AlignCenter
        ) | Qt.AlignmentFlag.AlignVCenter
        it.setTextAlignment(align)
        # The Key column gets its own scale: one colour per harmonic
        # relationship, because the composite traffic light's first cliff is
        # at 80 and every relationship worth mixing on scores above it.
        paint = key_score_color if spec.key == "key" else score_color
        it.setForeground(QColor("#444466") if sub is None else paint(sub))
        self.table.setItem(ri, ci, it)

    def _set_genre_cell(self, ri: int, ci: int, row, dim: bool) -> None:
        import json as _json

        parts: list[tuple[str, float]] = []
        try:
            raw = row["genres"]
            if raw:
                for g in _json.loads(raw)[:3]:
                    parts.append((g["name"], float(g.get("prob", 0.0))))
        except Exception:
            pass
        if parts and not dim:
            # Painted by MetricCellDelegate: each name more opaque the surer
            # the detection, joined by a muted "·".
            it = QTableWidgetItem()
            it.setData(
                PARTS_ROLE,
                [
                    (name, QColor(190, 205, 220, round(255 * max(0.55, min(1.0, prob / 0.50)))))
                    for name, prob in parts
                ],
            )
            self.table.setItem(ri, ci, it)
        else:
            it = QTableWidgetItem("" if not parts else parts[0][0])
            it.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            it.setForeground(QColor("#444466"))
            self.table.setItem(ri, ci, it)

    def _on_scroll(self, value: int) -> None:
        if self._loaded >= len(self._filtered_results):
            return
        sb = self.table.verticalScrollBar()
        # The scrollbar counts pixels or rows depending on the platform's
        # scroll mode; the margin is a number of rows either way.
        per_pixel = self.table.verticalScrollMode() == QAbstractItemView.ScrollMode.ScrollPerPixel
        row_h = self.table.verticalHeader().defaultSectionSize() or 1
        margin = _PREFETCH_ROWS * (row_h if per_pixel else 1)
        if value >= sb.maximum() - margin:
            self._load_more()

    def _on_col_resized(self, idx: int, _old: int, new: int) -> None:
        """Persist user-driven column resizes, keyed by logical column id so a
        width survives the columns being reordered by weight."""
        if self._building_cols or new <= 0:
            return
        if 0 <= idx < len(self._col_model):
            self._settings.setValue(f"results_colw/{self._col_model[idx]['id']}", int(new))
