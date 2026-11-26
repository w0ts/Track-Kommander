"""Forward keystrokes to Traktor.

Kommander sits on top of Traktor and holds keyboard focus while the DJ browses,
which means every key Traktor would normally act on is swallowed. Rather than
re-implement Traktor's keyboard mapping, Kommander claims only the few keys it
needs — move through the result list, load a deck — and passes everything else
straight through to Traktor, where the user's own mapping already lives.

macOS uses ``CGEventPostToPid``: the event goes to Traktor's process without
Kommander giving up focus, the same mechanism
``workers.webhook.traktor_search`` already uses to type into Traktor's browser.
It needs the Accessibility grant, which the app asks for anyway.

Press and release are forwarded as they happen rather than as one synthesised
press, so the key is down in Traktor for exactly as long as the user holds it.
Traktor's mapping ignores a zero-length press outright (measured against
Traktor Pro 4: 0 ms and 2 ms holds did nothing, 5 ms fired every time), and
hold-style mappings need the true duration. The cost is bookkeeping: a press
we forward owns its release, and :func:`release_all` frees anything still held
when the keyboard goes away.

Windows has no post-to-pid. The two candidates both have a catch:

* ``SendInput`` goes to whatever has focus — which is us. Reaching Traktor
  means taking focus first, and doing that on every press *and* release
  would make the window flicker for the entire time a key is held. It also
  breaks the model below, where a key stays down in Traktor while the user
  holds it.
* ``PostMessage`` addresses a window directly and changes no focus, which is
  the behaviour we want. The catch is that an application only sees it if it
  reads the keyboard through its message queue; one using DirectInput or Raw
  Input never looks.

``PostMessage`` is what is implemented, because it is the one that preserves
the semantics. Traktor Pro on Windows does read its message queue: a posted
key was seen to act in Traktor on 2026-09-29 (one Space, posted to its
window by ``verify_keyforward.py``, watched by hand).
"""

from __future__ import annotations

import ctypes
import logging
import os
import sys
import time
from ctypes import wintypes

from core import platform_modules, traktor_process

log = logging.getLogger(__name__)

_win = platform_modules.win_ctypes()

_IS_MAC = sys.platform == "darwin"
_IS_WIN = sys.platform == "win32"

# Qt modifier → CGEventFlags. Values from Carbon's CGEventTypes.h.
_CG_SHIFT = 0x00020000
_CG_CTRL = 0x00040000
_CG_ALT = 0x00080000
_CG_CMD = 0x00100000


# ── Windows message constants ──────────────────────────────────────────────
_WM_KEYDOWN = 0x0100
_WM_KEYUP = 0x0101
_WM_SYSKEYDOWN = 0x0104  # a key pressed with Alt held
_WM_SYSKEYUP = 0x0105
_MAPVK_VK_TO_VSC = 0
#: lParam bit 24 — the key is one of the "extended" set (arrows, navigation
#: block, right-hand Ctrl/Alt, keypad Enter and Divide). An app that reads the
#: scan code cannot tell Home from keypad-7 without it.
_KF_EXTENDED = 0x01000000
_VK_SHIFT, _VK_CONTROL, _VK_MENU = 0x10, 0x11, 0x12
#: Windows virtual keys that must carry the extended flag.
_EXTENDED_VKS = frozenset(
    {
        0x21,
        0x22,
        0x23,
        0x24,  # PageUp PageDown End Home
        0x25,
        0x26,
        0x27,
        0x28,  # Left Up Right Down
        0x2D,
        0x2E,  # Insert Delete
        0x6F,  # keypad Divide
        0x90,  # NumLock
        0xA3,
        0xA5,  # right Ctrl, right Alt
    }
)


def supported() -> bool:
    """True where keystrokes can actually reach Traktor."""
    return _IS_MAC or _IS_WIN


#: The Traktor found last time: (pid, its NSRunningApplication). Every key is
#: a press and a release, and finding Traktor walks every running app, so the
#: answer is kept while that process lives (see :func:`_still_running`).
_traktor_app: tuple[int, object] | None = None
#: When no Traktor was found, don't look again before this (monotonic) time:
#: typing in the search box with Traktor closed is the common case.
_no_traktor_until: float = 0.0
_NO_TRAKTOR_RECHECK_S = 2.0


def traktor_pid() -> int | None:
    """PID of the running Traktor, or None."""
    global _traktor_app, _no_traktor_until
    if not _IS_MAC:
        return None
    cached = _traktor_app
    if cached is not None and _still_running(*cached):
        return cached[0]
    now = time.monotonic()
    if cached is None and now < _no_traktor_until:
        return None
    _traktor_app = _find_traktor_app()
    if _traktor_app is None:
        _no_traktor_until = now + _NO_TRAKTOR_RECHECK_S
        return None
    return _traktor_app[0]


def _forget_traktor() -> None:
    """Drop the cached Traktor so the next key looks it up again."""
    global _traktor_app, _no_traktor_until
    _traktor_app = None
    _no_traktor_until = 0.0


def _find_traktor_app() -> tuple[int, object] | None:
    """Walk the running apps for Traktor: (pid, NSRunningApplication)."""
    try:
        return traktor_process.first_app()
    except Exception:
        log.debug("traktor_pid failed", exc_info=True)
    return None


def _still_running(pid: int, app) -> bool:
    """Whether the Traktor we found is still that running process.

    ``isTerminated`` is AppKit's own answer; the signal-0 probe backs it up,
    since AppKit only learns of the exit through the main run loop.
    """
    try:
        if app.isTerminated():
            return False
    except Exception:
        log.debug("isTerminated failed; looking Traktor up again", exc_info=True)
        return False
    return _process_exists(pid)


def _process_exists(pid: int) -> bool:
    """Whether *pid* is a live process, asked without disturbing it.

    Signal 0 is that question on POSIX. Windows has no such probe: there
    ``os.kill``'s "signal" 0 is CTRL_C_EVENT, delivered to the target's
    console group — which interrupted the test suite on the GitHub runner,
    where the target was pytest itself. This is only reached on macOS, as
    the backup to AppKit's answer; elsewhere that answer stands.
    """
    if sys.platform == "win32":
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, merely not ours to signal
    return True


class _Win32:
    """user32 and kernel32, each function used here with its signature.

    ctypes passes an argument it has been given no type for as a C ``int``,
    and takes the result as one. A window or process handle is the size of a
    pointer, and so are wParam and lParam. Declared, each travels at its own
    width.

    Measured on 64-bit Windows 11, Python 3.13, posting to a window of our
    own: undeclared, a number of 2**32 or more was refused before the call
    (``ArgumentError: int too long to convert``), which here would have read
    as "the key could not be posted"; anything smaller got through, the
    lParam of a released key with its bit 31 included. So the calls worked
    for as long as every handle happened to be small.

    The libraries are this module's own instances, so the signatures set
    here are not the ones :mod:`core.drag_win` sets on its.
    """

    def __init__(self) -> None:
        user32 = _win.WinDLL("user32", use_last_error=True)
        kernel32 = _win.WinDLL("kernel32", use_last_error=True)

        #: The callback ``EnumWindows`` calls once for each window.
        self.enum_proc = _win.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        user32.EnumWindows.argtypes = [self.enum_proc, wintypes.LPARAM]
        user32.EnumWindows.restype = wintypes.BOOL
        user32.IsWindowVisible.argtypes = [wintypes.HWND]
        user32.IsWindowVisible.restype = wintypes.BOOL
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        user32.MapVirtualKeyW.argtypes = [wintypes.UINT, wintypes.UINT]
        user32.MapVirtualKeyW.restype = wintypes.UINT
        user32.PostMessageW.argtypes = [
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        user32.PostMessageW.restype = wintypes.BOOL

        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
        ]
        kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL

        self.user32 = user32
        self.kernel32 = kernel32


#: Made on first use and kept: a key is two posts and a scan-code lookup, and
#: loading a library for each was three loads a key. Not made at import,
#: which happens on macOS too, where there is no user32 to load.
_win32: _Win32 | None = None


def _win32_api() -> _Win32:
    """The bound libraries. Raises :class:`OSError` or
    :class:`AttributeError` where they cannot be loaded."""
    global _win32
    if _win32 is None:
        _win32 = _Win32()
    return _win32


#: ``PROCESS_QUERY_LIMITED_INFORMATION`` — enough for the image name and,
#: unlike the full query right, granted across sessions.
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
#: ``MAX_PATH``: room for the path of the program a process runs.
_MAX_PATH = 260


def _image_name(api: _Win32, pid: int) -> str:
    """File name of the program process *pid* runs, lower case; "" if the
    process cannot be asked."""
    handle = api.kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(_MAX_PATH)
        buf = ctypes.create_unicode_buffer(size.value)
        if not api.kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return ""
        return buf.value.rsplit("\\", 1)[-1].lower()
    finally:
        api.kernel32.CloseHandle(handle)


def traktor_hwnd() -> int | None:
    """Handle of Traktor's main window on Windows, or None.

    Matched on the owning process's image name rather than the window title,
    which changes with the loaded track and is localised — by the rule in
    :func:`core.traktor_process.is_traktor_exe`. The first visible Traktor
    window in z-order, not the largest one :mod:`core.drag_win` picks.
    """
    if not _IS_WIN:
        return None
    try:
        api = _win32_api()
        found: list[int] = []

        def _each(hwnd, _lparam):
            if not api.user32.IsWindowVisible(hwnd):
                return True
            pid = wintypes.DWORD()
            api.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value and traktor_process.is_traktor_exe(_image_name(api, pid.value)):
                found.append(int(hwnd))
                return False  # stop at the first one
            return True

        api.user32.EnumWindows(api.enum_proc(_each), 0)
        return found[0] if found else None
    except Exception:
        log.debug("traktor_hwnd failed", exc_info=True)
        return None


def _win_lparam(virtual_key: int, down: bool) -> int:
    """The lParam a real WM_KEYDOWN/WM_KEYUP would carry.

    An application that only reads wParam does not care, but one that looks
    at the scan code — which is the layout-independent way to identify a key,
    and so the likely one for a DJ app — sees nonsense without this.
    """
    user32 = _win32_api().user32
    scan = user32.MapVirtualKeyW(virtual_key, _MAPVK_VK_TO_VSC) & 0xFF
    lparam = 1 | (scan << 16)  # repeat count 1
    if virtual_key in _EXTENDED_VKS:
        lparam |= _KF_EXTENDED
    if not down:
        # Bit 30 "key was previously down" and bit 31 "key is being released".
        lparam |= 0xC0000000
    return lparam


def _post_win(hwnd: int, virtual_key: int, qt_mods: int, down: bool) -> bool:
    """Post one key to Traktor's window. True if Windows accepted the post.

    True does **not** mean Traktor acted on it — ``PostMessage`` only puts the
    message on the queue.

    Modifiers are posted as their own key events around the main one, because
    a posted message carries no modifier state: the receiver would ask
    ``GetKeyState``, which reports *its* thread's view, not ours.
    """
    try:
        from PySide6.QtCore import Qt

        user32 = _win32_api().user32
        mods = [
            (_VK_SHIFT, Qt.KeyboardModifier.ShiftModifier),
            (_VK_CONTROL, Qt.KeyboardModifier.ControlModifier),
            (_VK_MENU, Qt.KeyboardModifier.AltModifier),
        ]
        active = [vk for vk, flag in mods if qt_mods & int(flag.value)]

        # Alt turns a key message into its "system" variant; an app that
        # switches on the message id would drop a plain WM_KEYDOWN.
        alt = _VK_MENU in active
        msg_down = _WM_SYSKEYDOWN if alt else _WM_KEYDOWN
        msg_up = _WM_SYSKEYUP if alt else _WM_KEYUP

        if down:
            for vk in active:
                user32.PostMessageW(hwnd, _WM_KEYDOWN, vk, _win_lparam(vk, True))
        ok = bool(
            user32.PostMessageW(
                hwnd, msg_down if down else msg_up, virtual_key, _win_lparam(virtual_key, down)
            )
        )
        if not down:
            for vk in reversed(active):
                user32.PostMessageW(hwnd, _WM_KEYUP, vk, _win_lparam(vk, False))
        return ok
    except Exception:
        log.debug("posting key %s (down=%s) to Traktor failed", virtual_key, down, exc_info=True)
        return False


def _cg_flags(qt_mods: int) -> int:
    from PySide6.QtCore import Qt

    flags = 0
    if qt_mods & int(Qt.KeyboardModifier.ShiftModifier.value):
        flags |= _CG_SHIFT
    if qt_mods & int(Qt.KeyboardModifier.ControlModifier.value):
        flags |= _CG_CTRL
    if qt_mods & int(Qt.KeyboardModifier.AltModifier.value):
        flags |= _CG_ALT
    if qt_mods & int(Qt.KeyboardModifier.MetaModifier.value):
        flags |= _CG_CMD
    return flags


#: Native codes currently held down in Traktor on our behalf. A press we
#: forwarded owns its release: Traktor must not be left holding a key because
#: the user switched windows mid-press, so this is what :func:`release_all`
#: frees.
_held: set[int] = set()


def held_keys() -> frozenset[int]:
    """Native codes Traktor currently has down because of us."""
    return frozenset(_held)


def is_held(virtual_key: int) -> bool:
    """True if we forwarded a press for this key and not yet its release."""
    return virtual_key in _held


def post_key(virtual_key: int, qt_mods: int = 0, *, down: bool, pid: int | None = None) -> bool:
    """Post one key-down or key-up to Traktor. True if it was posted.

    *virtual_key* is the platform's native code — on macOS the ``kVK_*``
    virtual key, which is what ``QKeyEvent.nativeVirtualKey()`` returns, so the
    physical key the user pressed is the physical key Traktor sees regardless
    of keyboard layout.

    The halves are posted separately, as they happen, so Traktor sees the key
    held for exactly as long as the user holds it. That matters twice over: a
    press and release in the same instant is ignored by Traktor's mapping
    (measured — 0 ms and 2 ms do nothing, 5 ms works), and a mapping that acts
    on hold needs the real duration.

    A release is only posted for a key whose press we forwarded, so a key
    Kommander consumed never pokes Traktor on the way up.

    Note that 0 is a *valid* code: ``kVK_ANSI_A`` is 0, so the key may not be
    rejected for being falsy — doing that silently swallowed the "A" key,
    confirmed against a running Traktor (S and W arrived in its search field,
    A never did).
    """
    if not supported() or virtual_key < 0:
        return False
    if not down and virtual_key not in _held:
        return False
    # No Traktor is the normal state when it is simply not running, so this
    # is not worth a line per keystroke; a real posting failure still logs.
    target = pid if pid is not None else _target()
    if not target:
        return False
    if _post(target, virtual_key, qt_mods, down):
        _held.add(virtual_key) if down else _held.discard(virtual_key)
        return True
    if pid is None and _IS_MAC:
        # The cached Traktor may be the reason; look it up afresh next key.
        _forget_traktor()
    return False


def _target() -> int | None:
    """What a key is addressed to: Traktor's pid on macOS, its window on
    Windows. Both are opaque ints to the caller."""
    return traktor_hwnd() if _IS_WIN else traktor_pid()


def release_all(pid: int | None = None) -> None:
    """Release every key we are still holding down in Traktor.

    Called when Kommander stops seeing the keyboard — window deactivated,
    app closing — because the release event for a key held across that moment
    never arrives, and a stuck key in Traktor is a stuck transport control.
    """
    if not _held:
        return
    target = pid if pid is not None else _target()
    for code in sorted(_held):
        if target:
            _post(target, code, 0, False)
    log.debug("released %d held key(s) in Traktor", len(_held))
    _held.clear()


def _post(target: int, virtual_key: int, qt_mods: int, down: bool) -> bool:
    """Post a single keyboard event. True if the OS accepted it.

    *target* is whatever :func:`_target` produced for this platform.
    """
    if _IS_WIN:
        return _post_win(target, virtual_key, qt_mods, down)
    try:
        Q = platform_modules.quartz()

        src = Q.CGEventSourceCreate(Q.kCGEventSourceStateHIDSystemState)
        ev = Q.CGEventCreateKeyboardEvent(src, virtual_key, down)
        flags = _cg_flags(qt_mods)
        if flags:
            Q.CGEventSetFlags(ev, flags)
        Q.CGEventPostToPid(target, ev)
        return True
    except Exception:
        log.debug("forwarding key %s (down=%s) to Traktor failed", virtual_key, down, exc_info=True)
        return False


def should_forward(event) -> bool:
    """Whether a key event is one we should hand to Traktor.

    Excluded:

    * bare modifier presses — Traktor tracks modifier state from the real
      keys, and posting them alone can leave it stuck;
    * anything with Cmd held, which belongs to the application menu
      (Cmd+F, Cmd+, Cmd+Q) and would be meaningless in Traktor;
    * auto-repeat, which would flood the event queue.
    """
    from PySide6.QtCore import Qt

    if event.isAutoRepeat():
        return False
    if event.modifiers() & Qt.KeyboardModifier.MetaModifier:
        return False
    return Qt.Key(event.key()) not in {
        Qt.Key.Key_Shift,
        Qt.Key.Key_Control,
        Qt.Key.Key_Alt,
        Qt.Key.Key_Meta,
        Qt.Key.Key_AltGr,
        Qt.Key.Key_CapsLock,
    }
