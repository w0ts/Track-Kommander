"""WebhookWatcher — embedded HTTP server that receives Traktor D2 QML posts.

The watcher: its thread, its signals and the queue of commands for Traktor.
The rest of the bridge's server is beside it —

* :mod:`workers.webhook_server`: the sockets and the request handler;
* :mod:`workers.webhook_guard`: what a request must look like to be served;
* :mod:`workers.webhook_posts`: what a post means.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from core import traktor_process
from core.constants import WEBHOOK_PORT
from core.prefs import AUTOMATION_MODE, RAMP_BARS
from core.runtime import in_traktor
from workers.webhook_posts import (
    SEMITONE,
    _num,
    handle_post,
    key_adjust_value,
    semitones_from_adjust,
)
from workers.webhook_server import (
    _LONG_POLL_MAX_S,
    _LONG_POLL_SLICE_S,
    _PROBE_PATH,
    _SHUTDOWN_POLL_S,
    REQUEST_TIMEOUT_S,
    _long_poll_wait,
    _make_handler,
    _ReuseServer,
    _ReuseServer6,
    bind_ipv6_loopback,
    port_holder,
)

#: Everything that used to live in this module is still reachable from it.
__all__ = [
    "COMMAND_MAX_AGE_S",
    "REQUEST_TIMEOUT_S",
    "SEMITONE",
    "WebhookWatcher",
    "_LONG_POLL_MAX_S",
    "_LONG_POLL_SLICE_S",
    "_PROBE_PATH",
    "_ReuseServer",
    "_ReuseServer6",
    "_long_poll_wait",
    "_make_handler",
    "_num",
    "bind_ipv6_loopback",
    "key_adjust_value",
    "port_holder",
    "semitones_from_adjust",
    "traktor_search",
]

# Set to True to log every incoming webhook request (useful for debugging Traktor QML)
_DEBUG_WEBHOOK = False

# Follows the root level, so INFO diagnostics such as "webhook bound: …"
# reach log.log. It used to be clamped to WARNING, which hid those too.
log = logging.getLogger("webhook")
if _DEBUG_WEBHOOK:
    log.setLevel(logging.DEBUG)

# Per-request traces. The bridge posts several times a second and polls ten
# times a second, so these stay out of even a verbose log unless
# _DEBUG_WEBHOOK asks for them — that was the only job the old clamp did.
request_log = logging.getLogger("webhook.requests")
request_log.setLevel(logging.DEBUG if _DEBUG_WEBHOOK else logging.INFO)

# Property probe diagnostics — inherits the root level.
probe_log = logging.getLogger("probe")


def traktor_search(search: str) -> None:
    """Pre-fill Traktor's browser search field.

    Uses CGEventPostToPid (no focus steal, requires pyobjc).
    No-op when dependencies are absent.

    pyobjc modules are pre-loaded here on the main thread to avoid an
    ImportError when AppKit/Quartz are first imported from a background thread
    while PySide6 already owns the Cocoa run loop.
    """
    try:
        import AppKit  # noqa: F401
        import Quartz  # noqa: F401
    except ImportError as e:
        log.warning("traktor_search: pyobjc not available — %s", e)
        return
    log.debug("traktor_search: %r", search)
    threading.Thread(target=_traktor_search_worker, args=(search,), daemon=True).start()


def _traktor_search_worker(search: str) -> None:
    try:
        _traktor_search_pyobjc(search)
    except Exception as exc:
        log.warning("traktor_search: %s", exc)


def _traktor_pid() -> int:
    """Return Traktor's PID, or raise RuntimeError if not running."""
    found = traktor_process.first_app()
    if found is None:
        raise RuntimeError("Traktor is not running")
    return found[0]


def _traktor_search_pyobjc(search: str) -> None:
    """Fill Traktor's browser search field without stealing window focus.

    Uses CGEventPostToPid to inject Cmd+F / Cmd+A then types each character
    directly via CGEventKeyboardSetUnicodeString.  No clipboard is touched.

    Key sequence:
      Cmd+F  — focus / open Traktor's browser search field
      Cmd+A  — select any existing text
      type   — each character injected directly into Traktor's event queue
    """
    import time

    import Quartz as Q

    pid = _traktor_pid()

    src = Q.CGEventSourceCreate(Q.kCGEventSourceStateHIDSystemState)
    cmd = Q.kCGEventFlagMaskCommand

    def post(keycode: int, flags: int = 0) -> None:
        for down in (True, False):
            ev = Q.CGEventCreateKeyboardEvent(src, keycode, down)
            if flags:
                Q.CGEventSetFlags(ev, flags)
            Q.CGEventPostToPid(pid, ev)

    post(3, cmd)  # Cmd+F  — focus browser search field
    time.sleep(0.15)  # give Traktor time to open/focus the field
    post(0, cmd)  # Cmd+A  — select all existing text
    for char in search:
        for down in (True, False):
            ev = Q.CGEventCreateKeyboardEvent(src, 0, down)
            Q.CGEventKeyboardSetUnicodeString(ev, 1, char)
            Q.CGEventPostToPid(pid, ev)
    log.debug("traktor_search: Cmd+F/A + typed %d chars → pid=%d", len(search), pid)


#: How long a command may wait for the bridge to collect it. The bridge holds
#: a request open for commands the whole time it is connected, so one is
#: normally collected at once; one that is still here after this long was
#: queued while Traktor was not listening, and playing, muting or loading
#: something that late is worse than not doing it.
COMMAND_MAX_AGE_S = 10.0


def _now() -> float:
    return time.monotonic()


class _Command(dict):
    """A queued command, and when it was queued.

    Still a plain dict to everything that reads it or sends it; the time
    rides along as an attribute so it never reaches the bridge.
    """

    def __init__(self, fields: dict, *, queued_at: float) -> None:
        super().__init__(fields)
        self.queued_at = queued_at


class _HeldPort:
    """How the server waits while another process holds its port.

    It looks again every few seconds, so it takes the port over soon after
    the other copy closes, but it must not write one error line each time:
    one run logged 9,600 of them (2026-11-06). It says so when the holder
    changes and then once a minute, and waits longer the longer it has been
    held, up to :attr:`MAX_DELAY_S`.
    """

    FIRST_DELAY_S = 5.0
    MAX_DELAY_S = 30.0
    REPEAT_LOG_S = 60.0

    def __init__(self) -> None:
        self.holder: str | None = None
        self.delay_s = self.FIRST_DELAY_S
        self._logged_at = float("-inf")

    def should_log(self, holder: str, now: float) -> bool:
        """True when this sighting of *holder* deserves a log line."""
        if holder != self.holder or now - self._logged_at >= self.REPEAT_LOG_S:
            self.holder = holder
            self._logged_at = now
            return True
        return False

    def waited(self) -> None:
        self.delay_s = min(self.delay_s * 2, self.MAX_DELAY_S)

    def clear(self) -> None:
        """The port is free: forget the wait."""
        self.holder = None
        self.delay_s = self.FIRST_DELAY_S
        self._logged_at = float("-inf")


class WebhookWatcher(QThread):
    track_changed = Signal(str)
    status = Signal(str)
    is_running = Signal(bool)
    deck_state = Signal(str, bool)  # deck letter, is_playing
    deck_loaded = Signal(str, dict)  # deck letter, full track info dict
    deck_on_air = Signal(str, float)  # deck letter, volume (0.0–1.0)
    deck_position = Signal(str, float)  # deck letter, playhead 0.0–1.0
    master_tempo = Signal(float)  # master clock BPM
    traktor_ping = Signal()  # fired on any valid POST from Traktor
    d2_version = Signal(str)  # D2_VERSION string from keepAlive
    layout_changed = Signal(str, str)  # old, new — e.g. "twoDecks" → "fourDecks"
    deck_phase = Signal(str, float)  # deck, beat phase against the master clock
    deck_key_adjust = Signal(str, int)  # deck, Key-knob position in semitones
    deck_tempo = Signal(str, float)  # deck, BPM it plays at (pitch included)
    show_requested = Signal()  # native opener (libtk) asked us to raise the window
    native_track = Signal(dict)  # track metadata read via libtk's native C++ calls
    ui_action = Signal(str)  # a button in libtk's native band inside Traktor was clicked
    ui_card = Signal(
        str, str, int
    )  # a card slider moved in Traktor: axis key, "value"/"strength", 0–100

    # Inside Traktor (integrated mode) commands are carried out by direct calls and
    # deck state comes from Traktor's own change callbacks: see attach_native().
    _command_sink: Callable[[dict], None] | None = None
    native_state: bool = False

    def __init__(self, port: int = WEBHOOK_PORT, parent=None):
        super().__init__(parent)
        self.port = port
        self._stop = False
        self._server: _ReuseServer | None = None
        self._server6: _ReuseServer6 | None = None
        # A Condition, not a bare Lock: a held long-poll /command request
        # waits on it and is woken the moment a command is queued.
        self._cmd_lock = threading.Condition()
        # FIFO queue so simultaneous play/stop calls (e.g. W and S pressed
        # together) don't overwrite each other before the QML script polls.
        self._pending_cmds: deque[dict] = deque()
        self._resync_pending: bool = False

    def attach_native(self, sink: Callable[[dict], None]) -> None:
        """Send every command to *sink* instead of queuing it for the D2 bridge.

        Also marks deck state as native: the bridge's own state posts (if a D2
        mapping is loaded too) are then ignored, or every change would arrive twice.
        """
        self._command_sink = sink
        self.native_state = True

    def stop(self) -> None:
        self._stop = True
        with self._cmd_lock:
            self._cmd_lock.notify_all()  # release held /command requests
        # Read once: run() clears it on its way out, from its own thread.
        server = self._server
        if server:
            server.shutdown()
        self._stop_ipv6()

    def _start_ipv6(self, handler) -> None:
        """Serve ``[::1]`` beside 127.0.0.1 — see :func:`bind_ipv6_loopback`."""
        server6 = bind_ipv6_loopback(self.port, handler)
        if server6 is None:
            return
        self._server6 = server6
        threading.Thread(
            target=server6.serve_forever,
            kwargs={"poll_interval": _SHUTDOWN_POLL_S},
            name="webhook-ipv6",
            daemon=True,
        ).start()
        log.info("webhook bound: socket=%s (IPv6 loopback)", server6.server_address)

    def _stop_ipv6(self) -> None:
        server6, self._server6 = self._server6, None
        if server6 is not None:
            server6.shutdown()
            server6.server_close()

    def queue_load(
        self, path: str, deck: str, skip_load: bool = False, transpose: int | None = None
    ) -> None:
        """Queue a track-load command to be picked up by the D2 QML script.

        *transpose* is the Key-knob shift in semitones this match needs — the
        number the Key-T column shows — or None when the Key axis is not in
        Key-T mode, in which case the knob is left exactly as the DJ set it.

        Within Key-T, 0 is sent rather than omitted: a deck keeps whatever
        transpose it was last left on, so a track that needs none has to say
        so or it inherits the previous one.

        When ``skip_load`` is True, Track Kommander has already placed the
        track on the deck (via a synthesized drag), so the QML must NOT
        re-search and re-load — it only runs the automation steps (mute /
        cue / sync+play / AutoMix). In that case we also skip
        ``traktor_search`` so we don't disturb Traktor's browser at all.
        """
        semitones = None if transpose is None else max(-12, min(12, int(transpose)))
        search = Path(path).stem  # filename without extension → used by QML fallback
        mode = AUTOMATION_MODE.get()
        ramp_bars = RAMP_BARS.get()
        play = mode != "off"
        mute = mode != "off"  # always mute before load so ramp starts from 0
        cue = mode != "off"
        raise_vol = mode in ("automix", "auto")
        cmd = {
            "action": "load",
            "deck": deck,
            "search": search,
            "path": path,
            "play": play,
            "mute": mute,
            "cue": cue,
            "automix": raise_vol,
            "skip_load": skip_load,
            "ramp_bars": ramp_bars,
            "transpose": semitones,
            "key_adjust": (None if semitones is None else key_adjust_value(semitones)),
        }
        log.debug(
            "queue_load  deck=%s  mode=%s  skip_load=%s  ramp_bars=%d  path=%s",
            deck,
            mode,
            skip_load,
            ramp_bars,
            path,
        )
        self._enqueue(cmd)

        if not skip_load and self._command_sink is None:  # natively, loads go by path
            traktor_search(f'"{path}"')  # quoted full path → exact match in Traktor browser

    def queue_stop(self, deck: str) -> None:
        """Queue a stop command for a deck (picked up by the D2 QML poll)."""
        cmd = {"action": "stop", "deck": deck}
        log.debug("queue_stop  deck=%s", deck)
        self._enqueue(cmd)

    def queue_mute(self, deck: str) -> None:
        """Set the mixer channel fader for *deck* to zero.

        Reuses the existing ``load`` action in the QML (see
        ``Traktor/D2/Api/ApiModule.qml`` — ``loadDelayTimer`` writes
        ``volCh{1..4}.value = 0.0`` when ``doMute`` is true). Sending
        ``skip_load=True`` with ``play=cue=automix=False`` drives only the
        channel-mute path — no track is loaded, no PFL is touched, no
        playback is started.
        """
        cmd = {
            "action": "load",
            "deck": deck,
            "search": "",
            "path": "",
            "play": False,
            "mute": True,
            "cue": False,
            "automix": False,
            "skip_load": True,
            "ramp_bars": 0,
        }
        log.debug("queue_mute  deck=%s", deck)
        self._enqueue(cmd)

    def queue_play(self, deck: str) -> None:
        """Queue a play command for a deck (picked up by the D2 QML poll)."""
        cmd = {"action": "play", "deck": deck}
        log.debug("queue_play  deck=%s", deck)
        self._enqueue(cmd)

    def queue_cancel_automix(self) -> None:
        """Cancel any in-progress fader/EQ ramp in the QML script."""
        log.debug("queue_cancel_automix")
        self._enqueue({"action": "cancel_automix"})

    def queue_hotcue(self, deck: str, index: int) -> None:
        """Trigger hotcue *index* (1–8) on *deck* (A/B/C/D)."""
        cmd = {"action": "hotcue", "deck": deck, "index": int(index)}
        log.debug("queue_hotcue  deck=%s  idx=%d", deck, index)
        self._enqueue(cmd)

    def queue_beatjump(self, deck: str, value: int) -> None:
        """Beat-jump *deck* (A/B/C/D) by ``value`` (JumpSize enum code).

        Common values (see Traktor's qml/Defines/JumpSize.qml):
          -10 → back 8 beats,   +10 → fwd 8 beats
          -11 → back 16 beats,  +11 → fwd 16 beats
        """
        cmd = {"action": "beatjump", "deck": deck, "value": int(value)}
        log.debug("queue_beatjump  deck=%s  value=%d", deck, value)
        self._enqueue(cmd)

    def queue_sync(self, deck: str | None = None, enabled: bool = True) -> None:
        """Arm (or disarm) Traktor's Sync on one deck, or on every deck.

        Traktor has no global Sync — it is a per-deck button with no way to
        arm them all, not even from a mapping. Writing each deck's
        ``sync.enabled`` through the bridge is the same thing pressing the
        button does, which is how Kommander offers the switch Traktor does
        not. ``deck=None`` means all four.
        """
        cmd: dict = {"action": "sync", "enabled": bool(enabled)}
        if deck:
            cmd["deck"] = deck
        log.debug("queue_sync  deck=%s  enabled=%s", deck or "all", enabled)
        self._enqueue(cmd)

    def request_resync(self) -> None:
        """Ask the QML bridge to re-send the full deck/channel state.

        Queued automatically whenever the server (re)binds; exposed publicly so
        the user can force it from Settings when the connection looks stale but
        neither side needs restarting.
        """
        if self._command_sink is not None:
            self._command_sink({"action": "resync"})
            return
        if in_traktor():
            return  # no D2 bridge to ask inside Traktor
        with self._cmd_lock:
            self._resync_pending = True
            self._cmd_lock.notify_all()
        log.info("resync requested")

    def _enqueue(self, cmd: dict) -> None:
        """Add *cmd* to the queue, stamped with the time, and wake a held
        long poll — or hand it straight to the native sink when there is one."""
        if self._command_sink is not None:
            self._command_sink(cmd)
            return
        if in_traktor():
            # Inside Traktor there is no D2 bridge to pick it up (User, 2026-11-24): without the
            # native sink (libtk's property API missing) the command cannot reach Traktor.
            log.warning(
                "command %s dropped: no native command path inside Traktor", cmd.get("action")
            )
            return
        with self._cmd_lock:
            self._pending_cmds.append(_Command(cmd, queued_at=_now()))
            self._cmd_lock.notify_all()

    def _take_fresh(self) -> dict | None:
        """The oldest command still worth delivering. Call with the lock held.

        One that waited longer than :data:`COMMAND_MAX_AGE_S` is dropped, and
        said so: it was an instruction for then, and the bridge was not
        listening.
        """
        while self._pending_cmds:
            cmd = self._pending_cmds.popleft()
            age = _now() - getattr(cmd, "queued_at", _now())
            if age <= COMMAND_MAX_AGE_S:
                return cmd
            log.warning(
                "dropped %s: it waited %.0f s for a bridge that was not listening", dict(cmd), age
            )
        return None

    def _pop_command(self) -> dict | None:
        with self._cmd_lock:
            if self._resync_pending:
                self._resync_pending = False
                return {"action": "resync"}
            return self._take_fresh()

    def _wait_command(
        self, timeout_s: float, client_gone: Callable[[], bool] = lambda: False
    ) -> dict | None:
        """The next command, waiting up to *timeout_s* for one (long poll).

        Checks every :data:`_LONG_POLL_SLICE_S` that the client is still
        connected, and takes a command off the queue only for a live one: a
        request Traktor has already dropped — at a loss, or on the macOS
        fresh-host switch — must not swallow the command meant for its
        replacement.
        """
        deadline = time.monotonic() + timeout_s
        with self._cmd_lock:
            while True:
                if self._resync_pending or self._pending_cmds:
                    if client_gone():
                        return None
                    if self._resync_pending:
                        self._resync_pending = False
                        return {"action": "resync"}
                    cmd = self._take_fresh()
                    if cmd is not None:
                        return cmd
                    # Everything queued had gone stale: go on waiting.
                left = deadline - time.monotonic()
                if left <= 0 or self._stop or client_gone():
                    return None
                self._cmd_lock.wait(min(left, _LONG_POLL_SLICE_S))

    def _requeue(self, cmd: dict) -> None:
        """Put back a command whose reply could not be delivered, first in line."""
        with self._cmd_lock:
            if cmd.get("action") == "resync":
                self._resync_pending = True
            else:
                self._pending_cmds.appendleft(cmd)
            self._cmd_lock.notify_all()

    def run(self) -> None:
        Handler = _make_handler(self)

        _RETRY_DELAY_S = 5  # seconds between restart attempts after a crash
        held = _HeldPort()

        while not self._stop:
            if held.holder is None:  # while the port stays held, say it once
                log.info("starting webhook server on port %d", self.port)

            # Ask before binding, because binding will not tell us. With
            # SO_REUSEADDR a second instance binds happily while the first
            # keeps every request, and the only symptom is a bridge that
            # says "listening" and never receives anything. That cost an
            # afternoon once: a copy running from the IDE held the port
            # while the installed app reported itself healthy.
            holder = port_holder(self.port)
            if holder is not None:
                if held.should_log(holder, time.monotonic()):
                    log.error("port %d is already held by %s — not binding", self.port, holder)
                self.status.emit(
                    f"Port {self.port} is already in use by {holder} — "
                    f"close it, or change the port in Settings"
                )
                self.is_running.emit(False)
                for _ in range(int(held.delay_s * 10)):
                    if self._stop:
                        break
                    self.msleep(100)
                held.waited()
                continue
            held.clear()

            try:
                with _ReuseServer(("127.0.0.1", self.port), Handler) as server:
                    self._server = server
                    self._start_ipv6(Handler)
                    with self._cmd_lock:
                        self._resync_pending = True  # ask QML to re-send deck state
                        self._cmd_lock.notify_all()
                    # Log the socket we actually got. SO_REUSEADDR means a bind
                    # can succeed while a previous process still holds the port,
                    # so "listening" alone doesn't prove we receive anything —
                    # this line is what to compare across a working and a
                    # non-working start order.
                    log.info(
                        "webhook bound: socket=%s fd=%d pid=%d",
                        server.server_address,
                        server.fileno(),
                        os.getpid(),
                    )
                    self.status.emit(f"Webhook server listening on port {self.port}")
                    self.is_running.emit(True)
                    server.serve_forever(poll_interval=_SHUTDOWN_POLL_S)
                    # serve_forever() returned normally → stop() was called
            except OSError as exc:
                if self._stop:
                    break
                log.error("webhook server error: %s — retrying in %ds", exc, _RETRY_DELAY_S)
                self.status.emit(f"Webhook error: {exc} — retrying in {_RETRY_DELAY_S}s…")
                self.is_running.emit(False)
                # Sleep in small increments so stop() can interrupt quickly
                for _ in range(_RETRY_DELAY_S * 10):
                    if self._stop:
                        break
                    self.msleep(100)
                continue  # try binding again
            finally:
                self._server = None
                self._stop_ipv6()

            break  # clean shutdown requested — exit loop

        log.info("webhook server stopped")
        self.is_running.emit(False)

    def _handle_post(self, path: str, body: str) -> None:
        handle_post(self, path, body)
