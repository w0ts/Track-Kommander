"""What the bridge posts, read and turned into signals.

Everything here has crossed from QML over HTTP, after the 200 has already
gone back: a value can be missing, the wrong type or NaN, and an exception
is seen by nobody — it just drops every signal the post would have carried.
So each field is read forgivingly, and what names a deck or ends up in the
settings is checked first (:mod:`workers.webhook_guard`).
"""

from __future__ import annotations

import json
import logging
import re
import urllib.parse

from PySide6.QtCore import QSettings

from core.features import BY_KEY
from core.runtime import in_traktor
from ui.band_extensions import SLOTS
from ui.band_pages import BAND_PAGES
from workers.webhook_guard import clean_for_log, is_deck, is_layout_name

log = logging.getLogger("webhook")
request_log = logging.getLogger("webhook.requests")
probe_log = logging.getLogger("probe")

# The buttons libtk's native band inside Traktor can report (POST /uiAction).
#: A click on the native page libtk draws (tk_nlist.mm): a tree item or a row with its mouse
#: button, or one of its two buttons.
_NL_ACTION = re.compile(r"NL:(?:TREE|ROW):\d{1,6}:[12]|NL:(?:LINK|NEW)")

UI_ACTIONS = frozenset(
    {
        "A",
        "B",
        "C",
        "D",
        "SETTINGS",
        "LIB",
        "AUTO",
        "KOMMANDER",
        "MATCHING",
        "BAND",
        "STATS",
        "LIST",
        *BAND_PAGES,
        *SLOTS,  # libtk's spare buttons, an add-on's (ui.band_extensions)
    }
)


def _fmt(v) -> str:
    try:
        return f"{float(v):+.4f}"
    except (TypeError, ValueError):
        return clean_for_log(v)


def _text(value) -> str:
    """A text field read off the bridge: stripped, and empty when absent.

    Whatever arrived is made text. A number where a title should be used to
    raise, and the whole post went with it.
    """
    return "" if value is None else str(value).strip()


def _rows(value) -> list[dict]:
    """The objects in a list read off the bridge; nothing, if it is not a
    list, and skipping anything in it that is not an object."""
    if not isinstance(value, list):
        return []
    return [row for row in value if isinstance(row, dict)]


def _num(value, default: float | None) -> float | None:
    """A number read off the bridge, or *default* when it isn't one.

    Same forgiving reading as :func:`semitones_from_adjust`: the value crossed
    a network from QML and may be missing, a string, or NaN. Posts are
    handled after the 200 has gone back, so raising here would silently drop
    every other signal the post carries.
    """
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number != number:  # NaN
        return default
    return number


_CHANNEL_MAP = {"1": "A", "2": "B", "3": "C", "4": "D"}


def _channel_to_deck(raw: str) -> str:
    """Map a channel identifier to a deck letter.
    Accepts either a number ('1'–'4') or a letter ('A'–'D').
    """
    return _CHANNEL_MAP.get(raw, raw.upper())


#: What one semitone is worth to ``app.traktor.decks.N.track.key.adjust``.
#: The property is normalised, not a semitone count: 1.0 is the full twelve
#: semitones, so one is a twelfth. Measured on a real deck — sending 1.0
#: moved it an octave, and a sixth moved it two semitones.
SEMITONE = 1.0 / 12.0


def key_adjust_value(semitones: int) -> float:
    """A semitone shift as Traktor's Key knob wants it."""
    return 0.0 if not semitones else semitones * SEMITONE


def semitones_from_adjust(value) -> int:
    """How far a deck's Key knob is turned, read back off the bridge.

    The inverse of :func:`key_adjust_value`, and deliberately forgiving: the
    reading crosses a network from QML, so it can be missing, a string, or a
    NaN, and none of those mean "transposed".
    """
    try:
        raw = float(value)
    except (TypeError, ValueError):
        return 0
    if raw != raw:  # NaN: not a knob position
        return 0
    return max(-12, min(12, round(raw / SEMITONE)))


# The bridge's deck-state posts (not its /command poll, not libtk's own posts).
D2_STATE_ROUTES = (
    "/deckLoaded/",
    "/updateDeck/",
    "/updateChannel/",
    "/updateMasterClock",
    "/keepAlive",
)


def handle_post(watcher, path: str, body: str) -> None:
    """Act on one post: emit *watcher*'s signals for what it carries."""
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        try:
            data = dict(urllib.parse.parse_qsl(body))
        except Exception:
            data = {}
    if not isinstance(data, dict):
        # Valid JSON that is not an object — a list, a number. Every
        # route below reads fields by name.
        request_log.debug("POST %s: body is not an object", path)
        return

    # ── /show ─────────────────────────────────────────────────────────────
    # Not from Traktor's QML bridge: the native injector (libtk, DYLD-loaded
    # into Traktor) posts this when the user clicks the "Track Kommander"
    # entry in Traktor's own browser tree. Raise our window. It is NOT proof
    # the D2 bridge is alive, so it returns before the traktor_ping below —
    # a click must not flip the health banner to "connected".
    if path == "/show":
        log.info("show requested (native opener)")
        watcher.show_requested.emit()
        return

    # ── /nativeTrack ──────────────────────────────────────────────────────
    # Track metadata libtk read straight from Traktor's C++ (MediaItem getters),
    # not via the QML bridge. `source` says where it came from ("deckLoad" when a
    # track was loaded to a deck, "browse" when a row rendered). Like /show it is
    # not a bridge ping, so it returns before traktor_ping.
    if path == "/nativeTrack":
        info = {
            "title": _text(data.get("title")),
            "artist": _text(data.get("artist")),
            "bpm": _num(data.get("bpm"), 0.0),
            "deck": _text(data.get("deck")).upper(),  # "A".."D" when a deck loaded, else ""
            "source": _text(data.get("source")),
        }
        log.info(
            "nativeTrack  [%s]  deck=%s  bpm=%.2f  %s – %s",
            clean_for_log(info["source"]),
            clean_for_log(info["deck"]) or "-",
            info["bpm"] or 0.0,
            clean_for_log(info["artist"]),
            clean_for_log(info["title"]),
        )
        watcher.native_track.emit(info)
        return

    # ── /uiAction ─────────────────────────────────────────────────────────
    # A click on one of the buttons libtk draws in its band inside Traktor's
    # browser (A–D pick the source deck; SETTINGS, LIB, AUTO mirror the side
    # buttons). Only the known names go on: the value drives the UI. Not a
    # bridge ping either.
    if path == "/uiAction":
        action = _text(data.get("action")).upper()
        if action.startswith("CARD:"):
            # A matching card in the band: the axis key must be one TK knows.
            key = action[5:].lower()
            if key not in BY_KEY:
                request_log.debug("POST /uiAction: unknown card %s", clean_for_log(key))
                return
            action = f"CARD:{key}"
        elif action.startswith("NL:"):
            # The native page drawn by libtk: a tree item, a row (button 1/2), or its buttons.
            if not _NL_ACTION.fullmatch(action):
                request_log.debug("POST /uiAction: bad native action %s", clean_for_log(action))
                return
        elif action not in UI_ACTIONS:
            request_log.debug("POST /uiAction: unknown action %s", clean_for_log(action))
            return
        log.info("uiAction  %s", action)
        watcher.ui_action.emit(action)
        return

    # ── /uiCard ───────────────────────────────────────────────────────────
    # A matching card's slider was set or dragged inside Traktor's band:
    # {"key": axis, "field": "value" | "strength", "value": 0–100}.
    if path == "/uiCard":
        key = _text(data.get("key")).lower()
        field = _text(data.get("field")).lower()
        number = _num(data.get("value"), None)
        if key not in BY_KEY or field not in ("value", "strength") or number is None:
            request_log.debug("POST /uiCard: rejected %s", clean_for_log(str(data)[:80]))
            return
        watcher.ui_card.emit(key, field, int(max(0, min(100, number))))
        return

    # Inside Traktor the D2 bridge is not used at all (User, 2026-11-24): deck
    # state comes from Traktor's own callbacks (workers.native_deck_state), and
    # libtk's own posts are the four above. A D2 mapping that is loaded as well
    # would report every change a second time.
    if in_traktor():
        request_log.debug("POST %s: ignored inside Traktor (no D2 bridge)", clean_for_log(path))
        return
    if getattr(watcher, "native_state", False) and path.startswith(D2_STATE_ROUTES):
        request_log.debug("POST %s: ignored, deck state is native", clean_for_log(path))
        return

    # A deck named in the path is one of Traktor's four, or the post is
    # not one of ours: the name goes on to settings keys and commands.
    if path.startswith(("/deckLoaded/", "/updateDeck/", "/updateChannel/")):
        named = _channel_to_deck(path.split("/")[-1])
        if not is_deck(named):
            request_log.debug("POST %s: no such deck", clean_for_log(path))
            return

    # ── /deckLoaded/<deck> ────────────────────────────────────────────────
    if path.startswith("/deckLoaded/"):
        deck = path.split("/")[-1].upper()
        # Accept both camelCase (lord-carlos QML) and snake_case (our QML)
        info = {
            "title": _text(data.get("title")),
            "artist": _text(data.get("artist")),
            "album": _text(data.get("album")),
            "genre": _text(data.get("genre")),
            "bpm": _num(data.get("bpm") or 0.0, 0.0),
            "key": _text(data.get("key")),
            "file_path": _text(data.get("filePath") or data.get("file_path")),
            "track_length": _num(data.get("trackLength") or data.get("track_length") or 0.0, 0.0),
        }
        log.info(
            "deckLoaded  deck=%-2s  bpm=%-7.2f  key=%-4s  %s – %s",
            deck,
            info["bpm"],
            clean_for_log(info["key"]),
            clean_for_log(info["artist"]),
            clean_for_log(info["title"]),
        )
        log.debug("            path=%s", clean_for_log(info["file_path"]))
        watcher.deck_loaded.emit(deck, info)

    # ── /updateDeck/<deck> ────────────────────────────────────────────────
    elif path.startswith("/updateDeck/"):
        deck = path.split("/")[-1].upper()
        # Accept both "play" (our QML) and "isPlaying" (lord-carlos QML)
        playing = bool(data.get("play") or data.get("isPlaying"))
        request_log.debug("updateDeck  deck=%-2s  play=%s", deck, playing)
        watcher.deck_state.emit(deck, playing)

    # ── /updateChannel/<channel> ──────────────────────────────────────────
    # Channel can be a deck letter (A-D, our QML) or a number (1-4, lord-carlos QML)
    elif path.startswith("/updateChannel/"):
        raw = path.split("/")[-1]
        deck = _channel_to_deck(raw)
        volume_raw = (
            data.get("volume") if data.get("volume") is not None else data.get("onAirLevel")
        )
        if volume_raw is not None:
            # Unreadable reads as closed: better a missed on-air than a
            # dropped post.
            volume = _num(volume_raw, 0.0)
        else:
            # isOnAir bool fallback: treat True as fully open fader
            volume = 1.0 if bool(data.get("isOnAir") or data.get("on_air")) else 0.0
        request_log.debug("updateChannel  %s→deck=%-2s  vol=%.3f", raw, deck, volume)
        watcher.deck_on_air.emit(deck, volume)

    # ── /updateMasterClock ────────────────────────────────────────────────
    elif path == "/updateMasterClock":
        bpm = _num(data.get("bpm", 0) or 0, 0.0)
        if bpm > 0:
            request_log.debug("masterClock  bpm=%.2f", bpm)
            watcher.master_tempo.emit(bpm)

    # ── /keepAlive ────────────────────────────────────────────────────────
    elif path == "/keepAlive":
        version = str(data.get("version") or "")
        if version:
            watcher.d2_version.emit(version)
        # Local to this request: the IPv4 and IPv6 servers each handle
        # posts on their own threads, and a shared dict let one request
        # log or clear another's rows.
        phase_rows: dict[str, tuple] = {}
        decks = _rows(data.get("decks"))
        for deck_info in decks:
            deck = str(deck_info.get("deck") or "").upper()
            if not is_deck(deck):
                continue
            watcher.deck_state.emit(deck, bool(deck_info.get("play")))
            if "position" in deck_info:
                position = _num(deck_info["position"], None)
                if position is not None:
                    watcher.deck_position.emit(deck, position)
            phase = deck_info.get("phase")
            if isinstance(phase, (int, float)):
                watcher.deck_phase.emit(deck, float(phase))
            # Absent, not zero, when the bridge is too old to report it:
            # emitting a zero would wipe a shift we set a moment ago.
            if deck_info.get("keyAdjust") is not None:
                watcher.deck_key_adjust.emit(deck, semitones_from_adjust(deck_info["keyAdjust"]))
            tempo = deck_info.get("tempo")
            # NaN fails the > 0 test; 0 is an empty deck, not a tempo.
            if isinstance(tempo, (int, float)) and tempo > 0:
                watcher.deck_tempo.emit(deck, float(tempo))
            phase_rows[deck] = (
                deck_info.get("phase"),
                deck_info.get("beatphase"),
                deck_info.get("syncphase"),
                bool(deck_info.get("play")),
                deck_info.get("bpm"),
                deck_info.get("keyAdjust"),
            )
        if phase_rows and probe_log.isEnabledFor(logging.DEBUG):
            probe_log.debug(
                "phase | "
                + "  ".join(
                    f"{d}{'*' if row[3] else ' '} ph={_fmt(row[0])} bp={_fmt(row[1])}"
                    f" sy={row[2]} bpm={_fmt(row[4])} ka={_fmt(row[5])}"
                    for d, row in sorted(phase_rows.items())
                )
            )
        for ch_info in _rows(data.get("channels")):
            idx = str(ch_info.get("index", ""))
            deck = _channel_to_deck(idx)
            if not is_deck(deck):
                continue
            watcher.deck_on_air.emit(deck, _num(ch_info.get("onAirLevel"), 0.0))
        # GUI layout change — re-detect deck letters when user flips
        # 2↔4-deck layout. Anything else previously sent in layout_probe
        # was development scaffolding and has been dropped.
        # Measured 2026-09-26 on Traktor 4.5.1 (macOS): the bridge's
        # AppProperty "app.traktor.gui.layout" yields nothing — no value
        # has ever arrived (nothing stored, no "layout changed" logged) —
        # and a probe of 15 other candidate paths (app.traktor.layout,
        # layout_selector, layouts.selected, decks.N.visible, …) found
        # none; only the control, app.traktor.browser.full_screen, read.
        # Deck layout is detected from screenshots instead (core.drag
        # auto-calibration, ui.layout_watch). Kept for a Traktor that
        # does expose it.
        new_layout = data.get("gui_layout")
        # Written to the settings below, so only a name is taken.
        if is_layout_name(new_layout) and new_layout not in ("null", "undefined"):
            old_layout = getattr(watcher, "_last_gui_layout", None)
            if new_layout != old_layout:
                watcher._last_gui_layout = new_layout
                # Persist the live layout so the library-overlay snap (in
                # core.drag, off the Qt signal graph) can read whether C/D
                # are on screen. Written on first sighting too, so the gap
                # is correct even without a 2↔4 toggle this session.
                QSettings().setValue("traktor_gui_layout", str(new_layout))
                if old_layout is not None:
                    probe_log.info(
                        "layout changed: %r → %r",
                        old_layout,
                        new_layout,
                    )
                    watcher.layout_changed.emit(str(old_layout), str(new_layout))
        if request_log.isEnabledFor(logging.DEBUG):
            request_log.debug(
                "keepAlive  decks=%s",
                [(clean_for_log(d.get("deck")), d.get("play")) for d in decks],
            )

    else:
        request_log.debug("unhandled POST path: %s", path)
        return

    watcher.traktor_ping.emit()
