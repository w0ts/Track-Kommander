"""Keep the display awake while Kommander is driving a set.

The app is a performance tool operated from hardware — during a set the mouse
may not move for an hour, so the screensaver kicks in and the display sleeps
over the thing the DJ is reading.

This uses the platforms' power-assertion APIs rather than nudging the cursor.
A synthetic pointer move needs the Accessibility grant on macOS, can land
inside Traktor and hit a control, and fights the user's own cursor; the
assertion APIs are one call and are exactly what this is for.

* macOS — ``IOPMAssertionCreateWithName`` with
  ``PreventUserIdleDisplaySleep``. Held until released; no re-arming needed.
* Windows — ``SetThreadExecutionState(ES_CONTINUOUS | ES_DISPLAY_REQUIRED)``,
  which is per-thread state that persists until cleared with ``ES_CONTINUOUS``.
* Anything else — no-op, and ``active`` stays False.

Neither call requires elevation or a permission grant.
"""

from __future__ import annotations

import ctypes
import logging
import sys

from core import platform_modules

log = logging.getLogger(__name__)

_IS_MAC = sys.platform == "darwin"
_IS_WIN = sys.platform == "win32"

# Shown to the user in macOS's `pmset -g assertions` output. It is also what
# the log says when the lock is taken, so it has to state the actual condition
# — an earlier "Kommander is connected to Traktor" was read as evidence of a
# working bridge when it only meant the app had noticed Traktor was running.
ASSERTION_REASON = "Kommander: Traktor is playing"

# IOKit constants (IOPMLib.h).
_kIOPMAssertionLevelOn = 255
_ASSERTION_TYPE = "PreventUserIdleDisplaySleep"

# Windows execution-state flags (winbase.h).
_ES_CONTINUOUS = 0x80000000
_ES_DISPLAY_REQUIRED = 0x00000002


class DisplayWakeLock:
    """Holds a "keep the display awake" assertion.

    Idempotent: repeated ``acquire`` / ``release`` calls are harmless, so a
    caller can simply reflect the current connection state into ``set_held``
    without tracking transitions itself.

    Failures are logged and swallowed — a screensaver that comes on is a minor
    annoyance, not a reason to break the app.
    """

    def __init__(self, reason: str = ASSERTION_REASON) -> None:
        self._reason = reason
        self._assertion_id: int | None = None  # macOS only
        self._held = False

    @property
    def held(self) -> bool:
        """True while the display is being kept awake by this lock."""
        return self._held

    @staticmethod
    def supported() -> bool:
        """True on platforms where the assertion can actually be taken."""
        return _IS_MAC or _IS_WIN

    def set_held(self, held: bool) -> None:
        """Acquire or release to match *held*."""
        if held:
            self.acquire()
        else:
            self.release()

    def acquire(self) -> bool:
        """Start keeping the display awake. True if the lock is now held."""
        if self._held:
            return True
        if _IS_MAC:
            self._held = self._mac_acquire()
        elif _IS_WIN:
            self._held = self._win_set(_ES_CONTINUOUS | _ES_DISPLAY_REQUIRED)
        else:
            return False
        if self._held:
            log.info("display wake lock acquired (%s)", self._reason)
        return self._held

    def release(self) -> None:
        """Stop keeping the display awake."""
        if not self._held:
            return
        if _IS_MAC:
            self._mac_release()
        elif _IS_WIN:
            self._win_set(_ES_CONTINUOUS)
        self._held = False
        log.info("display wake lock released")

    # ── macOS ───────────────────────────────────────────────────────────────

    def _mac_acquire(self) -> bool:
        try:
            iokit = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/IOKit.framework/IOKit")
            cf = ctypes.cdll.LoadLibrary(
                "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
            )
            cf.CFStringCreateWithCString.restype = ctypes.c_void_p
            cf.CFStringCreateWithCString.argtypes = [
                ctypes.c_void_p,
                ctypes.c_char_p,
                ctypes.c_uint32,
            ]
            _kCFStringEncodingUTF8 = 0x08000100

            a_type = cf.CFStringCreateWithCString(
                None, _ASSERTION_TYPE.encode(), _kCFStringEncodingUTF8
            )
            a_reason = cf.CFStringCreateWithCString(
                None, self._reason.encode(), _kCFStringEncodingUTF8
            )

            iokit.IOPMAssertionCreateWithName.argtypes = [
                ctypes.c_void_p,
                ctypes.c_uint32,
                ctypes.c_void_p,
                ctypes.POINTER(ctypes.c_uint32),
            ]
            iokit.IOPMAssertionCreateWithName.restype = ctypes.c_int

            assertion_id = ctypes.c_uint32(0)
            err = iokit.IOPMAssertionCreateWithName(
                a_type,
                _kIOPMAssertionLevelOn,
                a_reason,
                ctypes.byref(assertion_id),
            )
            if err != 0:
                log.warning("IOPMAssertionCreateWithName failed: %d", err)
                return False
            self._assertion_id = assertion_id.value
            return True
        except Exception:
            log.warning("display wake lock unavailable", exc_info=True)
            return False

    def _mac_release(self) -> None:
        if self._assertion_id is None:
            return
        try:
            iokit = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/IOKit.framework/IOKit")
            iokit.IOPMAssertionRelease.argtypes = [ctypes.c_uint32]
            iokit.IOPMAssertionRelease.restype = ctypes.c_int
            iokit.IOPMAssertionRelease(ctypes.c_uint32(self._assertion_id))
        except Exception:
            log.warning("releasing display wake lock failed", exc_info=True)
        finally:
            self._assertion_id = None

    # ── Windows ─────────────────────────────────────────────────────────────

    @staticmethod
    def _win_set(flags: int) -> bool:
        try:
            kernel32 = platform_modules.win_ctypes().windll.kernel32
            kernel32.SetThreadExecutionState.argtypes = [ctypes.c_uint32]
            kernel32.SetThreadExecutionState.restype = ctypes.c_uint32
            previous = kernel32.SetThreadExecutionState(ctypes.c_uint32(flags))
            if previous == 0:
                log.warning("SetThreadExecutionState(0x%x) failed", flags)
                return False
            return True
        except Exception:
            log.warning("display wake lock unavailable", exc_info=True)
            return False


# ── When to hold it ─────────────────────────────────────────────────────────

#: How long the display stays awake after the last deck stops. On macOS the
#: idle timer keeps counting while an assertion is held, so releasing the
#: instant playback stops would blank the screen *immediately* on a set that
#: has run hands-off for twenty minutes — the moment the DJ pauses to talk.
GRACE_AFTER_STOP_S = 90.0

#: The bridge reports deck state about once a second (see
#: ``core.d2_status.QUIET_AFTER_S``). Older than this, its last word cannot
#: vouch for what is playing now.
STALE_AFTER_S = 5.0


class PlaybackWakePolicy:
    """Hold the display awake only while Traktor is playing something.

    The lock used to be held for as long as Traktor was *running*, which kept
    the screen lit all evening for a Traktor left open with nothing on the
    decks. Playback is what a DJ is reading the screen for, so playback is
    the condition. Pure state, fed with an explicit clock; the caller
    reflects :meth:`wanted` into :class:`DisplayWakeLock`.
    """

    def __init__(self, grace_s: float = GRACE_AFTER_STOP_S, stale_s: float = STALE_AFTER_S) -> None:
        self._grace_s = grace_s
        self._stale_s = stale_s
        self._playing: set[str] = set()
        self._last_report_at: float | None = None  # any deck state arrived
        self._last_playing_at: float | None = None  # a deck was known playing

    def deck(self, deck: str, playing: bool, now: float) -> None:
        """One deck's play state, as the bridge reported it at *now*."""
        self._last_report_at = now
        if playing:
            self._playing.add(deck)
            self._last_playing_at = now
        else:
            if deck in self._playing:
                self._last_playing_at = now  # it was playing until now
            self._playing.discard(deck)

    def traktor_gone(self) -> None:
        """Traktor is not running: nothing is being read, release at once."""
        self._playing.clear()
        self._last_report_at = None
        self._last_playing_at = None

    def wanted(self, now: float, enabled: bool = True) -> bool:
        """Should the display be held awake right now?"""
        if not enabled:
            return False
        fresh = self._last_report_at is not None and now - self._last_report_at <= self._stale_s
        if self._playing and fresh:
            return True
        # Stopped, or the bridge went quiet mid-track: through the grace only.
        return self._last_playing_at is not None and now - self._last_playing_at <= self._grace_s
