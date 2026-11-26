"""Deck state from Traktor's own properties, as change callbacks (integrated mode).

The D2 QML bridge watches Traktor's AppProperties and POSTs what changed; this does
the same thing natively, through :mod:`workers.native_props`, and emits the very
signals :class:`workers.webhook.WebhookWatcher` emits for the bridge's posts, on the
watcher itself. Everything downstream (the Now Playing tab, matching, Auto DJ) is
unchanged and cannot tell the two apart, except that a change arrives at once
instead of on the bridge's next post or keep-alive.

What the bridge sent on its 1 s keep-alive and that changes continuously (playhead
position, beat phase) is read on a timer of the same period; the rest is watched.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from PySide6.QtCore import QObject, QTimer

from core.auto_dj_timing import last_hotcue_s
from workers.native_props import PropertyBus
from workers.webhook_posts import semitones_from_adjust

log = logging.getLogger(__name__)

DECKS = ("A", "B", "C", "D")
TICK_MS = 1000  # the D2 keep-alive's period
LOAD_SETTLE_MS = 50  # a load sets the path first; title, BPM … follow in the same turn
RELOAD_GAP_S = 2.0  # the same path again within this is the same load, reported late
HOTCUE_SLOTS = 8  # Traktor's hotcue slots per track
TEMPO_EPSILON = 0.005  # BPM: smaller wobbles (sync jitter) are not a tempo change


def deck_path(index: int, rest: str) -> str:
    return f"app.traktor.decks.{index + 1}.{rest}"


def channel_path(index: int, rest: str) -> str:
    return f"app.traktor.mixer.channels.{index + 1}.{rest}"


XFADER = "app.traktor.mixer.xfader.adjust"
MASTER_TEMPO = "app.traktor.masterclock.tempo"


def on_air_level(volume: float, assign_left: bool, assign_right: bool, xfader: float) -> float:
    """How loud a channel reaches the master: its fader, cut by the crossfader.

    The bridge's formula (ApiChannel.qml): a channel assigned to the side the
    crossfader is moving away from fades out linearly over the far half.
    """
    level = volume
    if (assign_left and xfader > 0.5) or (assign_right and xfader < 0.5):
        level *= 1.0 - abs(xfader * 2.0 - 1.0)
    return level


def _num(value: object, default: float = 0.0) -> float:
    return float(value) if isinstance(value, (int, float)) else default


class NativeDeckState(QObject):
    """Watches the four decks and the mixer; emits on *hub* (the webhook watcher)."""

    def __init__(self, props: PropertyBus, hub: QObject, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._props = props
        self._hub = hub
        self._loaded_path: dict[str, str] = {}
        self._loaded_at: dict[str, float] = {}
        self._playing: dict[str, bool] = {}
        self._level: dict[str, float] = {}
        self._tempo: dict[str, float] = {}
        self._key_shift: dict[str, int] = {}
        self._master: float = 0.0
        self._settle: dict[str, QTimer] = {}
        self._reload: set[str] = set()  # a load pulse came: report even the same path again
        self._tick = QTimer(self)
        self._tick.setInterval(TICK_MS)
        self._tick.timeout.connect(self._on_tick)

    # ── setup ────────────────────────────────────────────────────────────

    def start(self) -> None:
        for i, deck in enumerate(DECKS):
            settle = QTimer(self)
            settle.setSingleShot(True)
            settle.setInterval(LOAD_SETTLE_MS)
            settle.timeout.connect(lambda d=deck, i=i: self._on_settled(i, d))
            self._settle[deck] = settle
            self._watch(
                deck_path(i, "is_loaded_signal"), lambda i=i, d=deck: self._on_load_signal(i, d)
            )
            self._watch(deck_path(i, "track.content.file_path"), settle.start)
            self._watch(deck_path(i, "play"), lambda i=i, d=deck: self._on_play(i, d))
            self._watch(deck_path(i, "tempo.true_bpm"), lambda i=i, d=deck: self._on_tempo(i, d))
            self._watch(deck_path(i, "track.key.adjust"), lambda i=i, d=deck: self._on_key(i, d))
            for rest in ("volume", "xfader_assign.left", "xfader_assign.right"):
                self._watch(channel_path(i, rest), lambda i=i, d=deck: self._on_level(i, d))
        self._watch(XFADER, self._on_all_levels)
        self._watch(MASTER_TEMPO, self._on_master)
        self._tick.start()
        self.resync()
        log.info("native deck state: watching Traktor's decks and mixer directly")

    def stop(self) -> None:
        self._tick.stop()

    def set_hub(self, hub: QObject) -> None:
        """Emit on *hub* from now on (a reconnect makes a new watcher)."""
        self._hub = hub

    def _watch(self, path: str, fn: Callable[[], None]) -> None:
        if not self._props.watch(path, fn):
            log.warning("native deck state: cannot watch %s", path)

    def resync(self) -> None:
        """Send everything as it stands, as the bridge does on a reconnect."""
        self._loaded_path.clear()
        self._playing.clear()
        self._level.clear()
        self._tempo.clear()
        self._key_shift.clear()
        self._master = 0.0
        for i, deck in enumerate(DECKS):
            self._emit_loaded(i, deck, force=False)
            self._on_play(i, deck)
            self._on_level(i, deck)
            self._on_tempo(i, deck)
            self._on_key(i, deck)
        self._on_master()
        self._on_tick()

    # ── callbacks ────────────────────────────────────────────────────────

    def _get(self, path: str) -> object:
        return self._props.get(path)

    def _on_load_signal(self, i: int, deck: str) -> None:
        # Pulses on every load, also of the track already on the deck — and several
        # times per load (measured: 3-4), so it only arms the settle timer: one report.
        if self._get(deck_path(i, "is_loaded_signal")):
            self._reload.add(deck)
            self._settle[deck].start()

    def _on_settled(self, i: int, deck: str) -> None:
        force = deck in self._reload
        self._reload.discard(deck)
        self._emit_loaded(i, deck, force=force)

    def _emit_loaded(self, i: int, deck: str, *, force: bool) -> None:
        path = str(self._get(deck_path(i, "track.content.file_path")) or "")
        same = self._loaded_path.get(deck) == path
        now = time.monotonic()
        # Measured live: a load moves the path, then pulses is_loaded_signal ~1 s later.
        if not path or (
            same and (not force or now - self._loaded_at.get(deck, 0.0) < RELOAD_GAP_S)
        ):
            return
        self._loaded_path[deck] = path
        self._loaded_at[deck] = now
        info = {
            "title": str(self._get(deck_path(i, "content.title")) or ""),
            "artist": str(self._get(deck_path(i, "content.artist")) or ""),
            "album": str(self._get(deck_path(i, "content.album")) or ""),
            "genre": str(self._get(deck_path(i, "content.genre")) or ""),
            "bpm": _num(self._get(deck_path(i, "tempo.base_bpm"))),
            "key": str(self._get(deck_path(i, "content.musical_key")) or ""),
            "file_path": path,
            "track_length": _num(self._get(deck_path(i, "track.content.track_length"))),
            "last_hotcue_s": self._last_hotcue(i),  # where Auto DJ starts the transition
        }
        log.info("deckLoaded (native)  deck=%s  bpm=%.2f  %s", deck, info["bpm"], info["title"])
        self._hub.deck_loaded.emit(deck, info)

    def _last_hotcue(self, i: int) -> float | None:
        """The loaded track's last hotcue (s), from the deck's eight hotcue slots."""
        slots = []
        for slot in range(1, HOTCUE_SLOTS + 1):
            cue = f"track.cue.hotcues.{slot}."
            kind = self._get(deck_path(i, cue + "type"))
            slots.append(
                (
                    bool(self._get(deck_path(i, cue + "exists"))),
                    int(kind) if isinstance(kind, (int, float)) else -1,
                    _num(self._get(deck_path(i, cue + "start_pos"))),
                )
            )
        return last_hotcue_s(slots)

    def _on_play(self, i: int, deck: str) -> None:
        playing = bool(self._get(deck_path(i, "play")))
        if self._playing.get(deck) != playing:
            self._playing[deck] = playing
            self._hub.deck_state.emit(deck, playing)

    def _on_level(self, i: int, deck: str) -> None:
        level = on_air_level(
            _num(self._get(channel_path(i, "volume"))),
            bool(self._get(channel_path(i, "xfader_assign.left"))),
            bool(self._get(channel_path(i, "xfader_assign.right"))),
            _num(self._get(XFADER), 0.5),
        )
        if self._level.get(deck) != level:
            self._level[deck] = level
            self._hub.deck_on_air.emit(deck, level)

    def _on_all_levels(self) -> None:
        for i, deck in enumerate(DECKS):
            self._on_level(i, deck)

    def _on_tempo(self, i: int, deck: str) -> None:
        tempo = _num(self._get(deck_path(i, "tempo.true_bpm")))
        if tempo <= 0 or abs(tempo - self._tempo.get(deck, 0.0)) < TEMPO_EPSILON:
            return
        self._tempo[deck] = tempo
        self._hub.deck_tempo.emit(deck, tempo)

    def _on_key(self, i: int, deck: str) -> None:
        shift = semitones_from_adjust(self._get(deck_path(i, "track.key.adjust")))
        if self._key_shift.get(deck) != shift:
            self._key_shift[deck] = shift
            self._hub.deck_key_adjust.emit(deck, shift)

    def _on_master(self) -> None:
        bpm = _num(self._get(MASTER_TEMPO))
        if bpm > 0 and abs(bpm - self._master) >= TEMPO_EPSILON:
            self._master = bpm
            self._hub.master_tempo.emit(bpm)

    def _on_tick(self) -> None:
        """What the keep-alive carried: playhead position and beat phase."""
        for i, deck in enumerate(DECKS):
            length = _num(self._get(deck_path(i, "track.content.track_length")))
            if length > 0:
                elapsed = _num(self._get(deck_path(i, "track.player.playhead_position")))
                self._hub.deck_position.emit(deck, max(0.0, min(1.0, elapsed / length)))
            phase = self._get(deck_path(i, "tempo.phase"))
            if isinstance(phase, float):
                self._hub.deck_phase.emit(deck, phase)
        # Traktor is there and answering: what a bridge post proves.
        self._hub.traktor_ping.emit()
