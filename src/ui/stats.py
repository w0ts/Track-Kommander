"""StatsTab — live library feature dashboard.

Distributions, a usefulness ranking (how much each feature actually varies), and
a correlation matrix to spot features that move together (redundant). Everything
is painted with QPainter from :mod:`core.stats`, recomputed each time the
dashboard is opened.
"""

from __future__ import annotations

import logging
import textwrap
from dataclasses import dataclass

import numpy as np
from PySide6.QtCore import QPointF, QRectF, QSettings, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetricsF,
    QLinearGradient,
    QPainter,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from core.features import BY_KEY, FEATURES, axis_enabled_key
from core.stats import (
    correlation,
    feature_stats,
    key_distribution,
    key_spread,
    load_library_values,
)
from ui.now_playing import _FlowLayout  # shared flow layout for the histogram grid
from ui.style import T, camelot_color

_INK = "#e7ecf4"
_DIM = "#97a1b4"
_FAINT = "#5d6678"
_PANEL = "#141821"
_LINE = "#272e3b"

# Per-feature "shown as a matching axis" default, for reading the persisted flag.
_FEAT_DEFAULT = {f.key: f.default_enabled for f in FEATURES}


def _hsl(h: float, s: float, ll: float, a: float = 1.0) -> QColor:
    c = QColor()
    c.setHslF((h % 360) / 360.0, max(0.0, min(1.0, s)), max(0.0, min(1.0, ll)), a)
    return c


def _value(key: str, v: float) -> str:
    """A raw value of feature *key* as a person reads it: a date for the
    import day, a year for the release year, else a plain number."""
    spec = BY_KEY.get(key)
    return spec.fmt_value(v) if spec is not None and spec.fmt_value else _fmt(v)


def _width(key: str, w: float) -> str:
    """A spread of feature *key* (σ): days or years for the dates."""
    spec = BY_KEY.get(key)
    return spec.fmt_width(w) if spec is not None and spec.fmt_width else _fmt(w)


def _precise(key: str, v: float) -> str:
    """For the tooltip: three decimals, unless the feature has its own form."""
    spec = BY_KEY.get(key)
    return spec.fmt_value(v) if spec is not None and spec.fmt_value else f"{v:.3f}"


def _precise_width(key: str, w: float) -> str:
    spec = BY_KEY.get(key)
    return spec.fmt_width(w) if spec is not None and spec.fmt_width else f"{w:.3f}"


def _fmt(v: float) -> str:
    av = abs(v)
    return f"{v:.0f}" if av >= 100 else (f"{v:.1f}" if av >= 10 else f"{v:.2f}")


log = logging.getLogger(__name__)

#: Narrowest a distribution card may be and still read. The flow layout
#: fits as many as it can at this width and shares the row out between them.
_CARD_MIN_W = 250

_AUTO_PICK_TIP = (
    "Within each cluster of correlated dimensions (chains included, e.g. "
    "HFC↔Bass↔Brightness), keep the most discriminative one and drop the "
    "rest. Drag the threshold to see which rows it would drop."
)


@dataclass(frozen=True)
class AutoPickPlan:
    """What auto-pick would do: which correlated features survive, which go."""

    keep: list
    drop: list

    @property
    def is_empty(self) -> bool:
        return not self.drop


def auto_pick_plan(
    keys: list, matrix: list, *, spread: dict, locked: set, threshold: float
) -> AutoPickPlan:
    """Cluster features by correlation and keep one of each cluster.

    Clusters are transitive — A↔B↔C is one group even with no direct A↔C —
    so a chain of related measures collapses to its single most
    discriminative member. Locked core dimensions always survive, and their
    presence is what makes the rest of their cluster redundant.

    Returns the plan rather than applying it, so the slider can show what
    it would do before anything changes.
    """
    n = len(keys)
    if n < 2:
        return AutoPickPlan([], [])

    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i in range(n):
        for j in range(i + 1, n):
            if abs(matrix[i][j]) >= threshold:
                ri, rj = find(i), find(j)
                if ri != rj:
                    parent[ri] = rj

    clusters: dict = {}
    for i in range(n):
        clusters.setdefault(find(i), []).append(keys[i])

    keep, drop = [], []
    for members in clusters.values():
        if len(members) < 2:
            continue  # nothing redundant in a singleton
        locked_here = [k for k in members if k in locked]
        keepers = set(locked_here) or {max(members, key=lambda k: spread.get(k, 0.0))}
        for k in members:
            if k in locked:
                continue
            (keep if k in keepers else drop).append(k)
    return AutoPickPlan(keep, drop)


#: Shortest a bar may be while still holding tracks. Without it the pinned
#: CLAP features — 829 of 872 in one bin — drew their remaining tracks a
#: third of a pixel tall and the card looked empty.
_MIN_BAR_PX = 2.0


def bar_height(count: int, tallest: int, plot_height: float) -> float:
    """How tall a histogram bar should be. Empty bins stay empty; a bin that
    holds anything is never invisible."""
    if count <= 0 or tallest <= 0 or plot_height <= 0:
        return 0.0
    exact = count / tallest * plot_height
    return min(plot_height, max(exact, min(_MIN_BAR_PX, plot_height)))


def short_axis_labels(labels: list[str], limit: int) -> list[str]:
    """Shorten labels for the correlation map, keeping them distinguishable.

    A fixed cut turns "Bass range" and "Bass notes" both into "Bass ", which
    leaves two rows of the matrix unreadable. Labels that would collide keep
    as many characters as they need to differ; the rest stay short.
    """
    longest = max((len(lbl) for lbl in labels), default=0)
    out: list[str] = []
    for label in labels:
        length = limit
        # Grow only this label, and only while something else would read the
        # same — a row with no clash stays as short as it can.
        while length < longest:
            cut = label[:length]
            others = [o for o in labels if o != label and o[:length] == cut]
            if not others:
                break
            length += 1
        out.append(label[:length])
    return out


_TIP_WIDTH = 72  # characters — Qt does not wrap a plain-text tooltip
_INDEPENDENT_R = 0.3  # below this |r| two features say different things


def feature_explainer(key: str) -> str:
    """ "Label: low ←→ high" and what the feature measures, wrapped for a tooltip."""
    spec = BY_KEY.get(key)
    if spec is None:
        return ""
    head = f"{spec.label}: {spec.poles[0]} ←→ {spec.poles[1]}"
    if not spec.desc:
        return head
    return head + "\n" + textwrap.fill(spec.desc, _TIP_WIDTH)


def correlation_note(a: str, b: str, r: float, threshold: float = 0.85) -> str:
    """What a correlation of *r* between features *a* and *b* means for matching."""
    if abs(r) >= threshold:
        if r > 0:
            return (
                f"{a} and {b} measure nearly the same thing here — matching "
                "on both counts it twice. Keep one."
            )
        return f"{b} falls almost exactly as {a} rises — one is the other upside down. Keep one."
    if abs(r) < _INDEPENDENT_R:
        return f"{a} and {b} are independent — each adds its own information."
    if r > 0:
        return f"{a} and {b} tend to rise together, but each still adds something."
    return f"{b} generally falls as {a} rises, but each still adds something."


def spread_note(spread: float | None, *, relational: bool) -> str | None:
    """Why a feature has no spread bar, or None when it has one.

    A relational axis (Key, Similarity, Style) compares against the current
    track and has no distribution of its own. A scalar one with no value is a
    different thing entirely: nothing has been analysed for it yet.
    """
    if spread is not None:
        return None
    return "relational — no spread" if relational else "not analysed yet — run a scan"


def _spread_color(score: float) -> QColor:
    # red (useless) → amber → green (discriminative)
    hue = 0 + (score / 100.0) * 130
    return _hsl(hue, 0.6, 0.55)


class _Hist(QWidget):
    """One feature's distribution: histogram + IQR band + median + spread score.

    Clicking it uses or drops that dimension — the same decision as the rank
    list, taken where the evidence for it is. A dropped card keeps its shape
    but goes grey, so the grid reads at a glance as the set that is live.
    """

    toggled = Signal(str, bool)

    def __init__(self, st, parent=None, *, enabled: bool = True):
        super().__init__(parent)
        self.st = st
        self.enabled = enabled
        self.setMinimumSize(_CARD_MIN_W - 16, 166)
        self.setMaximumHeight(174)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(
            f"{feature_explainer(st.key) or st.label}\n\n"
            f"n={st.n}   mean={_precise(st.key, st.mean)}   "
            f"median={_precise(st.key, st.median)}\n"
            f"σ={_precise_width(st.key, st.std)}   "
            f"range {_precise(st.key, st.min)}–{_precise(st.key, st.max)}\n"
            f"IQR {_precise(st.key, st.p25)}–{_precise(st.key, st.p75)}   "
            f"spread {st.spread_score:.0f}/100\n\n"
            "Click to use or drop this dimension for matching."
        )

    def _sat(self, saturation: float) -> float:
        """Colour when the dimension is used, grey when it is not."""
        return saturation if self.enabled else 0.0

    def mousePressEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            return
        self.enabled = not self.enabled
        self.update()
        self.toggled.emit(self.st.key, self.enabled)

    def sizeHint(self):
        return QSize(280, 168)

    def paintEvent(self, _e):
        st = self.st
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()

        title = QFont()
        title.setPointSize(11)
        title.setBold(True)
        small = QFont()
        small.setPointSize(10)

        # Title in the metric's own colour.
        p.setFont(title)
        p.setPen(_hsl(st.hue, self._sat(0.55), 0.70))
        short = st.short
        if "±" in short:
            base = short[:-1]
            base_w = QFontMetricsF(title).horizontalAdvance(base)
            p.drawText(2, 16, base)
            pm_font = QFont()
            pm_font.setPointSize(13)
            pm_font.setBold(True)
            p.setFont(pm_font)
            p.setPen(_hsl(st.hue, self._sat(0.80), 0.68))
            p.drawText(int(2 + base_w) + 1, 16, "±")
        else:
            p.drawText(2, 16, short)
        # spread badge, top-right
        p.setFont(small)
        p.setPen(_spread_color(st.spread_score) if self.enabled else QColor(_FAINT))
        p.drawText(
            QRectF(0, 2, W - 2, 16), Qt.AlignmentFlag.AlignRight, f"spread {st.spread_score:.0f}"
        )

        top, bot = 26, H - 18
        left, right = 2, W - 2
        plotH = bot - top
        x0, x1 = st.edges[0], st.edges[-1]
        span = (x1 - x0) or 1.0

        def X(v):
            return left + (v - x0) / span * (right - left)

        maxc = max(st.counts) or 1

        def Y(c):
            return top + plotH - bar_height(c, maxc, plotH)

        # IQR band
        p.fillRect(
            QRectF(X(st.p25), top, max(0.0, X(st.p75) - X(st.p25)), plotH),
            _hsl(st.hue, self._sat(0.4), 0.55, 0.16),
        )
        # bars
        for i, c in enumerate(st.counts):
            if not c:
                continue
            bx0, bx1 = X(st.edges[i]), X(st.edges[i + 1])
            bw = max(1.0, bx1 - bx0 - 1)
            y = Y(c)
            grad = QLinearGradient(0, y, 0, top + plotH)
            grad.setColorAt(0, _hsl(st.hue, self._sat(0.65), 0.58, 0.95))
            grad.setColorAt(1, _hsl(st.hue, self._sat(0.65), 0.45, 0.5))
            p.fillRect(QRectF(bx0, y, bw, top + plotH - y), QBrush(grad))
        # baseline
        p.setPen(QColor("#2a3140"))
        p.drawLine(QPointF(left, top + plotH), QPointF(right, top + plotH))
        # median (dashed)
        pen = QPen(QColor("#eef2f8"))
        pen.setStyle(Qt.PenStyle.DashLine)
        pen.setWidthF(1.2)
        p.setPen(pen)
        mx = X(st.median)
        p.drawLine(QPointF(mx, top), QPointF(mx, top + plotH))
        # axis + stats line
        p.setFont(small)
        p.setPen(QColor(_FAINT))
        p.drawText(QRectF(left, bot, 90, 16), Qt.AlignmentFlag.AlignLeft, _value(st.key, x0))
        p.drawText(QRectF(right - 90, bot, 90, 16), Qt.AlignmentFlag.AlignRight, _value(st.key, x1))
        p.setPen(QColor(_DIM))
        p.drawText(
            QRectF(left, bot, right - left, 16),
            Qt.AlignmentFlag.AlignHCenter,
            f"μ{_value(st.key, st.mean)}  md{_value(st.key, st.median)}  σ{_width(st.key, st.std)}",
        )
        p.end()


class _KeyCard(QWidget):
    """The library across the Camelot wheel — one bar per slot.

    Key is categorical, so a numeric histogram would put 12A eleven bins
    from 1A when they are neighbours. Minor keys first, then major, each in
    its own colour, and the empty slots stay visible: those are the keys a
    set has nowhere to go in.
    """

    def __init__(self, dist: list, spread: float, parent=None):
        super().__init__(parent)
        self.dist = dist
        self.spread = spread
        self.setMinimumSize(_CARD_MIN_W - 16, 166)
        self.setMaximumHeight(174)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        top = max(self.dist, key=lambda sc: sc[1], default=None)
        self.busiest = top if top and top[1] else None
        self.setToolTip(
            "How the crate sits on the Camelot wheel — minor keys (A) then "
            "major (B).\nGaps are keys you have nothing in."
        )

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()

        title = QFont()
        title.setPointSize(11)
        title.setBold(True)
        small = QFont()
        small.setPointSize(10)
        p.setFont(title)
        p.setPen(QColor("#7fe3c0"))
        p.drawText(2, 16, "Key")
        if self.busiest:
            # Alongside the title, not at the foot: the wheel labels live
            # down there and the two ran into each other.
            p.setFont(small)
            p.setPen(QColor(_DIM))
            p.drawText(
                QRectF(0, 2, W - 66, 16),
                Qt.AlignmentFlag.AlignRight,
                f"most {self.busiest[0]} ({self.busiest[1]})",
            )
        p.setFont(small)
        p.setPen(_spread_color(self.spread))
        p.drawText(
            QRectF(0, 2, W - 2, 16), Qt.AlignmentFlag.AlignRight, f"spread {self.spread:.0f}"
        )

        top, bot = 26, H - 18
        left, right = 2, W - 2
        plotH = bot - top
        tallest = max((c for _s, c in self.dist), default=0)
        slot_w = (right - left) / max(len(self.dist), 1)

        for i, (slot, count) in enumerate(self.dist):
            x = left + i * slot_w
            h = bar_height(count, tallest, plotH)
            colour = camelot_color(slot)
            if h:
                grad = QLinearGradient(0, top + plotH - h, 0, top + plotH)
                c0 = QColor(colour)
                c0.setAlpha(240)
                c1 = QColor(colour)
                c1.setAlpha(120)
                grad.setColorAt(0, c0)
                grad.setColorAt(1, c1)
                p.fillRect(QRectF(x, top + plotH - h, max(1.0, slot_w - 1), h), QBrush(grad))
            else:
                # An empty slot still gets a mark, or a gap in the wheel reads
                # as a rendering fault rather than a hole in the crate.
                p.fillRect(QRectF(x, top + plotH - 1, max(1.0, slot_w - 1), 1), QColor("#39405010"))

        p.setPen(QColor("#2a3140"))
        p.drawLine(QPointF(left, top + plotH), QPointF(right, top + plotH))

        # Labels: the numbers along the bottom, with A and B marked at the ends.
        tiny = QFont()
        tiny.setPointSize(8)
        p.setFont(tiny)
        p.setPen(QColor(_FAINT))
        # Four marks is enough to read the wheel; more crowd a 250px card.
        for i, (slot, _c) in enumerate(self.dist):
            if slot in ("1A", "7A", "1B", "7B"):
                p.drawText(
                    QRectF(left + i * slot_w, bot + 2, slot_w * 3, 14),
                    Qt.AlignmentFlag.AlignLeft,
                    slot,
                )
        p.end()


class _Rank(QWidget):
    """Features ranked by spread, each a toggle: click to enable/disable it as a
    matching axis. Disabled rows are dimmed; locked core features (BPM/Key/Rating)
    are always on. Features with no spread (Style/Key) sort to the bottom.

    ``items`` is a list of dicts: {key, short, hue, spread|None, enabled, locked}.
    Emits ``toggled(key, enabled)`` when a non-locked row is clicked.
    """

    toggled = Signal(str, bool)

    _GENERIC_TIP = (
        "Click a row to use / drop that dimension for matching.\n"
        "Bar = spread (how varied it is); low spread → weak to match on."
    )
    _ROW = 27
    _CHECK_W = 24
    _LABEL_W = 100

    def __init__(self, items, parent=None):
        super().__init__(parent)
        self.items = items
        self._preview_drop: set = set()
        self._preview_keep: set = set()
        self.setMinimumHeight(len(self.items) * self._ROW + 6)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMouseTracking(True)
        self.setToolTip(self._GENERIC_TIP)

    def set_preview(self, drop: set, keep: set) -> None:
        """Show what auto-pick would do, without doing it.

        Dragging the threshold is the only way to understand what it means;
        seeing the rows it would drop light up as the slider moves turns a
        number into a decision. Nothing is applied until the button.
        """
        if (drop, keep) == (
            getattr(self, "_preview_drop", set()),
            getattr(self, "_preview_keep", set()),
        ):
            return
        self._preview_drop, self._preview_keep = drop, keep
        self.update()

    def _row_at(self, y: float) -> int:
        i = int(y // self._ROW)
        return i if 0 <= i < len(self.items) else -1

    def mouseMoveEvent(self, e):
        i = self._row_at(e.position().y())
        what = feature_explainer(self.items[i]["key"]) if i >= 0 else ""
        self.setToolTip(f"{what}\n\n{self._GENERIC_TIP}" if what else self._GENERIC_TIP)

    def mousePressEvent(self, e):
        i = self._row_at(e.position().y())
        if i < 0 or self.items[i]["locked"]:
            return
        it = self.items[i]
        it["enabled"] = not it["enabled"]
        self.update()
        self.toggled.emit(it["key"], it["enabled"])

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = self.width()
        bar_x = self._CHECK_W + self._LABEL_W + 8
        bar_w = max(40, w - bar_x - 52)
        f = QFont()
        f.setPointSize(11)
        fm = QFont()
        fm.setPointSize(10)
        for i, s in enumerate(self.items):
            y = i * self._ROW + 3
            on, locked = s["enabled"], s["locked"]
            ink = QColor(_INK if on else _FAINT)
            # A pending auto-pick: red wash over what it would drop, green
            # over what it would keep in that cluster.
            if s["key"] in self._preview_drop:
                p.fillRect(QRectF(0, i * self._ROW, w, self._ROW), QColor(200, 70, 70, 46))
            elif s["key"] in self._preview_keep:
                p.fillRect(QRectF(0, i * self._ROW, w, self._ROW), QColor(80, 190, 120, 40))
            # checkbox glyph
            p.setFont(f)
            p.setPen(QColor(_DIM if on else _FAINT))
            box = "☑" if on else "☐"
            if locked:
                box = "🔒" if on else "☐"
            p.drawText(
                QRectF(0, y, self._CHECK_W, self._ROW - 4),
                Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
                box,
            )
            # label — ± indicator rendered in accent colour at larger size
            short = s["short"]
            if "±" in short:
                base = short[:-1]
                pm_font = QFont()
                pm_font.setPointSize(13)
                pm_w = QFontMetricsF(pm_font).horizontalAdvance("±") + 2
                p.setFont(f)
                p.setPen(ink)
                p.drawText(
                    QRectF(self._CHECK_W, y, self._LABEL_W - pm_w, self._ROW - 4),
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                    base,
                )
                accent = _hsl(s["hue"], 0.80, 0.68)
                p.setFont(pm_font)
                p.setPen(accent)
                p.drawText(
                    QRectF(self._CHECK_W + self._LABEL_W - pm_w, y, pm_w + 2, self._ROW - 4),
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                    "±",
                )
            else:
                p.setFont(f)
                p.setPen(ink)
                p.drawText(
                    QRectF(self._CHECK_W, y, self._LABEL_W, self._ROW - 4),
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                    short,
                )
            # spread bar (or a dash when the feature has no spread metric)
            p.fillRect(QRectF(bar_x, y + 4, bar_w, self._ROW - 12), QColor("#1c2230"))
            spread = s["spread"]
            if spread is not None:
                bw = bar_w * spread / 100.0
                grad = QLinearGradient(bar_x, 0, bar_x + bar_w, 0)
                lo, hi = (0.9, 0.95) if on else (0.28, 0.32)
                grad.setColorAt(0, _hsl(s["hue"], 0.6, 0.5, lo))
                grad.setColorAt(1, _hsl(s["hue"], 0.7, 0.6, hi))
                p.fillRect(QRectF(bar_x, y + 4, bw, self._ROW - 12), QBrush(grad))
                p.setFont(fm)
                p.setPen(QColor(_DIM if on else _FAINT))
                p.drawText(
                    QRectF(bar_x + bar_w + 6, y, 46, self._ROW - 4),
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                    f"{spread:.0f}",
                )
            else:
                p.setFont(fm)
                p.setPen(QColor(_FAINT))
                p.drawText(
                    QRectF(bar_x, y, bar_w, self._ROW - 4),
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                    spread_note(None, relational=s.get("relational", True)) or "",
                )
        p.end()


class _Heatmap(QWidget):
    """Correlation matrix. Warm = positive, blue = negative, grey = independent."""

    cell_clicked = Signal(int, int)  # (row i, col j)

    def __init__(self, corr, threshold: float = 0.85, parent=None):
        super().__init__(parent)
        self.c = corr
        self._threshold = threshold
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        k = max(1, len(corr.keys))
        self._left, self._top = 70, 64
        self.setMinimumHeight(self._top + k * 24 + 4)
        self.setMinimumWidth(self._left + k * 24 + 4)

    def set_threshold(self, threshold: float) -> None:
        self._threshold = threshold
        self.update()

    def _geom(self):
        k = len(self.c.keys)
        if k == 0:
            return 0, 0.0
        avail = min(self.width() - self._left, self.height() - self._top)
        cell = max(14.0, avail / k)
        return k, cell

    @staticmethod
    def _cell_color(r: float) -> QColor:
        a = min(1.0, abs(r))
        hue = 18 if r >= 0 else 210
        return _hsl(hue, 0.7 * a, 0.16 + 0.34 * a)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        k, cell = self._geom()
        if k == 0:
            return
        labels = short_axis_labels(list(self.c.labels), 5)
        # Each axis label in its metric's own colour.
        label_colors = [
            _hsl(BY_KEY[key].hue, 0.55, 0.66) if key in BY_KEY else QColor(_DIM)
            for key in self.c.keys
        ]
        flab = QFont()
        flab.setPointSize(9)
        fcell = QFont()
        fcell.setPointSize(9)
        p.setFont(flab)
        # column labels (rotated)
        for j in range(k):
            cx = self._left + j * cell + cell / 2
            p.save()
            p.translate(cx, self._top - 6)
            p.rotate(-45)
            p.setPen(label_colors[j])
            p.drawText(QRectF(0, -8, 60, 14), Qt.AlignmentFlag.AlignLeft, labels[j])
            p.restore()
        # row labels
        for i in range(k):
            cy = self._top + i * cell
            p.setPen(label_colors[i])
            p.drawText(
                QRectF(0, cy, self._left - 6, cell),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                labels[i],
            )
        # cells — skip diagonal (self-correlation = 1.0, zero information)
        for i in range(k):
            for j in range(k):
                x = self._left + j * cell
                y = self._top + i * cell
                if i == j:
                    p.fillRect(QRectF(x + 1, y + 1, cell - 2, cell - 2), QColor(_PANEL))
                    continue
                r = self.c.matrix[i][j]
                p.fillRect(QRectF(x + 1, y + 1, cell - 2, cell - 2), self._cell_color(r))
                if cell >= 26:
                    p.setFont(fcell)
                    p.setPen(QColor("#e7ecf4") if abs(r) >= 0.45 else QColor(_FAINT))
                    p.drawText(
                        QRectF(x, y, cell, cell),
                        Qt.AlignmentFlag.AlignCenter,
                        f"{r:+.1f}".replace("+0.0", "0").replace("-0.0", "0"),
                    )
                # Ring cells whose |r| is above the threshold (both signs).
                if abs(r) >= self._threshold:
                    ring = QColor("#ffd24a") if r >= 0 else QColor("#6db8ff")
                    pen = QPen(ring)
                    pen.setWidthF(2.0)
                    p.setPen(pen)
                    p.setBrush(Qt.BrushStyle.NoBrush)
                    inset = 2.0
                    p.drawEllipse(QRectF(x + inset, y + inset, cell - 2 * inset, cell - 2 * inset))
        p.end()

    def _cell_at(self, e):
        k, cell = self._geom()
        if k == 0:
            return None
        j = int((e.position().x() - self._left) // cell)
        i = int((e.position().y() - self._top) // cell)
        return (i, j) if (0 <= i < k and 0 <= j < k) else None

    def mouseMoveEvent(self, e):
        cell = self._cell_at(e)
        if cell is None:
            self.setToolTip("")
            return
        i, j = cell
        r = self.c.matrix[i][j]
        a, b = self.c.labels[i], self.c.labels[j]
        if i == j:
            tip = feature_explainer(self.c.keys[i]) or a
        else:
            tip = (
                f"{a} ↔ {b}   r = {r:+.2f}\n"
                + textwrap.fill(correlation_note(a, b, r, self._threshold), _TIP_WIDTH)
                + "\nclick to plot"
            )
        self.setToolTip(tip)

    def mousePressEvent(self, e):
        cell = self._cell_at(e)
        if cell is not None and cell[0] != cell[1]:
            self.cell_clicked.emit(cell[0], cell[1])


class _Scatter(QWidget):
    """Scatter of one feature against another — every track is a point."""

    def __init__(self, xs, ys, xlabel, ylabel, xhue, yhue, r, parent=None, *, keys=("", "")):
        super().__init__(parent)
        self.xs, self.ys = xs, ys
        self._xkey, self._ykey = keys  # for the axis labels: dates read as dates
        self.xlabel, self.ylabel = xlabel, ylabel
        self.xhue, self.yhue, self.r = xhue, yhue, r
        # The ranges are fixed for the widget's life, so worked out once.
        if len(xs):
            self._xrange = (float(xs.min()), float(xs.max()))
            self._yrange = (float(ys.min()), float(ys.max()))
        # The whole plot as last drawn, and the size it was drawn at. One
        # ellipse per track, in Python, on every repaint (a hover, a move of
        # the dialog) was the cost; the picture only changes with the size.
        self._cache: QPixmap | None = None
        self._cache_key: tuple | None = None
        self.setMinimumSize(420, 380)

    def paintEvent(self, _e):
        W, H = self.width(), self.height()
        dpr = self.devicePixelRatioF()
        key = (W, H, dpr)
        if self._cache is None or self._cache_key != key:
            pix = QPixmap(max(1, round(W * dpr)), max(1, round(H * dpr)))
            pix.setDevicePixelRatio(dpr)
            pix.fill(Qt.GlobalColor.transparent)
            painter = QPainter(pix)
            try:
                self._draw_plot(painter, W, H)
            finally:
                painter.end()
            self._cache, self._cache_key = pix, key
        p = QPainter(self)
        p.drawPixmap(0, 0, self._cache)
        p.end()

    def _draw_plot(self, p: QPainter, width: int, height: int) -> None:
        W, H = width, height
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        ml, mr, mt, mb = 52, 16, 30, 44
        pw, ph = W - ml - mr, H - mt - mb
        if len(self.xs) == 0 or pw <= 0 or ph <= 0:
            return
        xmin, xmax = self._xrange
        ymin, ymax = self._yrange
        xs_span = (xmax - xmin) or 1.0
        ys_span = (ymax - ymin) or 1.0

        def X(v):
            return ml + (v - xmin) / xs_span * pw

        def Y(v):
            return mt + ph - (v - ymin) / ys_span * ph

        # frame + grid
        p.setPen(QColor("#2a3140"))
        p.drawRect(QRectF(ml, mt, pw, ph))
        p.setPen(QColor("#1d2433"))
        for f in (0.25, 0.5, 0.75):
            p.drawLine(QPointF(ml + f * pw, mt), QPointF(ml + f * pw, mt + ph))
            p.drawLine(QPointF(ml, mt + f * ph), QPointF(ml + pw, mt + f * ph))
        # points
        dot = _hsl((self.xhue + self.yhue) / 2, 0.6, 0.62, 0.45)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(dot)
        for xv, yv in zip(self.xs, self.ys, strict=True):
            p.drawEllipse(QPointF(X(xv), Y(yv)), 2.6, 2.6)
        # labels
        small = QFont()
        small.setPointSize(10)
        p.setFont(small)
        p.setPen(_hsl(self.xhue, 0.5, 0.62))
        p.drawText(QRectF(ml, H - 28, pw, 15), Qt.AlignmentFlag.AlignHCenter, f"{self.xlabel}  →")
        p.setPen(QColor(_FAINT))
        p.drawText(QRectF(ml, H - 15, 60, 15), Qt.AlignmentFlag.AlignLeft, _value(self._xkey, xmin))
        p.drawText(
            QRectF(ml + pw - 60, H - 15, 60, 15),
            Qt.AlignmentFlag.AlignRight,
            _value(self._xkey, xmax),
        )
        # y label (rotated)
        p.save()
        p.translate(14, mt + ph / 2)
        p.rotate(-90)
        p.setPen(_hsl(self.yhue, 0.5, 0.62))
        p.drawText(QRectF(-ph / 2, -10, ph, 15), Qt.AlignmentFlag.AlignHCenter, f"{self.ylabel}  →")
        p.restore()
        p.setPen(QColor(_FAINT))
        p.drawText(
            QRectF(2, mt - 3, ml - 6, 15), Qt.AlignmentFlag.AlignRight, _value(self._ykey, ymax)
        )
        p.drawText(
            QRectF(2, mt + ph - 11, ml - 6, 15),
            Qt.AlignmentFlag.AlignRight,
            _value(self._ykey, ymin),
        )
        # r readout
        big = QFont()
        big.setPointSize(11)
        big.setBold(True)
        p.setFont(big)
        p.setPen(QColor(_INK))
        p.drawText(
            QRectF(ml, 4, pw, 18),
            Qt.AlignmentFlag.AlignRight,
            f"r = {self.r:+.2f}  ·  n = {len(self.xs)}",
        )


def _section(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet(
        f"color: {_DIM}; font-size: 12px; letter-spacing: 2px; font-weight: 600;"
        f"border-bottom: 1px solid {_LINE}; padding-bottom: 6px; margin-top: 6px;"
    )
    return lbl


class StatsTab(QWidget):
    """Scrollable dashboard of library feature distributions and correlations."""

    metric_toggled = Signal(str, bool)  # (feature key, enabled) — used/dropped as axis

    def __init__(self, parent=None):
        super().__init__(parent)
        self._settings = QSettings()
        self._stats: list = []
        self._values: dict = {}
        self._corr = None
        self._scatter_dialog: QDialog | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        head = QHBoxLayout()
        title = QLabel("Library statistics")
        title.setStyleSheet(f"color: {_INK}; font-size: 18px; font-weight: 700;")
        head.addWidget(title)
        self._subtitle = QLabel("")
        self._subtitle.setStyleSheet(f"color: {_DIM}; font-size: 13px;")
        head.addWidget(self._subtitle)
        head.addStretch()
        self._replay_btn = QPushButton("History replay…")
        self._replay_btn.setToolTip(
            "Replay your Traktor history: where would each matching setup\n"
            "have ranked the track you actually played next?"
        )
        self._replay_btn.clicked.connect(self._open_replay)
        head.addWidget(self._replay_btn)
        from core.runtime import in_traktor

        # Not inside Traktor (User, 2026-11-24); the standalone app keeps it.
        self._replay_btn.setVisible(not in_traktor())
        root.addLayout(head)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(self._scroll.Shape.NoFrame)
        root.addWidget(self._scroll, stretch=1)

        # The auto-pick preview clears itself, so a threshold left mid-drag
        # does not look like a change waiting to be applied. Made once here:
        # reload() runs again after every scan and must not stack timers.
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(2500)
        self._preview_timer.timeout.connect(self._clear_preview)

        # Set when the library changed while the page was hidden; the next
        # show reloads. MainWindow keeps this page for the whole session.
        self._stale = False
        self.reload()

    def set_axes_provider(self, provider) -> None:
        """Where the history replay reads the current matching axes from (the
        Now Playing tab's ``current_axes``)."""
        self._axes_provider = provider

    def _open_replay(self) -> None:
        from ui.replay_dialog import ReplayDialog

        provider = getattr(self, "_axes_provider", None)
        if provider is None:
            return
        dialog = getattr(self, "_replay_dialog", None)
        if dialog is None:
            dialog = self._replay_dialog = ReplayDialog(provider, self)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def mark_stale(self) -> None:
        """The library changed (a scan finished). Reload now if showing,
        otherwise the next time the page is shown."""
        if self.isVisible():
            self.reload()
        else:
            self._stale = True

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._stale:
            self.reload()

    def reload(self) -> None:
        """Rebuild the page from the database. Safe to call again."""
        self._stale = False
        # A preview left running would clear rows of the page being replaced.
        self._preview_timer.stop()
        try:
            values, camelots = load_library_values()
            stats = feature_stats(values)
            corr = correlation(values)
        except Exception as exc:  # DB missing / unreadable
            log.warning("stats: could not read the library", exc_info=True)
            self._show_message(f"Couldn't read the library: {exc}")
            return
        if not stats:
            self._show_message("No analysed tracks yet — scan your library first.")
            return

        self._stats, self._values, self._corr = stats, values, corr
        n = stats[0].n
        self._subtitle.setText(f"·  {n} analysed tracks  ·  {len(stats)} numeric features")

        content = QWidget()
        lay = QVBoxLayout(content)
        lay.setContentsMargins(0, 0, 6, 0)
        lay.setSpacing(10)

        # ── Top: spread bars (left)  ·  correlation map (right) ──
        thr = min(0.99, max(0.50, float(self._settings.value("corr_threshold", 0.85))))
        top_row = QHBoxLayout()
        top_row.setSpacing(20)

        # LEFT — matching-metric bars.
        left = QVBoxLayout()
        left.setSpacing(6)
        left.addWidget(_section("MATCHING METRICS  ·  click to use / drop  ·  bar = spread"))
        hint = QLabel(
            "Tick a dimension to match on it; untick to ignore it. The bar shows "
            "how much it varies across the crate — low bars barely separate "
            "tracks. Core dimensions are always on."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {_FAINT}; font-size: 13px;")
        left.addWidget(hint)
        self._key_score = key_spread(camelots) or None
        self._rank = _Rank(self._rank_items(stats))
        self._rank.toggled.connect(self._on_metric_toggled)
        left.addWidget(self._rank)
        left.addStretch(1)
        top_row.addLayout(left, stretch=1)

        # RIGHT — correlation map + threshold + auto-pick.
        right = QVBoxLayout()
        right.setSpacing(6)
        right.addWidget(_section("CORRELATION  ·  warm = move together, blue = opposite"))

        thr_row = QHBoxLayout()
        thr_row.setSpacing(8)
        thr_cap = QLabel("|r| ≥")
        thr_cap.setStyleSheet(f"color: {_DIM}; font-size: 13px;")
        thr_row.addWidget(thr_cap)
        self._corr_slider = QSlider(Qt.Orientation.Horizontal)
        self._corr_slider.setRange(50, 99)
        self._corr_slider.setValue(int(round(thr * 100)))
        self._corr_slider.setFixedWidth(150)
        self._corr_slider.valueChanged.connect(self._on_corr_threshold)
        thr_row.addWidget(self._corr_slider)
        self._corr_thr_val = QLabel(f"{thr:.2f}")
        self._corr_thr_val.setStyleSheet(f"color: {_INK}; font-size: 13px; min-width: 34px;")
        thr_row.addWidget(self._corr_thr_val)
        thr_row.addStretch()
        self._auto_btn = QPushButton("✨ Auto-pick")
        self._auto_btn.setToolTip(_AUTO_PICK_TIP)
        self._auto_btn.clicked.connect(self._auto_select)
        thr_row.addWidget(self._auto_btn)
        right.addLayout(thr_row)

        # One correlated pair per line; recomputed live as the slider moves.
        self._corr_pairs_lbl = QLabel()
        self._corr_pairs_lbl.setWordWrap(True)
        self._corr_pairs_lbl.setTextFormat(Qt.TextFormat.RichText)
        right.addWidget(self._corr_pairs_lbl)

        self._corr_heat = _Heatmap(corr, threshold=thr)
        self._corr_heat.cell_clicked.connect(self._show_scatter)
        right.addWidget(self._corr_heat)
        tip = QLabel(
            "r runs from −1 to +1: how closely two features rise and fall "
            "together across your library. Near 0 they are independent; near "
            "±1 they measure the same thing, so matching on both counts it "
            "twice — keep one. Hover a cell for what the pair means, the "
            "diagonal for what a feature is; click to plot the pair. Ringed "
            "cells exceed the threshold (gold = positive, blue = inverse)."
        )
        tip.setWordWrap(True)
        tip.setStyleSheet(f"color: {_FAINT}; font-size: 13px;")
        right.addWidget(tip)
        right.addStretch(1)
        top_row.addLayout(right, stretch=1)

        lay.addLayout(top_row)

        # ── Distributions (full width) ──
        lay.addWidget(_section("DISTRIBUTIONS"))
        grid_host = QWidget()
        # The flow decides the width; the cards must not argue with it. A
        # card fixed wider than its slot is drawn over by the next one, which
        # hid the right third of every distribution — so a feature whose
        # values sit at the high end looked like an empty chart.
        flow = _FlowLayout(grid_host, hspacing=10, vspacing=10, min_item_width=_CARD_MIN_W)
        # Key leads: it is the one distribution a DJ reads before a set, and
        # it is categorical, so it needs a card of its own rather than a
        # histogram over key numbers.
        self._cards: dict = {}
        hists = []
        for st in stats:
            h = _Hist(st, enabled=self._metric_enabled(st.key))
            h.toggled.connect(self._on_card_toggled)
            self._cards[st.key] = h
            hists.append(h)
        key_card = _KeyCard(key_distribution(camelots), key_spread(camelots))
        for widget in (key_card, *hists):
            card = QWidget()
            card.setMinimumWidth(_CARD_MIN_W)
            card.setStyleSheet(
                f"background:{_PANEL}; border:1px solid {_LINE}; border-radius:10px;"
            )
            cl = QVBoxLayout(card)
            cl.setContentsMargins(8, 6, 8, 6)
            cl.addWidget(widget)
            flow.addWidget(card)
        lay.addWidget(grid_host)
        lay.addStretch()

        self._update_corr_pairs(thr)

        self._scroll.setWidget(content)

    def _rank_items(self, stats) -> list:
        """Build the interactive ranking rows: every matchable feature with its
        spread (where it has one), enabled flag, and locked flag. Spread desc;
        relational features (no spread) sink to the bottom."""
        by_key = {s.key: s for s in stats}
        # Key is categorical, not relational: its bar is how evenly the crate
        # uses the Camelot wheel. See core.stats.key_spread. Computed by
        # reload() from the same read as everything else.
        key_score = getattr(self, "_key_score", None)
        items = []
        for f in FEATURES:
            s = by_key.get(f.key)
            items.append(
                {
                    "key": f.key,
                    "short": f.short_label,
                    "hue": f.hue,
                    # The key axis is relational, so it has no column to take a
                    # spread from; give it the wheel's own spread or it sinks to
                    # the bottom and looks like a dimension that does nothing.
                    "spread": (
                        s.spread_score if s is not None else key_score if f.key == "key" else None
                    ),
                    "relational": f.kind != "scalar",
                    "enabled": self._settings.value(
                        axis_enabled_key(f.key), f.default_enabled, type=bool
                    ),
                    "locked": f.locked,
                }
            )
        items.sort(key=lambda it: (it["spread"] is None, -(it["spread"] or 0.0)))
        return items

    def _on_card_toggled(self, key: str, enabled: bool) -> None:
        """A distribution card was clicked — same decision as the rank row,
        so the row has to follow it."""
        self._on_metric_toggled(key, enabled)
        for item in self._rank.items:
            if item["key"] == key:
                item["enabled"] = enabled
                self._rank.update()
                break

    def _on_metric_toggled(self, key: str, enabled: bool) -> None:
        """A discriminative-list row was toggled → persist and notify the main
        window so the now-playing axis panel rebuilds."""
        self._settings.setValue(axis_enabled_key(key), enabled)
        self.metric_toggled.emit(key, enabled)

    def _on_corr_threshold(self, val: int) -> None:
        thr = val / 100.0
        self._settings.setValue("corr_threshold", thr)
        self._corr_thr_val.setText(f"{thr:.2f}")
        self._corr_heat.set_threshold(thr)
        self._update_corr_pairs(thr)
        self._preview_auto_pick(thr)

    def _plan_for(self, threshold: float):
        """What auto-pick would do at this threshold."""
        c = self._corr
        if c is None:
            return auto_pick_plan([], [], spread={}, locked=set(), threshold=threshold)
        return auto_pick_plan(
            list(c.keys),
            c.matrix,
            spread={st.key: st.spread_score for st in self._stats},
            locked={f.key for f in FEATURES if f.locked},
            threshold=threshold,
        )

    def _preview_auto_pick(self, threshold: float) -> None:
        """Wash the rows auto-pick would drop, then let it fade.

        Nothing is applied — the button does that. The preview clears itself
        so a threshold left mid-drag does not look like a pending change.
        """
        plan = self._plan_for(threshold)
        self._rank.set_preview(set(plan.drop), set(plan.keep))
        self._auto_btn.setEnabled(not plan.is_empty)
        self._auto_btn.setToolTip(
            _AUTO_PICK_TIP
            if not plan.is_empty
            else f"Nothing reaches |r| ≥ {threshold:.2f} — every dimension already "
            "carries distinct signal. Lower the threshold to find redundancy."
        )
        self._preview_timer.start()

    def _clear_preview(self) -> None:
        self._rank.set_preview(set(), set())

    def _metric_enabled(self, key: str) -> bool:
        return self._settings.value(axis_enabled_key(key), _FEAT_DEFAULT.get(key, True), type=bool)

    def _sync_cards(self) -> None:
        """Bring the cards in line with the rank rows."""
        for key, card in getattr(self, "_cards", {}).items():
            on = self._metric_enabled(key)
            if card.enabled != on:
                card.enabled = on
                card.update()

    def _refresh_rank_enabled(self) -> None:
        """Re-read enabled flags from settings and repaint the bars list."""
        rank = getattr(self, "_rank", None)
        if rank is not None:
            rank.items = self._rank_items(self._stats)
            rank.update()
        self._sync_cards()

    def _auto_select(self) -> None:
        """Group features into clusters of mutually-correlated dimensions
        (transitively — A↔B↔C is one cluster), then in each cluster keep the
        single most discriminative (highest-spread) feature and drop the rest.
        Locked core dimensions are always kept; relational axes without a
        correlation row (style/similarity/key/genre) are untouched."""
        thr = self._corr_slider.value() / 100.0
        plan = self._plan_for(thr)
        enable, disable = plan.keep, plan.drop

        changed = False
        for k in enable:
            if not self._metric_enabled(k):
                self._settings.setValue(axis_enabled_key(k), True)
                self.metric_toggled.emit(k, True)
                changed = True
        for k in disable:
            if self._metric_enabled(k):
                self._settings.setValue(axis_enabled_key(k), False)
                self.metric_toggled.emit(k, False)
                changed = True

        if changed:
            self._refresh_rank_enabled()

    def _update_corr_pairs(self, thr: float) -> None:
        """Refresh the one-per-line list of pairs whose |r| ≥ threshold (both
        signs), sorted strongest first."""
        c = self._corr
        labels = c.labels if c is not None else []
        k = len(labels)
        pairs = []
        for i in range(k):
            for j in range(i + 1, k):
                r = c.matrix[i][j]
                if abs(r) >= thr:
                    pairs.append((abs(r), i, j, r))
        pairs.sort(reverse=True)

        if not pairs:
            self._corr_pairs_lbl.setStyleSheet(
                f"color: {_FAINT}; font-size: 13px; background:{_PANEL};"
                f"border:1px solid {_LINE}; border-radius:8px; padding:8px 10px;"
            )
            self._corr_pairs_lbl.setText(
                f"No feature pairs reach |r| ≥ {thr:.2f} — each carries distinct signal."
            )
            return

        lines = []
        for _a, i, j, r in pairs:
            col = "#ffd24a" if r >= 0 else "#6db8ff"
            kind = "" if r >= 0 else " (inverse)"
            lines.append(
                f'{labels[i]} ↔ {labels[j]} <span style="color:{col}">{r:+.2f}{kind}</span>'
            )
        header = (
            f"⚠  Highly correlated (|r| ≥ {thr:.2f}) — keeping both adds little; "
            "consider matching on just one:"
        )
        self._corr_pairs_lbl.setStyleSheet(
            f"color: {_DIM}; font-size: 13px; background:{_PANEL};"
            f"border:1px solid {_LINE}; border-left:3px solid {T.ORANGE};"
            "border-radius:8px; padding:8px 10px;"
        )
        self._corr_pairs_lbl.setText(header + "<br>" + "<br>".join(lines))

    def _show_scatter(self, i: int, j: int) -> None:
        if self._corr is None:
            return
        kx, ky = self._corr.keys[j], self._corr.keys[i]  # x = column, y = row
        ax, ay = self._values.get(kx), self._values.get(ky)
        if ax is None or ay is None:
            return
        mask = ~np.isnan(ax) & ~np.isnan(ay)
        xs, ys = ax[mask], ay[mask]
        from core.features import BY_KEY

        sx, sy = BY_KEY[kx], BY_KEY[ky]
        r = self._corr.matrix[i][j]
        w = _Scatter(
            xs, ys, sx.short_label, sy.short_label, sx.hue, sy.hue, r, keys=(sx.key, sy.key)
        )
        if self._scatter_dialog is None:
            self._scatter_dialog = QDialog(self)
            self._scatter_dialog.setStyleSheet(f"background:{'#0c0e13'};")
            QVBoxLayout(self._scatter_dialog).setContentsMargins(10, 10, 10, 10)
        dlg = self._scatter_dialog
        dlg.setWindowTitle(f"{sy.short_label} vs {sx.short_label}")
        layout = dlg.layout()
        while layout.count():
            old = layout.takeAt(0).widget()
            if old is not None:
                old.deleteLater()
        layout.addWidget(w)
        dlg.resize(480, 440)
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def _show_message(self, msg: str) -> None:
        w = QWidget()
        layout = QVBoxLayout(w)
        lbl = QLabel(msg)
        lbl.setStyleSheet(f"color: {_DIM}; font-size: 13px; padding: 30px;")
        lbl.setWordWrap(True)
        layout.addWidget(lbl)
        layout.addStretch()
        self._scroll.setWidget(w)
