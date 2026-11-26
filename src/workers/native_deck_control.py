"""Deck commands as direct calls into Traktor (integrated mode).

Takes the same command dicts :class:`workers.webhook.WebhookWatcher` queues for the
D2 QML bridge and carries them out through Traktor's own properties
(:mod:`workers.native_props`), with the bridge's timing: the load sequence, the
two-phase AutoMix ramp, the press-and-release of hotcues and beat jumps. The QML
(``Traktor/D2/Api/ApiModule.qml``) stays for TK running beside a stock Traktor;
this is its port for TK running inside Traktor.

One difference: the bridge loaded a track by typing its name into Traktor's search
field and pressing "load selected", after waiting 800 ms for that search. Here the
track is loaded by its path, directly, so nothing is typed and nothing waited for.
"""

from __future__ import annotations

import logging
import time
import unicodedata
from collections.abc import Callable

from PySide6.QtCore import QObject, QTimer

from workers.native_deck_state import DECKS, MASTER_TEMPO, channel_path, deck_path
from workers.native_props import PropertyBus

log = logging.getLogger(__name__)

PLAY_DELAY_MS = 600  # load → sync + play, as the bridge's playDelayTimer
OWN_LOAD_S = 15.0  # a deckLoaded this soon after TK's own load of the same file is that load
RELEASE_MS = 50  # a pressed trigger (hotcue, beat jump) is released after this
RAMP_STEP_MS = 100  # AutoMix ramp tick (10 Hz)
EQ_CENTRE = 0.5  # 0 dB on Traktor's EQ knobs
EQ_KILL = 0.0
FALLBACK_BPM = 120.0


def deck_index(deck: object) -> int | None:
    """0-3 for "A"-"D", None for anything else."""
    return DECKS.index(deck) if isinstance(deck, str) and deck in DECKS else None


def ramp_steps(master_bpm: float, bars: int) -> int:
    """Ticks of RAMP_STEP_MS in *bars* bars of 4 beats at *master_bpm*."""
    bpm = master_bpm if master_bpm > 20 else FALLBACK_BPM
    total_ms = 60000.0 / bpm * 4.0 * max(1, bars)
    return max(1, round(total_ms / RAMP_STEP_MS))


class _AutoMix(QObject):
    """The bridge's two-phase AutoMix transition onto deck *new*.

    Phase 1 raises the new deck's fader 0 → 1 over the ramp. Then the bass is
    swapped (killed on the outgoing decks, centred on the new one) and phase 2
    fades the outgoing decks out over the same time and stops them.
    """

    def __init__(self, props: PropertyBus, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._props = props
        self._timer = QTimer(self)
        self._timer.setInterval(RAMP_STEP_MS)
        self._timer.timeout.connect(self._step)
        self._new = 0
        self._steps = 1
        self._done = 0
        self._phase = 0
        self._outgoing: dict[int, float] = {}  # deck index → fader level at the bass swap

    def start(self, new: int, bars: int) -> None:
        self.stop()
        master = self._props.get(MASTER_TEMPO)
        self._steps = ramp_steps(float(master) if isinstance(master, float) else 0.0, bars)
        self._new, self._done, self._phase = new, 0, 1
        self._timer.start()
        log.info("AutoMix phase 1: deck %s over %d steps", DECKS[new], self._steps)

    def stop(self) -> None:
        self._timer.stop()
        self._phase = 0

    def active(self) -> bool:
        return self._timer.isActive()

    def _step(self) -> None:
        self._done += 1
        t = min(1.0, self._done / self._steps)
        finished = self._done >= self._steps
        if self._phase == 1:
            self._props.set(channel_path(self._new, "volume"), t)
            if finished:
                self._swap_bass()
            return
        for idx, level in self._outgoing.items():
            self._props.set(channel_path(idx, "volume"), level * (1.0 - t))
            if finished:
                self._props.set(deck_path(idx, "play"), False)
                self._props.set(channel_path(idx, "eq.low"), EQ_CENTRE)
        if finished:
            self.stop()
            log.info("AutoMix transition complete")

    def _swap_bass(self) -> None:
        self._props.set(channel_path(self._new, "volume"), 1.0)
        # The load killed the new deck's bass; it comes back here even with nothing to fade out.
        self._props.set(channel_path(self._new, "eq.low"), EQ_CENTRE)
        self._outgoing = {}
        for idx in range(len(DECKS)):
            if idx != self._new and self._props.get(deck_path(idx, "play")):
                level = self._props.get(channel_path(idx, "volume"))
                self._outgoing[idx] = float(level) if isinstance(level, float) else 0.0
        if not self._outgoing:
            self.stop()
            log.info("AutoMix complete: no outgoing deck")
            return
        for idx in self._outgoing:
            self._props.set(channel_path(idx, "eq.low"), EQ_KILL)
        self._done, self._phase = 0, 2
        log.info("AutoMix phase 2: fading %s", " ".join(DECKS[i] for i in self._outgoing))


class NativeDeckControl(QObject):
    """Carries out the watcher's command dicts against Traktor, natively."""

    def __init__(
        self,
        props: PropertyBus,
        resync: Callable[[], None],
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._props = props
        self._resync = resync
        self._automix = _AutoMix(props, self)
        self._mine: dict[int, tuple[str, float]] = {}  # deck -> (path TK loaded, when)

    def loaded_by_tk(self, deck: str, path: str) -> bool:
        """Did TK itself just load *path* onto *deck* (its own load, already given the mode)?"""
        idx = deck_index(deck)
        mine = self._mine.get(idx) if idx is not None else None
        if mine is None:
            return False
        same = unicodedata.normalize("NFC", mine[0]) == unicodedata.normalize("NFC", path)
        return same and time.monotonic() - mine[1] < OWN_LOAD_S

    def execute(self, cmd: dict) -> None:
        action = cmd.get("action")
        handler = {
            "load": self._load,
            "play": lambda c: self._set_play(c, True),
            "stop": lambda c: self._set_play(c, False),
            "hotcue": self._hotcue,
            "beatjump": self._beatjump,
            "sync": self._sync,
            "cancel_automix": lambda _c: self._automix.stop(),
            "resync": lambda _c: self._resync(),
        }.get(str(action))
        if handler is None:
            log.warning("native deck control: unknown command %s", cmd)
            return
        handler(cmd)

    # ── commands ─────────────────────────────────────────────────────────

    def _set_play(self, cmd: dict, on: bool) -> None:
        idx = deck_index(cmd.get("deck", "A"))
        if idx is not None:
            self._props.set(deck_path(idx, "play"), on)

    def _press(self, path: str, value: int | bool, released: int | bool) -> None:
        self._props.set(path, value)
        QTimer.singleShot(RELEASE_MS, self, lambda: self._props.set(path, released))

    def _hotcue(self, cmd: dict) -> None:
        idx = deck_index(cmd.get("deck", "A"))
        number = int(cmd.get("index", 0))
        if idx is None or not 1 <= number <= 8:
            return
        # Traktor's "Select/Set + Store Hotcue" — what a controller's hotcue pad does.
        self._press(deck_path(idx, "track.cue.select_or_set_hotcue"), number, 0)

    def _beatjump(self, cmd: dict) -> None:
        idx = deck_index(cmd.get("deck", "A"))
        if idx is not None:
            self._press(deck_path(idx, "beatjump"), int(cmd.get("value", 0)), 0)

    def _sync(self, cmd: dict) -> None:
        on = cmd.get("enabled", True) is not False
        idx = deck_index(cmd.get("deck"))
        targets = range(len(DECKS)) if idx is None else (idx,)
        for i in targets:
            self._props.set(deck_path(i, "sync.enabled"), on)

    def _load(self, cmd: dict) -> None:
        idx = deck_index(cmd.get("deck", "A"))
        if idx is None:
            log.warning("native load: no such deck in %s", cmd)
            return
        automix = cmd.get("automix") is True
        if cmd.get("mute") is not False:
            self._props.set(channel_path(idx, "volume"), 0.0)
        if automix:
            self._props.set(channel_path(idx, "eq.low"), EQ_KILL)
        if cmd.get("cue") is not False:
            for i in range(len(DECKS)):  # headphone cue on the target channel only
                self._props.set(channel_path(i, "cue"), i == idx)
        path = str(cmd.get("path") or "")
        if cmd.get("skip_load") is not True and path:
            self._mine[idx] = (
                path,
                time.monotonic(),
            )  # its deckLoaded must not trigger the mode again
            if not self._props.load_path(idx, path):
                log.warning("native load: Traktor refused %s", path)
        key_adjust = cmd.get("key_adjust")
        if isinstance(key_adjust, (int, float)):
            self._props.set(deck_path(idx, "track.key.adjust"), float(key_adjust))
        if cmd.get("play") is not False:
            bars = int(cmd.get("ramp_bars") or 4)
            QTimer.singleShot(PLAY_DELAY_MS, self, lambda: self._start(idx, automix, bars))

    def _start(self, idx: int, automix: bool, bars: int) -> None:
        self._props.set(deck_path(idx, "sync.enabled"), True)
        self._props.set(deck_path(idx, "play"), True)
        if automix:
            self._automix.start(idx, bars)
