"""App stylesheet and colour-mapping helpers."""

from __future__ import annotations

from PySide6.QtGui import QColor

from core.constants import CAMELOT_COLORS
from core.matcher import KEY_SCORE_LEVELS


# ── Colour theme ─────────────────────────────────────────────────────────────
class T:
    """Single source of truth for all UI colours."""

    BG = "#141416"  # main background (cool near-black)
    BLUE = "#3d7fd4"  # primary accent (buttons, slider)
    ORANGE = "#f79520"  # warnings / on-air
    GREEN = "#bdf164"  # status ok / playing
    INPUT_BG = "#2a2b2e"  # text-field background
    BTN_BG = "#222326"  # button background
    BTN_HOVER = "#2e3035"  # button hover
    BTN_ON = "#3d7fd4"  # button active/checked

    PANEL = "#1a1b1e"  # header bars (slightly blue-tinted)
    BORDER = "#28292d"  # subtle rule lines
    TEXT = "#ced2d8"  # primary text (cool light grey)
    TEXT_DIM = "#5e6878"  # header labels (blue-grey, like Traktor)
    TEXT_VDIM = "#3a3d48"  # very dim / disabled
    TABLE_BG = "#111214"  # table row background
    SELECTION = "#1e3c5e"  # table selection — Traktor's browser selection
    TRAKTOR_BLUE = "#1e8dfe"  # Traktor's active-button blue (SYNC, QUANT, AUTO)


def scroll_page(page):
    """*page* inside a scroll area: a vertical bar appears only when the
    content is taller than the space, never a horizontal one.

    Settings and Library open inside the main window, which docked is only
    as tall as Traktor's library area — groups past the bottom were cut off.
    """
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QFrame, QScrollArea

    area = QScrollArea()
    area.setWidget(page)
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.Shape.NoFrame)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    return area


def make_tab_widget():
    """A QTabWidget configured the way this app wants tab bars.

    Qt elides tab labels the moment it thinks they don't fit, which turns
    "General" into "Gene…" while a longer neighbour stays intact. Labels here
    are short and meaningful, so eliding them is never the right trade —
    scroll instead if the window is genuinely too narrow.
    """
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QTabWidget

    w = QTabWidget()
    bar = w.tabBar()
    bar.setElideMode(Qt.TextElideMode.ElideNone)
    bar.setExpanding(False)
    bar.setUsesScrollButtons(True)
    bar.setDrawBase(False)
    return w


def _render_tick(path: str, size: int, scale: float) -> None:
    """Paint a white check mark of ``size`` px into ``path`` as a PNG."""
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QImage, QPainter, QPen

    px = int(size * scale)
    img = QImage(px, px, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.scale(scale, scale)
    pen = QPen(QColor("#ffffff"))
    pen.setWidthF(1.9)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    # Three points of a tick, as fractions of the indicator box.
    p.drawPolyline(
        [
            QPointF(size * 0.24, size * 0.53),
            QPointF(size * 0.43, size * 0.72),
            QPointF(size * 0.78, size * 0.30),
        ]
    )
    p.end()
    img.save(path, "PNG")


def _tick_url() -> str:
    """URL of the checkbox tick image, or "none" if it can't be written.

    Qt stylesheets can only draw a check glyph from an image file and the app
    ships no asset bundle, so the mark is painted here and cached in the temp
    directory (plus an ``@2x`` copy, which Qt picks up automatically on Retina
    displays). If anything fails the indicator just stays a solid blue fill,
    which is what it looked like before.
    """
    import os
    import tempfile

    base = os.path.join(tempfile.gettempdir(), "tk-checkbox-tick.png")
    try:
        _render_tick(base, 14, 1.0)
        _render_tick(base.replace(".png", "@2x.png"), 14, 2.0)
    except Exception:
        return "none"
    return f'url("{base.replace(chr(92), "/")}")'


_TICK = _tick_url()

STYLE = f"""
QMainWindow, QDialog {{ background: {T.BG}; color: {T.TEXT}; }}
QWidget {{ background: {T.BG}; color: {T.TEXT};
          font-family: "Inter", "Helvetica Neue", Helvetica, Arial; font-size: 12px; }}

/* ── Table ────────────────────────────────────────────────────────────── */
QTableWidget {{
    background: {T.TABLE_BG}; border: none;
    gridline-color: transparent;
    selection-background-color: {T.SELECTION}; selection-color: #fff;
    alternate-background-color: #141618;
    font-size: 14px;   /* matches Traktor's browser list (Traktor font @ 14) */
}}
QTableWidget::item {{ padding: 2px 8px; border: none; }}
QTableWidget::item:selected {{ background: {T.SELECTION}; color: #fff; }}
/* The results browser mirrors Traktor's: Roboto (Apache-2.0 — the face Traktor
   itself falls back to), at Traktor's browser size. Scoped to ResultsTable so
   the rest of the app keeps Inter. */
ResultsTable {{ font-family: "Roboto"; font-size: 13px; }}
ResultsTable QHeaderView::section {{ font-family: "Roboto"; }}
QHeaderView::section {{
    background: {T.PANEL}; color: {T.TEXT_DIM};
    padding: 4px 8px; border: none;
    border-bottom: 1px solid {T.BORDER};
    border-right: 1px solid #3a3d4a;
    font-size: 10px; letter-spacing: 1.5px; text-transform: uppercase;
}}

/* ── Buttons ──────────────────────────────────────────────────────────── */
QPushButton {{
    background: {T.BTN_BG}; color: {T.TEXT};
    border: 1px solid #333333; padding: 6px 16px; border-radius: 3px;
}}
QPushButton:hover  {{ background: {T.BTN_HOVER}; border-color: {T.BLUE}; }}
QPushButton:pressed {{ background: {T.BLUE}; color: #fff; border-color: {T.BLUE}; }}
QPushButton:disabled {{ color: {T.TEXT_VDIM}; border-color: #1e1e1e; }}
QPushButton#primary {{
    background: {T.BLUE}; color: #fff; font-weight: bold;
    padding: 8px 22px; border: none;
}}
QPushButton#primary:hover   {{ background: #5a9bf8; }}
QPushButton#primary:pressed {{ background: #2a6ee0; }}

/* ── Combo box ────────────────────────────────────────────────────────── */
QComboBox {{
    background: {T.BTN_BG}; color: {T.TEXT};
    border: 1px solid #333333; padding: 5px 10px; border-radius: 3px;
}}
QComboBox::drop-down {{ border: none; width: 20px; }}
QComboBox QAbstractItemView {{
    background: {T.PANEL}; color: {T.TEXT};
    selection-background-color: {T.BLUE};
}}

/* ── Inputs ───────────────────────────────────────────────────────────── */
QLineEdit {{
    background: {T.INPUT_BG}; color: {T.TEXT};
    border: 1px solid #333333; padding: 5px 8px; border-radius: 3px;
}}
QLineEdit:focus {{ border-color: {T.BLUE}; }}

QSpinBox {{
    background: {T.BTN_BG}; color: {T.TEXT};
    border: 1px solid #333333; padding: 4px 8px; border-radius: 3px;
}}

/* ── Slider ───────────────────────────────────────────────────────────── */
QSlider::groove:horizontal {{
    height: 4px; background: #333333; border-radius: 2px;
}}
QSlider::handle:horizontal {{
    background: {T.BLUE}; width: 14px; height: 14px;
    margin: -5px 0; border-radius: 7px;
}}
QSlider::sub-page:horizontal {{ background: {T.BLUE}; border-radius: 2px; }}
QSlider::handle:horizontal:disabled {{ background: {T.TEXT_VDIM}; }}
QSlider::sub-page:horizontal:disabled {{ background: {T.TEXT_VDIM}; }}

/* ── Progress bar ─────────────────────────────────────────────────────── */
QProgressBar {{
    background: {T.TABLE_BG}; border: 1px solid {T.BORDER};
    border-radius: 3px; text-align: center; color: {T.TEXT};
}}
QProgressBar::chunk {{ background: {T.BLUE}; border-radius: 2px; }}

/* ── Group box ────────────────────────────────────────────────────────── */
QGroupBox {{
    border: 1px solid {T.BORDER}; border-radius: 4px;
    margin-top: 14px; padding: 10px 12px 12px 12px;
    color: {T.TEXT_DIM}; font-size: 10px; letter-spacing: 1px;
}}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 6px; }}

/* ── Tabs ─────────────────────────────────────────────────────────────── */
/* Left-aligned, flat tabs in the app's own palette. Without this, macOS draws
   its native pill tab bar — a white strip with a bright blue capsule, which
   is jarring against a near-black UI. */
QTabWidget::pane {{
    background: {T.BG};
    border: none;
    border-top: 1px solid {T.BORDER};
    top: -1px;
}}
QTabWidget::tab-bar {{ alignment: left; left: 8px; }}
QTabBar {{ background: transparent; qproperty-drawBase: 0; }}
/* No letter-spacing here: QTabBar sizes a tab from QFontMetrics of the base
   font, which does not account for a stylesheet's letter-spacing, so the text
   ends up wider than the width reserved for it and Qt elides it to
   "Gene…" / "Connecti…". */
QTabBar::tab {{
    background: transparent;
    color: {T.TEXT_DIM};
    border: none;
    border-bottom: 2px solid transparent;
    padding: 8px 14px;
    margin: 0 1px;
    font-size: 13px;   /* the window's navigation: above the 12px body text */
}}
QTabBar::tab:hover {{ color: {T.TEXT}; background: {T.PANEL}; }}
QTabBar::tab:selected {{
    color: {T.TEXT};
    background: {T.PANEL};
    border-bottom: 2px solid {T.BLUE};
}}
QTabBar::tab:disabled {{ color: {T.TEXT_VDIM}; }}

/* ── Misc ─────────────────────────────────────────────────────────────── */
QLabel {{ color: {T.TEXT}; }}

/* Checkboxes carry their own breathing room: `spacing` is the gap Qt leaves
   between the indicator and the label (0 by default once the indicator is
   styled, which jams the text against the box), and the vertical padding stops
   stacked rows from touching each other in tight grids. */
QCheckBox {{
    color: #aaaaaa;
    spacing: 8px;
    padding: 4px 2px;
}}
QCheckBox:hover {{ color: {T.TEXT}; }}
QCheckBox:disabled {{ color: {T.TEXT_VDIM}; }}
QCheckBox::indicator {{
    width: 14px; height: 14px;
    border: 1px solid {T.TEXT_VDIM}; border-radius: 3px; background: {T.BTN_BG};
}}
QCheckBox::indicator:hover {{ border-color: {T.TEXT_DIM}; background: {T.BTN_HOVER}; }}
QCheckBox::indicator:checked {{
    background: {T.BLUE}; border-color: {T.BLUE}; image: {_TICK};
}}
QCheckBox::indicator:checked:hover {{ background: #4a8de0; border-color: #4a8de0; }}
QCheckBox::indicator:disabled {{ border-color: {T.BORDER}; background: {T.PANEL}; }}
QCheckBox::indicator:checked:disabled {{ background: #2c4a72; border-color: #2c4a72; }}

/* ── List with checkboxes (playlist selection) ─────────────────────────── */
/* QListWidget items don't inherit the QCheckBox indicator rules, so without
   these the tick boxes render as the platform default — nearly invisible on a
   dark row — and Qt's stock alternate colour washes every other line out. */
QListWidget {{
    background: {T.TABLE_BG};
    border: 1px solid {T.BORDER}; border-radius: 4px;
    alternate-background-color: {T.TABLE_BG};
    outline: none;
}}
QListWidget::item {{
    padding: 4px 6px; border: none; color: {T.TEXT};
}}
/* Belt and braces: Qt falls back to the palette's AlternateBase (white on a
   default light palette) unless the alternate row is styled explicitly, which
   made every other playlist unreadable. */
QListWidget::item:alternate {{ background: {T.TABLE_BG}; color: {T.TEXT}; }}
QListWidget::item:hover {{ background: {T.PANEL}; }}
QListWidget::item:selected {{ background: {T.SELECTION}; color: #fff; }}
QListWidget::indicator {{
    width: 14px; height: 14px; margin-right: 6px;
    border: 1px solid {T.TEXT_VDIM}; border-radius: 3px; background: {T.BTN_BG};
}}
QListWidget::indicator:hover {{ border-color: {T.BLUE}; }}
QListWidget::indicator:checked {{
    background: {T.BLUE}; border-color: {T.BLUE};
}}

QRadioButton {{ color: #aaaaaa; spacing: 8px; }}
QRadioButton::indicator {{
    width: 14px; height: 14px;
    border: 1px solid {T.TEXT_VDIM}; border-radius: 7px; background: {T.BTN_BG};
}}
QRadioButton::indicator:checked {{ background: {T.BLUE}; border-color: {T.BLUE}; }}

QScrollBar:vertical {{
    background: transparent; width: 8px; margin: 2px 1px 2px 0;
}}
QScrollBar::handle:vertical {{
    background: #2c2e38; border-radius: 4px; min-height: 32px;
    border: 1px solid #383a46;
}}
QScrollBar::handle:vertical:hover  {{ background: {T.BLUE}; border-color: {T.BLUE}; }}
QScrollBar::handle:vertical:pressed {{ background: #5a9bf8; border-color: #5a9bf8; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; border: none; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: none; }}

QScrollBar:horizontal {{
    background: transparent; height: 8px; margin: 0 2px 1px 2px;
}}
QScrollBar::handle:horizontal {{
    background: #2c2e38; border-radius: 4px; min-width: 32px;
    border: 1px solid #383a46;
}}
QScrollBar::handle:horizontal:hover  {{ background: {T.BLUE}; border-color: {T.BLUE}; }}
QScrollBar::handle:horizontal:pressed {{ background: #5a9bf8; border-color: #5a9bf8; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; border: none; }}
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ background: none; }}

QScrollArea {{ border: none; background: transparent; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}

QStatusBar {{ background: {T.TABLE_BG}; color: {T.TEXT_VDIM}; font-size: 11px; }}
QMenuBar {{ background: {T.TABLE_BG}; color: {T.TEXT_DIM}; }}
QMenuBar::item:selected {{ background: {T.BTN_BG}; color: {T.TEXT}; }}
QMenu {{ background: {T.PANEL}; color: {T.TEXT}; border: 1px solid #333333; }}
QMenu::item:selected {{ background: {T.BLUE}; }}

QToolTip {{
    background: {T.PANEL}; color: {T.TEXT};
    border: 1px solid {T.BORDER}; border-radius: 5px;
    padding: 6px 9px; font-size: 11px;
}}
"""


def apply_dark_tooltip_palette() -> None:
    """Force tooltips to the app's dark theme via the palette.

    The ``QToolTip`` stylesheet rule above is not always honoured for the
    tooltip *window* itself — on macOS Qt paints the native light tooltip
    background regardless (observed on Qt 6.11 / macOS 26), leaving our
    light-on-dark rich-text tooltips unreadable. The native path reads
    ``QToolTip.palette()``, so set the palette too; the stylesheet still
    covers platforms where it does apply.
    """
    from PySide6.QtGui import QPalette
    from PySide6.QtWidgets import QToolTip

    pal = QToolTip.palette()
    pal.setColor(QPalette.ColorRole.ToolTipBase, QColor(T.PANEL))
    pal.setColor(QPalette.ColorRole.ToolTipText, QColor(T.TEXT))
    QToolTip.setPalette(pal)


def load_app_fonts(fonts_dir) -> list[str]:
    """Register bundled UI fonts so the stylesheet's font-family resolves.

    Traktor's browser uses NI's proprietary "Traktor" face (not redistributable);
    Inter is a close, OFL-licensed grotesque used as the app's primary family, and
    Roboto (Apache-2.0 — the face Traktor itself falls back to) backs the results
    list to mirror Traktor's browser. Returns the registered family names (empty
    if none loaded — the stylesheet then falls back to Helvetica Neue).
    """
    from pathlib import Path

    from PySide6.QtGui import QFontDatabase

    families: list[str] = []
    for ttf in ("Inter.ttf", "Roboto-Regular.ttf"):
        path = Path(fonts_dir) / ttf
        if not path.exists():
            continue
        fid = QFontDatabase.addApplicationFont(str(path))
        if fid != -1:
            families.extend(QFontDatabase.applicationFontFamilies(fid))
    return families


# ── The Key column ─────────────────────────────────────────────────────────
#
# Grouped by what the relationship does in a mix, not by where the number
# falls. The composite traffic light could not do this job: its first cliff is
# at 80, and every relationship worth mixing on scores above it, so a perfect
# match and a neighbour came out the same green.
#
# Green family — the moves that work: the same key, the neighbour on the wheel
# (an energy step), and the relative major/minor (a mood step, the same
# notes). The relative gets its own hue rather than a duller green because it
# is a *different kind* of move, not a worse one — the score orders them, the
# hue says which is which.
# Amber — the diagonal, +-1 across modes. Usable, least reliable of the set.
# Red fading to grey — outside the mixing set, dimmer the further round the
# wheel, so nothing out there competes for the eye.
_KEY_LEVEL_COLORS: dict[float, str] = {
    100.0: "#00e676",  # same key
    90.0: "#76ff03",  # +-1 on the wheel, same mode — the energy move
    75.0: "#00e5c0",  # relative major/minor — the mood move
    50.0: "#ffb300",  # +-1 across modes — the diagonal
}

#: The unrelated tail, a step per level so two distant keys still differ.
_KEY_TAIL_FROM = (192, 57, 43)
_KEY_TAIL_TO = (74, 74, 85)
_KEY_TAIL_LEVELS: tuple[float, ...] = tuple(
    lvl for lvl in KEY_SCORE_LEVELS if lvl not in _KEY_LEVEL_COLORS
)

#: No key on the track. ``_score_key`` returns 0.0 for *unknown*, which is not
#: the same as *clashes* — so it takes the table's own missing-value grey
#: rather than the bottom of the tail.
KEY_UNKNOWN_COLOR = "#444466"


def key_score_color(score: float) -> QColor:
    """One colour per harmonic relationship, for the Key column."""
    if score <= 0.0:
        return QColor(KEY_UNKNOWN_COLOR)
    level = next((lvl for lvl in KEY_SCORE_LEVELS if score >= lvl), None)
    if level is None:  # below every level the tables hold
        return QColor(KEY_UNKNOWN_COLOR)
    pinned = _KEY_LEVEL_COLORS.get(level)
    if pinned is not None:
        return QColor(pinned)
    i = _KEY_TAIL_LEVELS.index(level)
    f = i / (len(_KEY_TAIL_LEVELS) - 1) if len(_KEY_TAIL_LEVELS) > 1 else 0.0
    return QColor(
        *(round(a + (b - a) * f) for a, b in zip(_KEY_TAIL_FROM, _KEY_TAIL_TO, strict=True))
    )


def score_color(score: float) -> QColor:
    """Traffic-light colour for the composite match score."""
    if score >= 80:
        return QColor("#00e676")
    if score >= 55:
        return QColor("#ffb300")
    return QColor("#ef5350")


# Anchors for the smooth score gradient: red → amber → green, keyed by score/100.
_SCORE_STOPS: tuple[tuple[float, tuple[int, int, int]], ...] = (
    (0.0, (239, 83, 80)),  # #ef5350 red
    (0.5, (255, 179, 0)),  # #ffb300 amber
    (1.0, (0, 230, 118)),  # #00e676 green
)


def score_color_smooth(score: float) -> QColor:
    """Composite score colour that follows the score continuously (red→amber→green),
    interpolating between the traffic-light anchors instead of snapping in steps."""
    t = max(0.0, min(1.0, score / 100.0))
    stops = _SCORE_STOPS
    for (t0, c0), (t1, c1) in zip(stops, stops[1:], strict=False):
        if t <= t1:
            f = 0.0 if t1 == t0 else (t - t0) / (t1 - t0)
            return QColor(
                round(c0[0] + (c1[0] - c0[0]) * f),
                round(c0[1] + (c1[1] - c0[1]) * f),
                round(c0[2] + (c1[2] - c0[2]) * f),
            )
    return QColor(*stops[-1][1])


def component_color(score: float) -> QColor:
    """Dim → amber → green gradient for individual BPM / Key / Energy %."""
    if score >= 85:
        return QColor("#00e676")
    if score >= 70:
        return QColor("#69f0ae")
    if score >= 50:
        return QColor("#ffb300")
    if score >= 25:
        return QColor("#ff7043")
    return QColor("#555555")


def delta_color(delta: float) -> QColor:
    """Green near 0 BPM, dims as the gap grows."""
    a = abs(delta)
    if a <= 1.0:
        return QColor("#00e676")
    if a <= 2.0:
        return QColor("#b5f542")
    if a <= 3.0:
        return QColor("#ffb300")
    return QColor("#555555")


def bpm_pct_color(pct_diff: float) -> QColor:
    """Traffic-light colour for BPM % deviation from source."""
    a = abs(pct_diff)
    if a <= 0.5:
        return QColor("#00e676")
    if a <= 5.0:
        return QColor("#b5f542")
    if a <= 10.0:
        return QColor("#f78f1e")
    if a <= 15.0:
        return QColor("#ff5722")
    if a <= 20.0:
        return QColor("#f44336")
    return QColor("#666677")


def camelot_color(camelot: str) -> QColor:
    return QColor(CAMELOT_COLORS.get(camelot, "#d0d0d0"))
