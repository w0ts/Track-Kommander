"""TK's BPM and KEY match cells for Traktor's list, built as TK's own results table builds them.

The text is ``_bpm_display`` / ``key_cell_text`` and the colour TK's (``score_color`` for BPM, one
colour per harmonic relationship for Key, dim when that axis has no score), so a result reads the
same inside Traktor as in standalone TK (User, 2026-11-24: "like they did in tk").
"""

from __future__ import annotations

from collections.abc import Iterable

from ui.np_format import _bpm_display, key_cell_text
from ui.style import key_score_color, score_color
from workers.native_columns import MatchCells

#: TK's colour for a cell whose axis has no score (np_columns._set_metric_cell).
_UNSCORED_ARGB = 0xFF444466


def _cells(r: dict, src_bpm: float, src_cam: str) -> MatchCells:
    row = r["row"]
    bpm = row["bpm"] or 0
    subs = r.get("axis_scores") or {}
    bpm_sub, key_sub = subs.get("bpm"), subs.get("key")
    return MatchCells(
        score=r.get("composite"),
        bpm=_bpm_display(bpm, r.get("effective_bpm", bpm), r.get("bpm_factor", 1), src_bpm),
        bpm_argb=_UNSCORED_ARGB if bpm_sub is None else score_color(bpm_sub).rgba(),
        key=key_cell_text(row["camelot"], r.get("transpose"), src_cam),
        key_argb=_UNSCORED_ARGB if key_sub is None else key_score_color(key_sub).rgba(),
    )


def match_cells(results: Iterable[dict], src_bpm: float, src_cam: str) -> dict[str, MatchCells]:
    """Each result's cells, by path."""
    return {r["row"]["path"]: _cells(r, src_bpm, src_cam) for r in results}
