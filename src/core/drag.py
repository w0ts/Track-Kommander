"""Programmatic drag-and-drop for loading tracks into Traktor decks.

This module is the cross-platform public API. The window-finding and
mouse-synthesis primitives differ per platform:

  * macOS  — Quartz ``CGEvent`` mouse simulation + ``CGWindowList`` (in this
    file). Our Qt window starts a real ``NSDraggingSession`` via the overridden
    ``startDrag`` in ``ResultsTable``; the Quartz events guide that session to
    Traktor's deck drop zone.
  * Windows — Win32 ``SendInput`` + ``EnumWindows`` (in ``core/drag_win.py``).
    Same idea: Qt's ``QDrag.exec`` enters the OLE ``DoDragDrop`` loop and the
    synthesized mouse drag carries it onto the deck.

The public functions (:func:`is_traktor_visible`, :func:`capture_traktor_window`,
:func:`snap_over_traktor_library`, :func:`drag_to_deck`) dispatch to the right
backend; everything else (calibration math, deck-letter detection) is platform
independent and shared.
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from core import platform_modules, traktor_process
from core.constants import APP_NAME

if TYPE_CHECKING:
    import numpy as np
    from PySide6.QtGui import QImage

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class DragOutcome:
    """Result of :func:`drag_to_deck`.

    Truthiness mirrors ``ok`` so existing ``if not drag_to_deck(...)`` call
    sites keep working; ``reason`` is a user-displayable explanation when the
    drag was refused (None on success).
    """

    ok: bool
    reason: str | None = None

    def __bool__(self) -> bool:
        return self.ok


_DRAG_OK = DragOutcome(True)

_IS_WIN = sys.platform == "win32"
_IS_MAC = sys.platform == "darwin"

#: Traktor's app / window-owner names. Defined, with the matching rule, in
#: :mod:`core.traktor_process`; kept here for the code that imports it.
TRAKTOR_OWNER_NAMES: tuple[str, ...] = traktor_process.APP_NAMES


# ── helpers ────────────────────────────────────────────────────────────────────


def row_screen_center(table, row: int) -> tuple[float, float] | None:
    """Return the global screen (x, y) centre of *row* in *table*, or None.

    On macOS the result is in Qt LOGICAL points (what Quartz wants). On Windows
    it is converted to PHYSICAL pixels (what ``SendInput`` wants) using the
    table window's own logical↔physical mapping — see :func:`_physical_point_win`.
    """
    idx = table.model().index(row, 0)
    if not idx.isValid():
        return None
    rect = table.visualRect(idx)
    if rect.isEmpty():
        return None
    pt = table.viewport().mapToGlobal(rect.center())
    x, y = float(pt.x()), float(pt.y())
    if _IS_WIN:
        return _physical_point_win(table, x, y)
    return x, y


def _physical_point_win(widget, x: float, y: float) -> tuple[float, float]:
    """Map a Qt-logical global point to PHYSICAL screen px via *widget*'s window.

    Qt's ``devicePixelRatio()`` is unreliable on Windows (it intermittently
    reports 1.0 even when coordinates are in logical pixels), so instead of
    scaling by the dpr we measure the window's actual logical↔physical ratio
    directly: Win32 ``GetWindowRect`` (always physical) vs Qt ``frameGeometry``
    (logical). This is correct whether Qt is scaling (dpr 2, ratio 2) or not
    (dpr 1, ratio 1), and the physical result matches the space ``SendInput``
    drives the cursor in. Falls back to the dpr scale if the mapping is missing.
    """
    from core import drag_win

    try:
        win = widget.window()
        r = drag_win.window_rect(int(win.winId()))
        g = win.frameGeometry()
        if r and g.width() > 0 and g.height() > 0:
            sx = r[2] / float(g.width())
            sy = r[3] / float(g.height())
            return (r[0] + (x - g.x()) * sx, r[1] + (y - g.y()) * sy)
    except Exception:
        _log.exception("_physical_point_win: window mapping failed; using dpr")
    dpr = drag_win.device_pixel_ratio() or 1.0
    return (x * dpr, y * dpr)


# ── finding Traktor (see core.traktor_process) ────────────────────────────────

#: The Quartz window list, and Traktor's main window / its id in it (macOS).
#: The names stay here because the drag code, and tests, look them up here.
_onscreen_windows = traktor_process.mac_windows
_traktor_bounds = traktor_process.mac_window_bounds
_traktor_window_id = traktor_process.mac_window_id


def is_traktor_visible() -> bool:
    """Return True if a Traktor window is currently on-screen (not minimised
    / hidden behind Spaces / on another display offscreen).

    Wraps the same window lookup ``drag_to_deck`` uses, so the two stay in sync.
    Returns True on any error so we don't block features on unrelated import
    or platform failures.
    """
    try:
        return traktor_process.window_bounds() is not None
    except Exception:
        return True


def front_app() -> str:
    """Which app is active: ``"traktor"``, ``"us"`` or ``"other"``.

    ``"other"`` also when the check fails. See
    :func:`core.traktor_process.front_app`.
    """
    return traktor_process.front_app()


def front_app_name() -> str:
    """The active app's name and bundle id, for the log."""
    return traktor_process.front_app_name()


def front_app_is_traktor_or_us() -> bool:
    """True when the active app is Traktor or this app.

    What decides whether Traktor can actually be seen: another app in front
    — an editor, a browser — may cover it completely while every one of its
    windows still counts as on screen.
    """
    return front_app() != "other"


def mouse_buttons_down() -> bool:
    """True while any mouse button is held, in any app."""
    try:
        if _IS_MAC:
            return platform_modules.appkit().NSEvent.pressedMouseButtons() != 0
        if _IS_WIN:
            state = platform_modules.win_ctypes().windll.user32.GetAsyncKeyState
            return any(state(vk) & 0x8000 for vk in (0x01, 0x02, 0x04))
    except Exception:
        _log.debug("mouse_buttons_down: check failed", exc_info=True)
        return True  # unsure: treat as held, so nothing switches mid-drag
    return False


def _app_owner_at_point(
    windows,
    x: float,
    y: float,
    skip_owners: tuple[str, ...] = (),
) -> str | None:
    """Return the owning app name of the topmost window at (x, y), or None.

    Windows are ordered front-to-back, so the first hit is the foremost.
    Owners listed in *skip_owners* (e.g. our own app) are ignored so that an
    overlapping Kommander window doesn't mask Traktor underneath.

    Only normal application windows (``kCGWindowLayer == 0``) count. macOS
    system overlays — Notification Centre, the menu bar, Dock and Control
    Centre — float at higher window layers and span the whole screen, so
    without this filter they'd always be reported as the topmost window and
    every drag would be wrongly rejected as "not Traktor".
    """
    for win in windows:
        if win.get("kCGWindowLayer", 0) != 0:
            continue
        owner = win.get("kCGWindowOwnerName")
        if owner in skip_owners:
            continue
        b = win.get("kCGWindowBounds", {})
        wx, wy = float(b.get("X", 0)), float(b.get("Y", 0))
        ww, wh = float(b.get("Width", 0)), float(b.get("Height", 0))
        if wx <= x < wx + ww and wy <= y < wy + wh:
            return owner
    return None


def _our_owner_name() -> str:
    """Best-effort name for *our* app as it appears in the CG window list."""
    try:
        app = platform_modules.appkit().NSRunningApplication.currentApplication()
        name = app.localizedName()
        if name:
            return str(name)
    except Exception:  # pragma: no cover — fallback below
        pass
    return APP_NAME


_BOUNDS_INSET = 8.0  # px from window edge — keep dst safely inside Traktor

# Auto-detection records each deck's LETTER glyph centre, but the glyph itself
# is a poor drop target. We drop a bit LEFT of it, at a platform-specific
# height:
#   * macOS   — the SAME height as the letter, onto the deck's track-info
#     header strip, which accepts the drop there.
#   * Windows — Traktor Pro 4's header strip REFUSES OLE drops: probing showed
#     every point at the letter's height returns DROPEFFECT_NONE (QDrag.exec →
#     IgnoreAction), while everything from ~35 px below the letter down through
#     the waveform accepts with Copy. So we aim BELOW the letter, into the top
#     of the deck body. Dropping below the letter is safe for C/D too:
#     ``snap_over_traktor_library`` pushes the overlay under the C/D deck row,
#     and ``point_is_traktor`` refuses the drag if anything covers the point.
#
# The offsets are ABSOLUTE (pixels), not a fraction of the window: the deck
# letter and the header strip are a fixed size in Traktor's UI regardless of
# window size, so a fixed pixel nudge is more stable than a percentage. They
# are baked into the stored ``drag_deck_<X>_rx/_ry`` at calibration time (in
# ``auto_calibrate_decks``), so the stored value is the final DROP position and
# ``_deck_drop_pos`` uses it directly. Startup auto-calibration re-bakes them,
# which also refreshes values stored before an offset change like this one.
_DROP_OFFSET_X_PX = 85.0  # drop this many px LEFT of the letter
_DROP_OFFSET_Y_PX = 60.0 if _IS_WIN else 0.0  # px BELOW the letter


def _deck_drop_pos(deck: str, bounds: tuple[float, float, float, float]) -> tuple[float, float]:
    """Compute the screen (x, y) drop position for *deck* given Traktor window *bounds*.

    Priority (highest first):
      1. Per-deck normalised offset (``drag_deck_<X>_rx`` / ``_ry``) saved by
         the screenshot calibration dialog — but only if both values are
         within [0, 1].
      2. Legacy hover-click row Y (``drag_deck_y_top`` / ``_y_bottom``) — but
         only if the saved Y actually falls inside the current Traktor bounds.
         (Stale absolute values from a previous monitor are silently dropped.)
      3. Hard-coded heuristic (25 % / 75 % X, 15 % / 65 % Y).

    The result is always clamped into ``bounds`` with a small inset so we can
    never aim at the menubar / a neighbouring app when calibration is wrong.
    """
    from PySide6.QtCore import QSettings

    wx, wy, ww, wh = bounds
    d = deck.upper()
    s = QSettings()

    px: float | None = None
    py: float | None = None

    rx_raw = s.value(f"drag_deck_{d}_rx")
    ry_raw = s.value(f"drag_deck_{d}_ry")
    if rx_raw is not None and ry_raw is not None:
        try:
            rx, ry = float(rx_raw), float(ry_raw)
        except (TypeError, ValueError):
            rx = ry = -1.0
        if 0.0 <= rx <= 1.0 and 0.0 <= ry <= 1.0:
            # Stored value is already the drop position (offset baked in at
            # calibration time, or a manual click on the exact drop zone).
            px = wx + ww * rx
            py = wy + wh * ry
        else:
            _log.warning(
                "drag_to_deck: discarding out-of-range per-deck calibration "
                "for deck %s (rx=%r, ry=%r) — falling back to defaults.",
                d,
                rx_raw,
                ry_raw,
            )

    if px is None or py is None:
        px = wx + ww * (0.25 if d in ("A", "C") else 0.75)
        row_key = "drag_deck_y_top" if d in ("A", "B") else "drag_deck_y_bottom"
        default_y = wy + wh * (0.15 if d in ("A", "B") else 0.65)
        legacy_raw = s.value(row_key)
        try:
            legacy_y = float(legacy_raw) if legacy_raw is not None else None
        except (TypeError, ValueError):
            legacy_y = None
        if legacy_y is not None and wy <= legacy_y <= wy + wh:
            py = legacy_y
        else:
            if legacy_y is not None:
                _log.warning(
                    "drag_to_deck: discarding stale legacy calibration "
                    "%s=%r (outside current Traktor bounds y=[%g..%g]).",
                    row_key,
                    legacy_raw,
                    wy,
                    wy + wh,
                )
            py = default_y

    px = min(max(px, wx + _BOUNDS_INSET), wx + ww - _BOUNDS_INSET)
    py = min(max(py, wy + _BOUNDS_INSET), wy + wh - _BOUNDS_INSET)
    return px, py


# ── Snap Kommander over Traktor's library ─────────────────────────────────────

# How many pixels of Traktor to leave UNCOVERED at the top — Kommander
# starts that far down from Traktor's top edge and extends to Traktor's
# bottom. Picked so the deck waveform/header stay visible above the
# overlay regardless of Traktor's overall window height. This is the
# baseline for a 2-deck layout; a detected 4-deck layout overrides it (see
# ``_deck_row_top_fraction``) so the lower C/D decks are not covered.
_LIB_OVERLAY_TOP_GAP_PX = 470

# Offset (fraction of window height) between a deck row's TOP and its letter
# glyph. The detected C/D bottom = one row-height below the C/D glyph, minus
# this — so the overlay lands at the library top, not below it. Empirically
# ~0.02 on a maximized window; the glyph sits just inside the panel's top edge.
_FOUR_DECK_HEADER_OFFSET = 0.02
# Never push the overlay top below this fraction — keeps a usable library strip
# even if the row-height estimate runs long.
_FOUR_DECK_MAX_TOP_FRAC = 0.85
# Fallback for the A/B header band when only C/D were detected (no upper
# reference). The glyph sits near the TOP of the A/B search region (~0.05–0.21).
_AB_HEADER_FALLBACK_FRAC = 0.07
# Used when Traktor reports a 4-deck layout but the screenshot detector can't
# pin the C/D glyphs (e.g. unusual window shape). Empirically the library sits
# at ~half the window height in 4-deck layouts.
_FOUR_DECK_DEFAULT_TOP_FRAC = 0.50


def _layout_is_four_deck(layout: object) -> bool | None:
    """Classify Traktor's ``gui_layout`` value: True=4-deck, False=2-deck, None=unknown.

    Matches loosely on the textual layout name (e.g. ``"fourDecks"``,
    ``"4 light"``) so it survives Traktor naming variations.
    """
    if layout in (None, "", "null", "undefined"):
        return None
    s = str(layout).lower()
    if "four" in s or "4" in s:
        return True
    if "two" in s or "2" in s or "single" in s or "1" in s:
        return False
    return None


def _deck_row_top_fraction(found: dict[str, tuple[float, float]], img_h: float) -> float | None:
    """Vertical fraction (0–1) just below Traktor's C/D deck row, or ``None``.

    Given detected deck-letter positions (``{deck: (x, y)}`` in capture-image
    pixels) and the capture height, returns where a library overlay should
    START so the lower (C/D) deck bodies stay uncovered.

    The A/B and C/D deck rows are equal height, so the C/D row bottom (≈ the
    library top) is one row-height below the C/D glyph:

        bottom = C/D_glyph + (C/D_glyph − A/B_glyph) − header_offset
               = 2·C/D_glyph − A/B_glyph − header_offset

    where ``header_offset`` is the small gap between a row's top and its glyph.
    Landing the overlay there covers the library fully without slicing into the
    deck bodies.

    Returns ``None`` for a 2-deck layout (no C/D detected) so the caller keeps
    its configured gap.
    """
    img_h = max(float(img_h), 1.0)
    lower = [y for d, (_x, y) in found.items() if d in ("C", "D")]
    if not lower:
        return None  # 2-deck layout — nothing to protect
    upper = [y for d, (_x, y) in found.items() if d in ("A", "B")]
    lower_frac = max(lower) / img_h
    upper_frac = (max(upper) / img_h) if upper else _AB_HEADER_FALLBACK_FRAC
    row = max(lower_frac - upper_frac, 0.0)
    top = lower_frac + row - _FOUR_DECK_HEADER_OFFSET
    return min(top, _FOUR_DECK_MAX_TOP_FRAC)


def _library_top_fraction_4deck(default: float | None = None) -> float | None:
    """Capture Traktor and compute the C/D-safe overlay-top fraction.

    Returns the fraction derived from the detected C/D glyphs, or ``default``
    when Traktor isn't capturable or the detector can't find C/D. Callers that
    already know it's a 4-deck layout (via the webhook) pass a sensible
    ``default`` so the overlay still clears C/D when detection misses.
    """
    captured = capture_traktor_window()
    if captured is None:
        return default
    image, _bounds = captured
    found = detect_deck_letters(image)
    frac = _deck_row_top_fraction(found, image.height())
    return frac if frac is not None else default


def _overlay_top_fraction(is_four: bool | None) -> float | None:
    """Decide the library-overlay top fraction from the authoritative layout.

    Only an authoritative 4-deck layout (``is_four is True``, from Traktor's
    ``gui_layout`` webhook) triggers the push-down below the C/D deck row. A
    2-deck layout (``False``) and an *unknown* layout (``None``) both keep the
    caller's configured gap (return ``None``).

    We deliberately do NOT fall back to screenshot detection when the layout is
    unknown: ``detect_deck_letters`` false-positives C/D on 2-deck layouts
    (tiny bright UI blobs pass the glyph size/aspect/fill filters), which would
    shove the overlay down onto a thin bottom strip — the exact bug this guards
    against. When we can't confirm 4-deck, we keep the safe 2-deck gap.
    """
    if is_four is True:
        return _library_top_fraction_4deck(default=_FOUR_DECK_DEFAULT_TOP_FRAC)
    return None


#: True while the browser line keeps not being found (or Traktor keeps not
#: being captured): the capture is saved and the reason logged once per such
#: episode, not on every LayoutWatch tick.
_browser_probe_missing = False


def browser_top_fraction() -> float | None:
    """Where Traktor's browser starts, as a fraction of its window height —
    from one capture — or None when Traktor cannot be captured or the
    browser is not visible.

    The capture leaves out the title bar (``IgnoreFraming``) while the
    window bounds include it, so the fraction is translated onto the full
    window height here, and callers apply it to the bounds directly.
    """
    global _browser_probe_missing
    captured = capture_traktor_window()
    if captured is None:
        if not _browser_probe_missing:
            _log.info("browser_top_fraction: Traktor could not be captured")
        _browser_probe_missing = True
        return None
    image, bounds = captured
    frac = detect_browser_top(image)
    if frac is None:
        # LayoutWatch probes every few seconds; the first capture of a miss
        # says why, the rest would only re-encode the same PNG each time.
        if not _browser_probe_missing:
            _dump_capture(image, "snap-capture.png")
        _browser_probe_missing = True
        return None
    _browser_probe_missing = False
    img_h = float(image.height())
    win_h = float(bounds[3]) if bounds and bounds[3] else img_h
    frame = max(0.0, win_h - img_h)
    return (frame + frac * img_h) / win_h


def _dump_capture(image, name: str) -> None:
    """Keep the capture a detection failed on, beside the log, for a look."""
    try:
        from core.runtime import user_log_dir

        path = user_log_dir() / name
        image.save(str(path))
        _log.info("browser line not found — capture saved to %s", path)
    except Exception:
        _log.debug("could not save the capture", exc_info=True)


def _traktor_logical_bounds() -> tuple[float, float, float, float] | None:
    """Traktor window ``(x, y, w, h)`` in Qt LOGICAL pixels, or None.

    macOS Quartz bounds are already logical points. Win32 ``GetWindowRect``
    bounds are PHYSICAL pixels, so they're divided by the screen's device
    pixel ratio to match what ``QWidget.setGeometry`` expects.
    """
    bounds = traktor_process.window_bounds()
    if _IS_WIN and bounds:
        from core import drag_win

        x, y, w, h = bounds
        dpr = drag_win.device_pixel_ratio() or 1.0
        return (x / dpr, y / dpr, w / dpr, h / dpr)
    return bounds


def snap_over_traktor_library(window) -> bool:
    """Resize/move *window* to overlay Traktor's library area, then raise it.

    The overlay rect is computed as:
        x      = Traktor.x        (full Traktor width, left-aligned)
        width  = Traktor.width
        top    = Traktor.top + ``gap``
        bottom = Traktor.bottom   (anchored to Traktor's bottom edge)

    ``gap`` is the configured ``lib_overlay_top_gap_px`` (default
    ``_LIB_OVERLAY_TOP_GAP_PX``), which assumes a 2-deck layout. When Traktor
    is showing decks C/D (4-deck layout), that gap would slice through the
    lower deck row, so we detect the live layout and push the overlay top
    below C/D instead — keeping them visible.

    The gap is clamped so we always leave at least 60 px visible at top
    and 60 px of Kommander below, even if the value runs bigger than
    Traktor's current height.

    Returns True if Traktor was found and the window was repositioned.
    """
    from PySide6.QtCore import QRect, QSettings

    bounds = _traktor_logical_bounds()
    if not bounds:
        _log.warning("snap_over_traktor_library: Traktor window not found.")
        return False
    # Degenerate bounds (minimized window's -32000 rect, a splash screen, a
    # collapsed window) would snap us off-screen with negative height.
    if bounds[2] < 400 or bounds[3] < 300 or bounds[0] < -20000 or bounds[1] < -20000:
        _log.warning(
            "snap_over_traktor_library: implausible Traktor bounds %s "
            "(minimized or still opening?) — not snapping.",
            bounds,
        )
        return False

    gap_raw = QSettings().value("lib_overlay_top_gap_px", _LIB_OVERLAY_TOP_GAP_PX)
    gap = _LIB_OVERLAY_TOP_GAP_PX
    if isinstance(gap_raw, (int, float, str)):
        try:
            gap = int(float(gap_raw))
        except ValueError:
            pass

    wx, wy, ww, wh = bounds

    # Where the browser starts, read off Traktor's own pixels: the capture is
    # of Traktor's window alone, so it sees through us. Works for every
    # layout and deck size, and replaces both the fixed gap and the C/D
    # glyph arithmetic whenever it finds the line.
    browser = browser_top_fraction()
    if browser is not None:
        gap = int(round(browser * wh))
        _log.info(
            "snap: browser starts %.3f down Traktor's window → overlay top at %d px", browser, gap
        )
    else:
        # Fallbacks: Traktor's reported gui_layout (never seen to arrive, but
        # authoritative if it does) pushes below the C/D row; otherwise the
        # configured gap. See ``_overlay_top_fraction`` for why letter
        # detection alone is not trusted here.
        layout = QSettings().value("traktor_gui_layout")
        top_frac = _overlay_top_fraction(_layout_is_four_deck(layout))
        if top_frac is not None:
            auto_gap = int(round(top_frac * wh))
            if auto_gap > gap:
                gap = auto_gap

    # Clamp: at least 60 px of Traktor visible above; at least 60 px of
    # Kommander height. So 60 ≤ gap ≤ wh - 60.
    gap = max(60, min(gap, int(wh) - 60))
    height = int(wh) - gap

    geom = QRect(
        int(round(wx)),
        int(round(wy + gap)),
        int(round(ww)),
        height,
    )
    _log.info(
        "snap: Traktor bounds=%s top_gap=%dpx -> geom=%s",
        bounds,
        gap,
        (geom.x(), geom.y(), geom.width(), geom.height()),
    )

    window.setGeometry(geom)
    window.show()
    window.raise_()
    window.activateWindow()
    return True


# ── Traktor window screenshot ──────────────────────────────────────────────────


def capture_traktor_window() -> tuple[QImage, tuple[float, float, float, float]] | None:
    """Capture the Traktor Pro 4 window as a QImage.

    Returns ``(QImage, bounds)`` where ``bounds`` is the screen-space
    ``(x, y, w, h)`` of the captured window, or ``None`` if Traktor is not
    running or the capture failed.
    """
    if _IS_WIN:
        from core import drag_win

        return drag_win.capture()

    Quartz = platform_modules.quartz()
    from PySide6.QtGui import QImage

    wins = _onscreen_windows()
    bounds = _traktor_bounds(wins)
    traktor_id = _traktor_window_id(wins)
    if not bounds or traktor_id is None:
        return None

    cg_image = Quartz.CGWindowListCreateImage(
        Quartz.CGRectNull,
        Quartz.kCGWindowListOptionIncludingWindow,
        traktor_id,
        Quartz.kCGWindowImageBoundsIgnoreFraming | Quartz.kCGWindowImageNominalResolution,
    )
    if cg_image is None:
        return None

    width = Quartz.CGImageGetWidth(cg_image)
    height = Quartz.CGImageGetHeight(cg_image)
    bytes_per_row = Quartz.CGImageGetBytesPerRow(cg_image)
    data_provider = Quartz.CGImageGetDataProvider(cg_image)
    raw = Quartz.CGDataProviderCopyData(data_provider)
    if raw is None:
        return None

    # CGImage on macOS is BGRA premultiplied; QImage.Format_ARGB32 expects the
    # same byte order on little-endian systems.
    img = QImage(
        bytes(raw),
        width,
        height,
        bytes_per_row,
        QImage.Format.Format_ARGB32,
    ).copy()
    return img, bounds


def _post_mouse(event_type: int, pos: tuple[float, float]) -> None:
    Quartz = platform_modules.quartz()
    ev = Quartz.CGEventCreateMouseEvent(
        None,
        event_type,
        Quartz.CGPoint(pos[0], pos[1]),
        Quartz.kCGMouseButtonLeft,
    )
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)


def _drag_mac(src: tuple[float, float], dst: tuple[float, float]) -> None:
    """Blocking drag worker — run in a daemon thread.

    A single teleport from src→dst races against Qt's startDrag / NSDraggingSession
    initialisation: the mouse-up can arrive before QDrag.exec has fully taken
    over, and the drop never registers. Interpolating ~16 steps with small
    sleeps gives Qt time to recognise the drag, lets Traktor's drop zone track
    the cursor, and ensures the mouse-up lands inside an established drag
    session.
    """
    Quartz = platform_modules.quartz()

    sx, sy = src
    dx, dy = dst
    display = Quartz.CGMainDisplayID()
    origin = Quartz.CGEventGetLocation(Quartz.CGEventCreate(None))

    # Teleport close to dst, then do a short interpolated approach for the
    # final stretch. The big jump is enough for Qt to fire startDrag (it just
    # needs to cross the drag-distance threshold once); the slow tail gives
    # NSDraggingSession time to take over and lets Traktor's drop zone latch.
    APPROACH_PX = 50.0
    import math

    vx, vy = dx - sx, dy - sy
    dist = math.hypot(vx, vy)
    if dist > APPROACH_PX:
        ux, uy = vx / dist, vy / dist
        ax, ay = dx - ux * APPROACH_PX, dy - uy * APPROACH_PX
    else:
        ax, ay = sx, sy  # already close — skip the teleport

    steps = 8
    step_sleep = 0.015  # ~120 ms over the final 50 px

    Quartz.CGDisplayHideCursor(display)
    try:
        _post_mouse(Quartz.kCGEventMouseMoved, (sx, sy))
        _post_mouse(Quartz.kCGEventLeftMouseDown, (sx, sy))
        # Let our app process the press and arm the drag-distance check
        time.sleep(0.15)

        # Single big jump to ~APPROACH_PX before dst — fires Qt's startDrag
        _post_mouse(Quartz.kCGEventLeftMouseDragged, (ax, ay))
        time.sleep(0.06)

        # Interpolated final approach — gives the NSDraggingSession time to
        # become active and lets Traktor track the cursor over its drop zone.
        for i in range(1, steps + 1):
            t = i / steps
            _post_mouse(
                Quartz.kCGEventLeftMouseDragged,
                (ax + (dx - ax) * t, ay + (dy - ay) * t),
            )
            time.sleep(step_sleep)

        # Hover before release so Traktor highlights/locks the deck
        time.sleep(0.12)
        _post_mouse(Quartz.kCGEventLeftMouseUp, (dx, dy))
        time.sleep(0.05)
        _post_mouse(Quartz.kCGEventMouseMoved, (origin.x, origin.y))
    finally:
        Quartz.CGDisplayShowCursor(display)


# ── Auto-detect deck letters in a Traktor screenshot ──────────────────────────

# The deck-letter glyph is a BRIGHT character whose hue depends on the deck's
# assigned colour and focus state, so we deliberately do NOT key on a specific
# hue. Observed in the wild:
#   * Saturated cyan-blue (≈ 67, 139, 247) — active deck on macOS.
#   * Saturated orange    (≈ 248, 113, 66) — active deck on Traktor Pro 4 / Win.
#   * Neutral grey        (≈ 156, 156, 156) — the inactive / secondary deck.
# A glyph pixel is therefore simply *bright* and either near-neutral (a grey/
# white letter) or strongly saturated (a coloured letter of any hue). Mid-chroma
# pixels — anti-aliased chrome and panel fills — are excluded. The actual letter
# is then isolated by the size / aspect / fill / region filters below, which do
# the real work of rejecting non-glyph UI (including the saturated waveform).
_GLYPH_MIN_BRIGHT = 150  # max(r,g,b) — dim background/chrome falls below
_GLYPH_MAX_NEUTRAL_CHROMA = 25  # max-min ≤ this → a grey / white letter
_GLYPH_MIN_SAT_CHROMA = 60  # max-min ≥ this → a saturated coloured letter


def _qimage_to_rgb(img) -> np.ndarray:
    """Return an HxWx3 uint8 RGB numpy array. Caller must own the array."""
    import numpy as np
    from PySide6.QtGui import QImage

    if img.format() != QImage.Format.Format_RGBA8888:
        img = img.convertToFormat(QImage.Format.Format_RGBA8888)
    ptr = img.constBits()
    # PyQt6 returns a sip pointer that needs .setsize(); PySide6 returns a
    # real memoryview that already knows its length. Bridge both shapes.
    if hasattr(ptr, "setsize"):
        ptr.setsize(img.sizeInBytes())
    stride = img.bytesPerLine() // 4
    arr = np.frombuffer(ptr, dtype=np.uint8).reshape(img.height(), stride, 4)
    return arr[:, : img.width(), :3].copy()


#: The separator above the browser is pure black; a row averaging under this
#: counts. Two of them in a row on a 1x capture.
_BROWSER_LINE_MAX = 4
#: The header band must begin within this fraction of the height after the
#: line and run at least this long (≈ 12 px and 4 px at 1000 px).
_BROWSER_BAND_WITHIN = 0.012
_BROWSER_BAND_MIN_ROWS = 0.004
#: A header row is flat (little spread) and lighter than the background row
#: just under the line by at least this much. Colour-managed screenshots and
#: live captures disagree on the absolute grays (33 vs 38), never on this.
_BROWSER_BAND_FLAT = 3.0
_BROWSER_BAND_LIFT = 8.0


def detect_browser_top(qimage) -> float | None:
    """Vertical fraction (0–1) of the line where Traktor's browser starts.

    Every layout — 2 decks, 4 decks, any deck size — draws the same thing
    there: a pure-black separator across the window, a row or two of
    background, then the browser's column-header band: flat, and lighter
    than that background. The separators between deck rows and under the
    toolbar are black too, but the background simply continues below them,
    so they do not match. Measured over the middle of the width, clear of
    the tree panel and the scrollbar.

    ``None`` when there is no such pair, e.g. the browser is hidden.
    """
    import numpy as np

    rgb = _qimage_to_rgb(qimage)
    h, w = rgb.shape[:2]
    if h < 50 or w < 50:
        return None
    x0, x1 = int(w * 0.30), int(w * 0.70)
    gray = rgb[:, x0:x1, :].mean(axis=2)
    row_mean = gray.mean(axis=1)
    row_p10 = np.percentile(gray, 10, axis=1)
    row_p90 = np.percentile(gray, 90, axis=1)
    within = max(3, int(h * _BROWSER_BAND_WITHIN))
    need = max(3, int(h * _BROWSER_BAND_MIN_ROWS))

    hit = None
    for y in np.flatnonzero(row_mean <= _BROWSER_LINE_MAX):
        y = int(y)
        if y + 2 + within + need >= h or row_mean[y + 1] <= _BROWSER_LINE_MAX:
            continue  # off the end, or not the line's last row
        background = row_p10[y + 1]
        # Header rows: flat background lifted above what is under the line.
        # Text on them raises the 90th percentile, so the 10th is the band.
        band = (row_p10[y + 1 : y + 1 + within + need] - background) >= _BROWSER_BAND_LIFT
        flat = (
            row_p90[y + 1 : y + 1 + within + need] - row_p10[y + 1 : y + 1 + within + need]
        ) <= _BROWSER_BAND_FLAT
        starts = np.flatnonzero(band[:within] & flat[:within])
        if not len(starts):
            continue
        s0 = int(starts[0])
        # The band's background stays lifted for `need` rows; text may make
        # some of them un-flat, so only the lift is required to hold.
        if band[s0 : s0 + need].all():
            hit = y  # keep the lowest such line
    return None if hit is None else hit / h


def _deck_search_regions(img_w: int, img_h: int) -> dict[str, tuple[int, int, int, int]]:
    """Return ``(x0, y0, x1, y1)`` in image-pixel coords for each deck letter.

    The letter sits at the top-right of each deck panel:
      * 2-deck layout — only A and B regions match; C/D return zero blobs.
      * 4-deck layout — all four match.

    The vertical extent is kept to the deck's HEADER strip — just the rows
    where the letter sits, ABOVE the waveform. The hue-agnostic glyph mask
    would otherwise latch onto bright/saturated waveform segments (which live
    a little lower in the panel) and report those instead of the letter.
    """

    def rect(rx0: float, rx1: float, ry0: float, ry1: float) -> tuple[int, int, int, int]:
        return (
            int(rx0 * img_w),
            int(ry0 * img_h),
            int(rx1 * img_w),
            int(ry1 * img_h),
        )

    # The C/D vertical band is intentionally wide: the lower deck row rides
    # high on a maximized 16:9 window (glyphs ~0.29) but low on a tall/narrow
    # one (~0.46). The size/aspect/fill filters reject the A/B waveform that
    # also falls in this x/y span, so the band can be generous without latching
    # onto chrome.
    return {
        "A": rect(0.30, 0.50, 0.05, 0.21),
        "B": rect(0.80, 1.00, 0.05, 0.21),
        "C": rect(0.30, 0.50, 0.23, 0.56),
        "D": rect(0.80, 1.00, 0.23, 0.56),
    }


def detect_deck_letters(qimage) -> dict[str, tuple[float, float]]:
    """Auto-locate deck letters in a Traktor screenshot.

    Returns ``{deck: (x_in_image, y_in_image)}`` for every letter found above
    the size/colour thresholds. Missing decks are simply absent from the dict
    (e.g. a 2-deck layout returns ``{"A": …, "B": …}``).

    Strategy:
      1. Threshold for Traktor blue (saturated cyan-blue, dark background).
      2. Label connected components.
      3. For each expected deck region, pick the largest component whose
         centroid falls inside that region and whose pixel area is plausible
         for a deck letter.
    """
    try:
        import numpy as np
        from scipy import ndimage
    except ImportError:
        _log.warning("detect_deck_letters: numpy/scipy unavailable — skipping.")
        return {}

    rgb = _qimage_to_rgb(qimage)
    h, w = rgb.shape[:2]
    r = rgb[..., 0].astype(np.int16)
    g = rgb[..., 1].astype(np.int16)
    b = rgb[..., 2].astype(np.int16)

    # Hue-agnostic glyph mask: bright AND (near-neutral OR strongly saturated).
    mx = np.maximum.reduce([r, g, b])
    mn = np.minimum.reduce([r, g, b])
    chroma = mx - mn
    is_bright = mx >= _GLYPH_MIN_BRIGHT
    is_neutral = chroma <= _GLYPH_MAX_NEUTRAL_CHROMA  # grey / white letter
    is_saturated = chroma >= _GLYPH_MIN_SAT_CHROMA  # coloured letter, any hue
    mask = is_bright & (is_neutral | is_saturated)

    # Written into a caller-supplied array: label() then hands back only the
    # count, and the labels are consecutive, so the largest one is that count.
    labeled = np.zeros(mask.shape, dtype=np.int32)
    ndimage.label(mask, output=labeled)
    n_components = int(labeled.max())
    if n_components == 0:
        _log.debug("detect_deck_letters: no glyph blobs found.")
        return {}

    sizes = ndimage.sum(mask, labeled, range(1, n_components + 1))
    centroids = ndimage.center_of_mass(mask, labeled, range(1, n_components + 1))
    bboxes = ndimage.find_objects(labeled)

    # Filters tuned to catch the BIG deck letters (A/B/C/D) and reject other
    # bright UI. Area thresholds are scaled by total image area so the same
    # rules work on the native Quartz capture and downscaled PNG copies.
    #
    # Measured on a 1728×1084 capture: the real deck letters are ~24×24 px
    # square glyphs (area ≈ 180–260, ≈ 0.0001 of the image) with a HOLLOW
    # outline fill (the "A" is only ~0.33 filled, "B" ~0.50). They are NOT tiny
    # — an earlier upper bound of ~0.00012 wrongly rejected the "B" (251 px) and
    # left only small junk (BPM-clock digits, knob LEDs) in each region.
    #
    #   * area   — generous band so big letters in roomy layouts (e.g. Traktor's
    #              "4 light") still pass. The bound rejects large UI fills.
    #   * aspect — letters are roughly SQUARE (h ≈ 0.6–1.6 × w). This is the key
    #              discriminator: wide buttons / BPM digits (aspect ≪ 1) and
    #              tall strokes are rejected; the square letter survives.
    #   * fill   — between 18 % (hollow "A" outline) and 85 %. The UPPER bound
    #              rejects the SOLID square UI tiles (e.g. the blue ♪ "load"
    #              boxes, ≈ 0.95 filled) that share the deck panel.
    total_px = w * h
    min_area = max(60, int(total_px * 0.00004))
    max_area = int(total_px * 0.0008)
    min_aspect, max_aspect = 0.6, 1.6
    min_fill, max_fill = 0.18, 0.85

    # (size, x, y, aspect) — aspect kept so the per-region pick can favour the
    # squarest (most letter-like) blob.
    blobs: list[tuple[float, float, float, float]] = []
    rejected = 0
    for i, sz in enumerate(sizes):
        if sz < min_area or sz > max_area:
            rejected += 1
            continue
        sl_y, sl_x = bboxes[i]
        bb_h = max(sl_y.stop - sl_y.start, 1)
        bb_w = max(sl_x.stop - sl_x.start, 1)
        aspect = bb_h / bb_w
        fill = sz / (bb_h * bb_w)
        if not (min_aspect <= aspect <= max_aspect) or not (min_fill <= fill <= max_fill):
            rejected += 1
            continue
        cy, cx = centroids[i]
        blobs.append((float(sz), float(cx), float(cy), float(aspect)))

    if not blobs:
        _log.debug(
            "detect_deck_letters: %d glyph components, %d rejected by "
            "size/aspect/fill filters — no deck-letter candidates.",
            n_components,
            rejected,
        )
        return {}

    # Diagnostic: dump every surviving candidate as window-fractions so a
    # missed deck can be traced to "no blob there" vs "blob rejected by a
    # filter". Cheap (a handful of blobs) and only at DEBUG.
    if _log.isEnabledFor(logging.DEBUG):
        _log.debug(
            "detect_deck_letters: %d candidate blob(s): %s",
            len(blobs),
            ", ".join(
                f"(xf={x / w:.3f}, yf={y / h:.3f}, area={int(sz)}, asp={asp:.2f})"
                for sz, x, y, asp in sorted(blobs, key=lambda c: (c[2], c[1]))
            ),
        )

    regions = _deck_search_regions(w, h)
    out: dict[str, tuple[float, float]] = {}
    missing: list[str] = []
    for deck, (x0, y0, x1, y1) in regions.items():
        in_region = [(sz, x, y, asp) for sz, x, y, asp in blobs if x0 <= x <= x1 and y0 <= y <= y1]
        if not in_region:
            missing.append(deck)
            continue
        # Pick the most LETTER-LIKE blob: square (aspect near 1.0) and large.
        # Scoring this way — rather than "nearest the top-right corner" — stops
        # off-letter junk that happens to sit nearer the corner (the BPM clock,
        # a knob LED) from outvoting the real deck letter.
        _, x, y, _ = max(
            in_region,
            key=lambda c: c[0] / (1.0 + abs(c[3] - 1.0) * 3.0),
        )
        out[deck] = (x, y)

    # Enforce Traktor's deck-GRID structure to reject spurious matches. Decks
    # only ever appear as A+B (2-deck) or A+B+C+D (4-deck) — never a lone bottom
    # deck. So the bottom row (C, D) is kept only if it forms a complete pair
    # AND each bottom letter sits BELOW and column-aligned with its top partner
    # (C under A, D under B). This drops the lone false "D" that bright UI in the
    # lower-right of a 2-deck layout can otherwise produce.
    _MIN_ROW_GAP = 0.05 * h  # C/D must be at least this far below A/B
    _MAX_COL_SKEW = 0.08 * w  # …and within this horizontal skew of the column

    def _below_and_aligned(bottom: str, top: str) -> bool:
        if bottom not in out or top not in out:
            return False
        bx, by = out[bottom]
        tx, ty = out[top]
        return by > ty + _MIN_ROW_GAP and abs(bx - tx) <= _MAX_COL_SKEW

    if not (_below_and_aligned("C", "A") and _below_and_aligned("D", "B")):
        dropped = [d for d in ("C", "D") if d in out]
        if dropped:
            _log.debug(
                "detect_deck_letters: dropping bottom-row %s — not a complete "
                "pair aligned below A/B (likely a 2-deck layout with spurious "
                "matches).",
                ", ".join(dropped),
            )
        out.pop("C", None)
        out.pop("D", None)

    if missing:
        _log.debug(
            "detect_deck_letters: no candidate in region(s) %s — "
            "expected glyph x/y-fraction ranges %s.",
            ", ".join(missing),
            {
                d: (round(x0 / w, 2), round(y0 / h, 2), round(x1 / w, 2), round(y1 / h, 2))
                for d, (x0, y0, x1, y1) in regions.items()
                if d in missing
            },
        )
    _log.debug(
        "detect_deck_letters: %d/%d found (%s) from %d candidate blobs.",
        len(out),
        len(regions),
        ", ".join(sorted(out)) or "none",
        len(blobs),
    )
    return out


# ── Headless auto-calibration ─────────────────────────────────────────────────


def auto_calibrate_decks() -> dict[str, tuple[float, float]]:
    """Capture Traktor and store per-deck calibration based on auto-detection.

    Returns the dict of normalised offsets that were saved (one entry per
    detected deck), or ``{}`` if Traktor isn't running / detection failed.
    Safe to call from a timer or background thread (uses ``QSettings``).
    """
    captured = capture_traktor_window()
    if captured is None:
        _log.debug("auto_calibrate_decks: Traktor not capturable — skipping.")
        return {}
    image, _bounds = captured

    found = detect_deck_letters(image)
    if not found:
        _log.debug("auto_calibrate_decks: detection found no deck letters.")
        return {}

    from PySide6.QtCore import QSettings

    s = QSettings()
    img_w = max(image.width(), 1)
    img_h = max(image.height(), 1)

    # Turn each detected letter position into the final *drop* position by
    # baking in the drop offset (so the stored value is used directly by
    # ``_deck_drop_pos``). The offset is a fixed pixel nudge LEFT of the letter
    # at the same height, onto the deck's track-info header — identical on macOS
    # and Windows (Traktor's UI layout is the same). The offset is applied in
    # capture-image pixels, then normalised by the image size for storage so the
    # stored value tracks the window across moves/resizes.
    saved: dict[str, tuple[float, float]] = {}
    for deck, (x, y) in found.items():
        rx = min(max((float(x) - _DROP_OFFSET_X_PX) / img_w, 0.0), 1.0)
        ry = min(max((float(y) + _DROP_OFFSET_Y_PX) / img_h, 0.0), 1.0)
        s.setValue(f"drag_deck_{deck}_rx", rx)
        s.setValue(f"drag_deck_{deck}_ry", ry)
        saved[deck] = (rx, ry)
    # Stale absolute Y values from a previous monitor break things if the
    # window has since moved. Drop them once per-deck offsets exist.
    for legacy in ("drag_deck_y_top", "drag_deck_y_bottom"):
        s.remove(legacy)

    _log.debug(
        "auto_calibrate_decks: saved %d deck(s): %s",
        len(saved),
        ", ".join(f"{d}=({rx:.3f},{ry:.3f})" for d, (rx, ry) in sorted(saved.items())),
    )
    return saved


# ── deck-detection guard ───────────────────────────────────────────────────────


def deck_calibrated(deck: str) -> bool:
    """Return True if a usable per-deck drop calibration exists for *deck*.

    "Calibrated" means :func:`auto_calibrate_decks` (or the screenshot dialog)
    has located this deck's letter and stored a normalised drop offset that is
    in range. This is what we mean by a deck having been *detected*.
    """
    from PySide6.QtCore import QSettings

    s = QSettings()
    d = deck.upper()
    rx_raw, ry_raw = s.value(f"drag_deck_{d}_rx"), s.value(f"drag_deck_{d}_ry")
    if rx_raw is None or ry_raw is None:
        return False
    try:
        rx, ry = float(rx_raw), float(ry_raw)
    except (TypeError, ValueError):
        return False
    return 0.0 <= rx <= 1.0 and 0.0 <= ry <= 1.0


def ensure_deck_detected(deck: str) -> bool:
    """Make sure *deck* has been detected, detecting it now if it hasn't.

    Returns True if the deck is calibrated (either already, or after a fresh
    detection pass). Returns False if detection still can't locate the deck —
    e.g. Traktor isn't visible, or that deck isn't shown in the current layout.
    The caller is expected to surface visual feedback on False.
    """
    if deck_calibrated(deck):
        return True
    _log.debug(
        "ensure_deck_detected: deck %s not calibrated — running detection.",
        deck,
    )
    auto_calibrate_decks()
    ok = deck_calibrated(deck)
    if not ok:
        _log.warning(
            "ensure_deck_detected: deck %s still not detected after a "
            "detection pass — is Traktor visible and showing that deck?",
            deck,
        )
    return ok


# ── public API ─────────────────────────────────────────────────────────────────


def _deck_not_detected_reason(deck: str) -> str:
    return (
        f"Deck {deck} could not be located in Traktor's window — it may not "
        "be shown in the current layout, or Traktor isn't fully visible."
    )


def _drag_to_deck_win(src: tuple[float, float], deck: str) -> DragOutcome:
    """Windows backend for :func:`drag_to_deck`. Coords *src* are Qt LOGICAL px.

    Before touching the mouse we require the deck to have been detected; if it
    hasn't, we attempt a detection pass and bail out (returning a falsy
    outcome) if that fails, so the caller can show the user that the load
    didn't work rather than firing a blind drag at a guessed position.
    """
    from core import drag_win

    if not ensure_deck_detected(deck):
        _log.warning(
            "drag_to_deck: deck %s could not be detected — drag aborted.",
            deck,
        )
        return DragOutcome(False, _deck_not_detected_reason(deck))
    try:
        found = drag_win.traktor_window()
        if not found:
            _log.warning("drag_to_deck: no Traktor window found.")
            return DragOutcome(False, "Traktor's window was not found.")
        _hwnd, bounds = found  # PHYSICAL px
        dst = _deck_drop_pos(deck, bounds)  # PHYSICAL px (calibration is normalised)
        if not drag_win.point_is_traktor(*dst):
            _log.warning(
                "drag_to_deck: drop point %s is not over Traktor — rejecting. "
                "Move/resize Traktor so the deck drop zone is visible, or "
                "recalibrate.",
                dst,
            )
            return DragOutcome(
                False,
                (
                    f"Deck {deck}'s drop zone is covered by another window — "
                    "move/resize Traktor so the deck is visible."
                ),
            )
        # row_screen_center already returns PHYSICAL px on Windows (see
        # _physical_point_win), matching the space SendInput drives in.
        src_phys = (float(src[0]), float(src[1]))
    except Exception:
        _log.exception("drag_to_deck: exception while preparing Windows drag")
        return DragOutcome(False, "Internal error preparing the drag — see log.")

    _log.debug(
        "drag_to_deck[win]: deck=%s src=%s dst=%s bounds=%s",
        deck,
        src_phys,
        dst,
        bounds,
    )
    # Mark the upcoming startDrag as synthetic BEFORE the worker can press —
    # only then may the input-blocking hook be installed (see drag_win).
    drag_win.begin_synthetic_drag()
    threading.Thread(
        target=drag_win.do_drag,
        args=(src_phys, dst),
        daemon=True,
    ).start()
    return _DRAG_OK


def drag_to_deck(src: tuple[float, float], deck: str) -> DragOutcome:
    """Initiate a programmatic drag from screen position *src* to a Traktor deck.

    Returns a truthy :class:`DragOutcome` if the drag was launched, or a falsy
    one (with a user-displayable ``reason``) if Traktor's window cannot be
    found, the deck couldn't be detected, or (macOS) Accessibility permission
    is missing. Runs asynchronously (daemon thread) so the caller is not
    blocked.
    """
    if _IS_WIN:
        return _drag_to_deck_win(src, deck)

    from core.permissions import accessibility_granted

    # Synthesized Quartz mouse events (CGEventPost) silently no-op without
    # Accessibility permission — the drag would appear to do nothing. Refuse
    # up front with a clear log instead of a mysterious dead drag.
    if not accessibility_granted():
        _log.warning(
            "drag_to_deck: Accessibility permission not granted — synthesized "
            "mouse drags cannot be posted. Grant it in System Settings → "
            "Privacy & Security → Accessibility (Settings tab → Permissions).",
        )
        return DragOutcome(
            False,
            (
                "Accessibility permission is missing — grant it in System "
                "Settings → Privacy & Security → Accessibility."
            ),
        )

    # Require the deck to have been DETECTED before aiming a drag at it. Without
    # this, an undetected deck (e.g. C/D in a 2-deck layout) would fall through
    # to ``_deck_drop_pos``'s hard-coded heuristic and drop a track at a guessed
    # spot — which lands on the WRONG deck. Abort instead so the caller can flash
    # a failure rather than mis-loading.
    if not ensure_deck_detected(deck):
        _log.warning(
            "drag_to_deck: deck %s is not detected (not shown in the current "
            "Traktor layout, or detection failed) — drag aborted.",
            deck,
        )
        return DragOutcome(False, _deck_not_detected_reason(deck))
    try:
        wins = _onscreen_windows()
        bounds = _traktor_bounds(wins)
        if not bounds:
            seen = sorted({w.get("kCGWindowOwnerName", "?") for w in wins})[:25]
            _log.warning(
                "drag_to_deck: no Traktor window found; visible owners=%s",
                seen,
            )
            return DragOutcome(False, "Traktor's window was not found on screen.")
        dst = _deck_drop_pos(deck, bounds)
        ours = _our_owner_name()
        owner = _app_owner_at_point(wins, *dst, skip_owners=(ours,))
        _log.debug(
            "drag_to_deck: deck=%s src=%s dst=%s bounds=%s owner_at_dst=%r ours=%r",
            deck,
            src,
            dst,
            bounds,
            owner,
            ours,
        )
        if not traktor_process.is_traktor_app_name(owner):
            _log.warning(
                "drag_to_deck: rejecting drag — topmost window at %s "
                "is %r, not Traktor. Move/resize Traktor so the deck "
                "drop zone is visible, or recalibrate.",
                dst,
                owner,
            )
            return DragOutcome(
                False,
                (
                    f"Deck {deck}'s drop zone is covered by "
                    f"{owner or 'another window'} — move/resize Traktor so the "
                    "deck is visible."
                ),
            )
    except Exception:
        _log.exception("drag_to_deck: exception while preparing drag")
        return DragOutcome(False, "Internal error preparing the drag — see log.")
    threading.Thread(target=_drag_mac, args=(src, dst), daemon=True).start()
    return _DRAG_OK
