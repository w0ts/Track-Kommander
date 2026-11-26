"""The rich-text tooltip shown over a result row and over a deck button.

One builder for both, so a deck reads like the row it was loaded from: the
composite match score, then this track against the current one on every
metric, with a cover thumbnail cached on disk because Qt rich text can only
reference images by URL. Split out of ``now_playing`` since none of it needs
the tab: it takes a result dict and a source row and returns HTML.
``ui.now_playing`` re-exports the names its tests and the tab use.
"""

from __future__ import annotations

import html
import json
import logging

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QColor

from core.features import BY_KEY
from ui.np_cover_handle import _load_album_art
from ui.np_format import _get_f, _to_openkey
from ui.style import score_color
from ui.widgets import TOOLTIP_MS_DEFAULT, TOOLTIP_MS_MAX

_log = logging.getLogger(__name__)


def _tooltip_bar(frac_this: float, frac_cur: float | None, hue: int) -> str:
    """A block-character bar for a tooltip: ``frac_this`` filled in the metric's
    (luminance-tamed) hue, plus a bright marker at ``frac_cur`` — the current
    track's position on the same axis. Built from fixed-width █ glyphs so it
    renders reliably inside Qt's rich-text tooltips."""
    n = 18
    c = QColor.fromHslF(hue / 360.0, 0.5, 0.48)
    # Tame luminous hues so the white current-track marker always stands out.
    luma = 0.2126 * c.redF() + 0.7152 * c.greenF() + 0.0722 * c.blueF()
    if luma > 0.32:
        s = 0.32 / luma
        c = QColor.fromRgbF(c.redF() * s, c.greenF() * s, c.blueF() * s)
    fill, track, marker = c.name(), "#2b3038", "#ffffff"
    fi = int(round(max(0.0, min(1.0, frac_this)) * n))
    ci = (
        None
        if frac_cur is None
        else max(0, min(n - 1, int(round(max(0.0, min(1.0, frac_cur)) * n))))
    )
    cells = []
    for i in range(n):
        col = marker if (ci is not None and i == ci) else (fill if i < fi else track)
        cells.append(f'<span style="color:{col}">█</span>')
    return f'<span style="font-family:monospace;font-size:11px">{"".join(cells)}</span>'


_TIP_ART_PX = 84  # displayed size (logical px) of the tooltip cover art
_TIP_ART_CACHE_PX = 168  # cached at 2× so it stays crisp on retina displays


def _tooltip_art_file(track_path: str) -> str | None:
    """Return a cached square cover-art thumbnail for *track_path*, or None.

    Qt rich text can only reference images by URL, so the embedded art is
    extracted once per track and cached as a PNG under the app cache dir.
    Misses are negative-cached as empty files — tooltips are rebuilt on every
    hover, and re-parsing audio tags each time would make hovering laggy.
    """
    if not track_path:
        return None
    import hashlib
    from pathlib import Path

    from PySide6.QtCore import QStandardPaths

    cache_root = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.CacheLocation)
    if not cache_root:
        return None
    key = hashlib.sha1(track_path.encode("utf-8", "surrogateescape")).hexdigest()
    out = Path(cache_root) / "tip_art" / f"{key}.png"
    try:
        if out.exists():
            return str(out) if out.stat().st_size > 0 else None
        out.parent.mkdir(parents=True, exist_ok=True)
        px = _load_album_art(track_path)
        if px is None:
            out.touch()  # negative cache
            return None
        side = _TIP_ART_CACHE_PX
        px = px.scaled(
            side,
            side,
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
        # Center-crop to an exact square (art is occasionally non-square).
        x = max(0, (px.width() - side) // 2)
        y = max(0, (px.height() - side) // 2)
        px = px.copy(x, y, min(side, px.width()), min(side, px.height()))
        if not px.save(str(out), "PNG"):
            out.touch()
            return None
        return str(out)
    except Exception:
        _log.debug("tooltip art extraction failed for %r", track_path, exc_info=True)
        return None


def _row_tooltip(r: dict, source) -> str:
    """One unified rich-text (HTML) tooltip for every cell of a result row.

    Explains the composite match score, then compares this track against the
    current (source) track on each metric. Scalar metrics render as a value
    bar with a bright marker at the current track's position; BPM/Key/Rating
    (which aren't 0–1 quantities) stay as a this/current text comparison. The
    per-axis match %% is shown when the metric is an active axis.

    ``r`` is a result dict (``row`` / ``composite`` / ``axis_scores``);
    ``source`` is the current track's row (or None in library-browse mode).
    The string starts with an HTML tag so Qt renders it as rich text.
    """
    row = r["row"]
    composite = r.get("composite")
    axis_scores = r.get("axis_scores", {})

    def _text(rw, key: str) -> str:
        try:
            v = rw[key]
            return str(v).strip() if v else ""
        except (IndexError, KeyError, TypeError):
            return ""

    artist, title = _text(row, "artist"), _text(row, "title")
    header = (
        f"{artist} — {title}"
        if artist and title
        else artist or title or _text(row, "album") or "Track"
    )

    def _lab_color(key: str) -> str:
        spec = BY_KEY.get(key)
        return QColor.fromHslF((spec.hue if spec else 210) / 360.0, 0.45, 0.62).name()

    def _match_html(key: str) -> str:
        sub = axis_scores.get(key)
        return (
            f'<span style="color:{score_color(sub).name()}"> &nbsp;{sub:.0f}%</span>'
            if sub is not None
            else ""
        )

    rows_html: list[str] = []

    def _row(label: str, key: str, mid: str, right: str) -> None:
        rows_html.append(
            "<tr>"
            f'<td style="color:{_lab_color(key)};padding-right:14px">{label}</td>'
            f'<td style="padding-right:12px">{mid}</td>'
            f'<td style="color:#8893a5;white-space:nowrap">{right}</td>'
            "</tr>"
        )

    # ── Text comparison rows: BPM / Key / Rating / Genre / Style ──────────────
    def _bpm(rw):
        b = _get_f(rw, "bpm")
        return f"{b:.1f}" if b else None

    def _key(rw):
        cam = _text(rw, "camelot")
        return _to_openkey(cam) if cam else None

    def _stars(rw):
        try:
            rt = int(rw["rating"] or 0) if "rating" in rw.keys() else 0
        except (TypeError, ValueError):
            rt = 0
        return ("★" * rt + "☆" * (5 - rt)) if rt > 0 else None

    def _genre(rw):
        # Traktor genre tag (free-text metadata) — the "genre" matching axis.
        return html.escape(_text(rw, "genre")) or None

    def _style(rw):
        # CLAP-detected top genres (the "style" axis), distinct from the tag.
        try:
            raw = rw["genres"]
            if raw:
                names = [html.escape(g["name"]) for g in json.loads(raw)[:3]]
                return ", ".join(names) or None
        except Exception:
            pass
        return None

    def _released(rw):
        return html.escape(_text(rw, "release_date")) or None

    def _imported(rw):
        return html.escape(_text(rw, "import_date")) or None

    for label, key, fn in (
        ("BPM", "bpm", _bpm),
        ("Key", "key", _key),
        ("Rating", "rating", _stars),
        ("Genre", "genre", _genre),
        ("Style", "style", _style),
        ("Released", "release_date", _released),
        ("Imported", "import_date", _imported),
    ):
        this_v = fn(row)
        if this_v is None and key not in BY_KEY:
            continue
        cur_v = fn(source) if source is not None else None
        shown = this_v if this_v is not None else "—"
        mid = f'<span style="color:#dde3ea">{shown}</span>{_match_html(key)}'
        _row(label, key, mid, cur_v if cur_v is not None else "—")

    # ── Bar rows: every scalar metric (value bar + current-track marker) ──────
    bar_metrics = (
        ("Energy", "party"),
        ("Mood", "valence"),
        ("Happy", "happy"),
        ("Aggressive", "aggressive"),
        ("Relaxed", "relaxed"),
        ("Texture", "melodic"),
        ("Vocal", "vocal"),
        ("Electronic", "electronic"),
        ("Arousal", "arousal"),
        ("Danceability", "danceability"),
        ("Dynamics", "dynamics"),
        ("Bass Dyn", "low_dynamics"),
        ("Mid Dyn", "mid_dynamics"),
        ("Hi Dyn", "high_dynamics"),
        ("Loudness", "loudness"),
        ("Brightness", "brightness"),
        ("HF Content", "hfc"),
        ("Bass Energy", "low_energy"),
        ("Mid Energy", "mid_energy"),
        ("Bassiness", "bassiness"),
    )
    for label, key in bar_metrics:
        spec = BY_KEY.get(key)
        if spec is None or spec.column is None or spec.normalize is None:
            continue
        raw_t = _get_f(row, spec.column)
        if raw_t is None:
            continue
        ft = spec.normalize(raw_t)
        raw_c = _get_f(source, spec.column) if source is not None else None
        fc = spec.normalize(raw_c) if raw_c is not None else None
        nums = f'<span style="color:#dde3ea">{ft * 100:.0f}%</span>'
        if fc is not None:
            nums += f'<span style="color:#8893a5"> / {fc * 100:.0f}%</span>'
        _row(label, key, _tooltip_bar(ft, fc, spec.hue), nums + _match_html(key))

    # Similarity (CLAP embedding) — relational, match-only (no absolute value).
    if "similarity" in BY_KEY:
        sim = _match_html("similarity")
        _row(
            "Similarity", "similarity", sim if sim else '<span style="color:#dde3ea">—</span>', "—"
        )

    title_html = (
        f'<div style="color:#dde3ea;font-weight:bold;font-size:12px">{html.escape(header)}</div>'
    )
    if composite is not None:
        title_html += (
            '<div style="margin-top:3px;color:#9aa6b6">Match '
            f'<span style="color:{score_color(composite).name()};font-weight:bold">'
            f"{composite:.0f}%</span> &mdash; strength-weighted across active axes</div>"
        )

    art_file = _tooltip_art_file(_text(row, "path"))
    if art_file:
        from PySide6.QtCore import QUrl

        src_url = QUrl.fromLocalFile(art_file).toString()
        out = [
            '<table cellspacing="0" cellpadding="0"><tr>'
            f'<td style="padding-right:10px"><img src="{src_url}" '
            f'width="{_TIP_ART_PX}" height="{_TIP_ART_PX}"></td>'
            f"<td>{title_html}</td>"
            "</tr></table>"
        ]
    else:
        out = [title_html]
    if rows_html:
        head = (
            "<tr><td></td>"
            '<td style="color:#667799;padding-right:12px">this&nbsp;·&nbsp;'
            '<span style="color:#ffffff">▏</span>current</td>'
            '<td style="color:#667799">this / current</td></tr>'
        )
        out.append(
            '<table cellspacing="0" cellpadding="0" style="margin-top:6px">'
            f"{head}{''.join(rows_html)}</table>"
        )
    return "".join(out)


def _tooltip_duration_ms() -> int:
    """Configured lifetime for the big rich-text tooltips, in milliseconds.

    Qt's own auto-hide delay scales with text length, which for these
    cover-art-sized tooltips means "never" — so the lifetime is enforced by
    hand. 0 turns the tooltips off entirely.
    """
    raw = QSettings().value("results_tooltip_ms", TOOLTIP_MS_DEFAULT)
    try:
        return max(0, min(int(raw), TOOLTIP_MS_MAX))
    except (TypeError, ValueError):
        return TOOLTIP_MS_DEFAULT
