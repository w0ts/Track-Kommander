"""Pure text and number helpers for the Now Playing tab.

Key and BPM formatting, the info line, the result-column table and the small
arithmetic the tab leans on. Nothing here builds a widget or reads tab state,
which is why it lives apart from ``now_playing``: the pieces can be unit-tested
without a Qt application, and every other ``np_*`` module can import them
without pulling in the tab. ``ui.now_playing`` re-exports every public name so
existing imports keep working.
"""

from __future__ import annotations

from core.constants import CAMELOT_COLORS, CAMELOT_TO_OPENKEY
from ui.style import T


def _set_qss(widget, qss: str) -> None:
    """Apply *qss* unless the widget already has exactly that stylesheet.

    setStyleSheet re-polishes the widget and its children even when the text
    is unchanged, and several paths here run many times a second (deck pings,
    slider drags, re-ranks) with nothing new to show.
    """
    if widget.styleSheet() != qss:
        widget.setStyleSheet(qss)


def _set_tip(widget, text: str) -> None:
    """setToolTip, skipped when the text is unchanged."""
    if widget.toolTip() != text:
        widget.setToolTip(text)


def _beats(phase: float) -> str:
    """A phase offset in beats, naming the fractions a DJ recognises: the
    grid landing on the offbeat, or on the 16ths, is what this mostly fixes.
    """
    named = {0.5: "half a beat", 0.25: "a quarter beat", 0.125: "an eighth beat"}
    for size, name in named.items():
        if abs(abs(phase) - size) < 0.02:
            return f"{phase:+.3f} beat — {name}"
    return f"{phase:+.3f} beat"


def transposed_key_text(camelot: str | None, shift: int | None) -> str:
    """The key this track would play in, and how far to move the Key knob.

    Plain key when it already fits, so a track needing no transpose reads
    the same as it does in the Key column.
    """
    from core.matcher import transpose_camelot

    if not camelot:
        return "—"
    if not shift:
        return _to_openkey(camelot)
    moved = transpose_camelot(camelot, shift)
    if moved is None:
        return "—"
    return f"{_to_openkey(moved)}  {shift:+d}"


def widen_stored_columns(settings, defaults: dict[str, int], *, flag: str) -> list[str]:
    """One-shot: forget a stored column width that a raised default beats.

    Widths are remembered per column the first time the table is built, so a
    new default would otherwise only ever reach a fresh install. A width the
    user dragged wider than the new default is theirs and is left alone.
    Returns the ids that were cleared, which is what the test asserts on.
    """
    key = f"colw_migrated/{flag}"
    if settings.value(key, False, type=bool):
        return []
    settings.setValue(key, True)
    cleared = []
    for cid, want in defaults.items():
        setting = f"results_colw/{cid}"
        raw = settings.value(setting)
        if raw is None:
            continue
        try:
            have = int(raw)
        except (TypeError, ValueError):
            continue  # hand-edited or from another build — leave it
        if have < want:
            settings.remove(setting)
            cleared.append(cid)
    return cleared


def key_cell_text(camelot: str | None, shift: int | None, source_camelot: str | None) -> str:
    """The Key column, as plain text: ``12m+1 3``.

    The key you will *hear* (the written one moved by the Key knob), the
    knob move itself pressed against it, then how far that lands from what
    is playing. The shift is dropped when there is none and the distance
    when there is nothing to measure against, so an ordinary row stays two
    short tokens.
    """
    from core.matcher import key_gap, transpose_camelot

    if not camelot:
        return "—"
    sounding = transpose_camelot(camelot, shift or 0)
    if sounding is None:
        return "—"
    out = _to_openkey(sounding)
    if shift:
        out += f"{shift:+d}"
    gap = key_gap(source_camelot, sounding)
    if gap is not None:
        steps, same_mode = gap
        # A mode change is not a wheel step, so it cannot go in the number —
        # but without it "12d 0" claims to be the same key as 12m, when it is
        # the relative major/minor: a shared key signature and nothing else.
        out += f" {'' if same_mode else '~'}{steps}"
    return out


def sounding_key(camelot: str | None, semitones: int | None) -> str | None:
    """The key a deck plays in with its Key knob turned this far.

    ``None`` when there is nothing to add to the display: no key, an
    unreadable one, or a knob sitting at zero.
    """
    from core.matcher import transpose_camelot

    if not camelot or not semitones:
        return None
    return transpose_camelot(camelot, semitones)


def live_source_bpm(
    selected: str | None, deck_tempo: dict[str, float], *, master_bpm: float, playing: set[str]
) -> float:
    """The tempo the selected deck plays at, or 0 when we can't tell.

    The deck's own reading first: it holds while paused and is right when
    the master clock follows another deck. The master clock stands in only
    for a bridge too old to report deck tempo, and only while playing.
    """
    if not selected:
        return 0.0
    if deck_tempo.get(selected, 0.0) > 0:
        return deck_tempo[selected]
    return master_bpm if master_bpm and selected in playing else 0.0


def info_line_html(*, camelot: str | None, db_bpm: float, master_bpm: float, semitones: int) -> str:
    """The line under the cover art: what the deck is actually doing.

    Both halves show a written value and an effective one — the track's
    tempo beside the master clock, the track's key beside the key the Key
    knob leaves it in. The unit is left off: the pair of numbers and the
    percentage say what they are, and the width goes to the second key.
    """
    dim = T.TEXT_VDIM
    val = "#9999cc"
    sep = f"&nbsp;&nbsp;<span style='color:{dim}'>·</span>&nbsp;&nbsp;"
    parts: list[str] = []

    if db_bpm:
        bpm_str = f"<span style='color:{val}'>{db_bpm:.0f}</span>"
        if master_bpm:
            pct = (master_bpm - db_bpm) / db_bpm * 100
            bpm_str += (
                f"&nbsp;<b style='color:#ccccee'>{master_bpm:.2f}</b>"
                f"&nbsp;<span style='color:{val}'>{pct:+.2f}%</span>"
            )
        parts.append(bpm_str)

    if camelot:
        moved = sounding_key(camelot, semitones)
        written = CAMELOT_COLORS.get(camelot, "#dde1f0")
        if moved is None:
            parts.append(f"<b style='color:{written}'>{_to_openkey(camelot)}</b>")
        else:
            parts.append(
                f"<span style='color:{written}'>{_to_openkey(camelot)}</span>"
                f"&nbsp;<b style='color:{CAMELOT_COLORS.get(moved, '#dde1f0')}'>"
                f"{_to_openkey(moved)}</b>"
                f"&nbsp;<span style='color:{val}'>{semitones:+d}</span>"
            )

    return sep.join(parts)


def still_snapped(
    snapped: tuple[int, int, int, int], current: tuple[int, int, int, int], *, slop: int = 2
) -> bool:
    """Is the window still where the library snap put it?

    Position and width must match (to a pixel or two). The height may be
    *larger*: a 4-deck snap can ask for less than the window's minimum
    height, and macOS then makes it taller after the fact — that is the
    window manager, not the user, and the overlay should keep following.
    Smaller means the user resized it.
    """
    sx, sy, sw, sh = snapped
    cx, cy, cw, ch = current
    return (
        abs(cx - sx) <= slop and abs(cy - sy) <= slop and abs(cw - sw) <= slop and ch >= sh - slop
    )


# Result-list display ("non-matching") columns the user can show/hide from
# Settings ▸ Result columns. Each: (id, header, default_width, default_on,
# group). group "lead" = leading text columns that carry the match-score fill
# bar (artist/album/title); "trail" = appended after the metric columns.
RESULT_COLUMNS = (
    ("artist", "ARTIST", 160, True, "lead"),
    ("album", "ALBUM", 150, True, "lead"),
    ("title", "TITLE", 240, True, "lead"),
    ("genre_tag", "GENRE", 120, False, "trail"),
    ("style_labels", "STYLE", 170, True, "trail"),
    ("release_date", "RELEASED", 94, True, "trail"),
    ("import_date", "IMPORTED", 94, False, "trail"),
    ("label", "LABEL", 130, False, "trail"),
    ("duration", "TIME", 58, False, "trail"),
    ("bitrate", "KBPS", 56, False, "trail"),
    ("play_count", "PLAYS", 52, False, "trail"),
    ("analyzed", "ANALYSED", 100, False, "trail"),
    ("filename", "FILE", 220, False, "trail"),
    ("path", "PATH", 360, False, "trail"),
)

_COL_TIPS = {
    "artist": "Track artist",
    "album": "Album",
    "title": "Track title",
    "genre_tag": "Traktor genre tag",
    "style_labels": "Detected style — top genre labels (CLAP); colour intensity = confidence",
    "release_date": "Release date (Traktor)",
    "import_date": "Date added to Traktor",
    "label": "Record label",
    "duration": "Track length",
    "bitrate": "Bitrate (kbps)",
    "play_count": "Traktor play count",
    "analysed": "Date analysed by this app",
    "filename": "File name",
    "path": "Full file path",
}


def rescol_key(cid: str) -> str:
    """QSettings key for whether result column *cid* is shown."""
    return f"rescol/{cid}"


def _flow_columns(
    n_items: int, avail_width: int, hspace: int, min_w: int, max_w: int = 0
) -> tuple[int, int]:
    """Compute the column count and per-item width for a stretch-to-fill flow.

    Items are spread across the *fewest rows* that keep every card at least
    ``min_w`` wide, then split **evenly** over those rows so each row is full
    rather than packing the first row solid and leaving a ragged remainder on
    the last (e.g. 12 cards become 6 + 6, not 8 + 4). Every card then grows to
    share its row evenly so the row is always filled edge-to-edge; ``max_w``
    (if > 0) caps the width, otherwise cards stretch without an upper bound.
    Pure arithmetic so it can be unit-tested without a Qt layout. Returns
    ``(0, min_w)`` for no items.
    """
    if n_items <= 0:
        return 0, min_w
    avail = max(0, avail_width)
    # How many min-width items (plus the gaps between them) fit on one row?
    fit = (avail + hspace) // (min_w + hspace)
    fit = max(1, min(int(fit), n_items))
    # Fewest rows needed, then balance the items evenly across them.
    rows = (n_items + fit - 1) // fit  # ceil(n / fit)
    cols = (n_items + rows - 1) // rows  # ceil(n / rows)
    item_w = (avail - (cols - 1) * hspace) // cols
    item_w = max(min_w, int(item_w))
    if max_w > 0:
        item_w = min(max_w, item_w)
    return cols, item_w


def _to_openkey(cam: str) -> str:
    """Convert a Camelot key string to Open Key notation (3d, 7m).
    Falls back to the input unchanged if it's not a known Camelot key.
    """
    return CAMELOT_TO_OPENKEY.get(cam, cam)


def _bpm_display(bpm: float, effective_bpm: float, factor: int, source_bpm: float) -> str:
    """BPM with optional ×2//2 indicator and %-variation from source."""
    if factor == 2:
        suffix = " ×2"
    elif factor == -2:
        suffix = " /2"
    else:
        suffix = ""
    if source_bpm:
        pct = (effective_bpm - source_bpm) / source_bpm * 100
        return f"{bpm:.0f}{suffix}  {pct:+.1f}%"
    return f"{bpm:.0f}{suffix}"


# ── Reading a library row ──────────────────────────────────────────────────────


def _get_f(row, key: str):
    """Safely read a float column from a sqlite3.Row; returns None if absent."""
    try:
        v = row[key]
        return float(v) if v is not None else None
    except (IndexError, KeyError):
        return None


def _get_s(row, key: str) -> str:
    """Safely read a text column from a sqlite3.Row; returns '' if absent/null."""
    try:
        v = row[key]
    except (IndexError, KeyError):
        return ""
    return v if isinstance(v, str) else ""
