"""Forwarding keystrokes to Traktor on Windows.

macOS posts to a *process* with ``CGEventPostToPid`` and changes no focus.
Windows has nothing equivalent, so the key is posted to Traktor's window
instead. That keeps the semantics — no focus flicker, key held for as long
as the user holds it — at the cost that Traktor only sees it if it reads the
keyboard through its message queue.

Whether it does is the one thing these cannot answer. What they can pin is
everything that would be wrong even if it does: the message ids, the lParam
a real keystroke carries, and the modifier ordering.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes

import pytest

pytest.importorskip("PySide6.QtCore")

from PySide6.QtCore import Qt

import core.keyforward as kf

VK_A = 0x41
VK_LEFT = 0x25  # extended
VK_F1 = 0x70

#: ctypes type codes, by whether the C type has a sign.
_SIGNED, _UNSIGNED = "bhilq", "BHILQP"


def _fits(kind, value) -> bool:
    """Can *value* travel as a *kind* without losing anything?"""
    code = getattr(kind, "_type_", "")
    if not isinstance(value, int) or not isinstance(code, str) or not code:
        return True  # a pointer, a buffer, None: not a number
    bits = ctypes.sizeof(kind) * 8
    if code in _SIGNED:
        return -(1 << (bits - 1)) <= value < (1 << (bits - 1))
    if code in _UNSIGNED:
        return 0 <= value < (1 << bits)
    return True


class _Fn:
    """A function as ctypes hands one out: callable, and carrying whatever
    signature is declared on it.

    Stricter than the real thing, so that a mistake shows here and not on
    someone's machine: it will not be called before its signature is
    declared, and it will not take a number that does not fit the type
    declared for it. The real one passes what it has no type for as a C
    ``int`` and says nothing.
    """

    def __init__(self, name: str, call) -> None:
        self._name = name
        self._call = call
        self.argtypes = None
        self.restype = None

    def __call__(self, *args):
        if self.argtypes is None or self.restype is None:
            raise AssertionError(f"{self._name} called with no signature")
        if len(args) != len(self.argtypes):
            raise ctypes.ArgumentError(f"{self._name} takes {len(self.argtypes)} arguments")
        for position, (kind, value) in enumerate(zip(self.argtypes, args, strict=True), 1):
            if not _fits(kind, value):
                raise ctypes.ArgumentError(
                    f"{self._name}, argument {position}: {value:#x} does not fit a {kind.__name__}"
                )
        return self._call(*args)


class _FakeUser32:
    """Records PostMessageW calls; MapVirtualKeyW returns a known scan code;
    EnumWindows walks the windows put in :attr:`windows`."""

    SCAN = {
        VK_A: 0x1E,
        VK_LEFT: 0x4B,
        VK_F1: 0x3B,
        kf._VK_SHIFT: 0x2A,
        kf._VK_CONTROL: 0x1D,
        kf._VK_MENU: 0x38,
    }

    def __init__(self, accept: bool = True):
        self.posts: list[tuple[int, int, int, int]] = []
        self._accept = accept
        #: window handle → (owning pid, visible), in the order enumerated.
        self.windows: dict[int, tuple[int, bool]] = {}
        self.MapVirtualKeyW = _Fn("MapVirtualKeyW", lambda vk, _kind: self.SCAN.get(vk, 0))
        self.PostMessageW = _Fn("PostMessageW", self._post)
        self.EnumWindows = _Fn("EnumWindows", self._enumerate)
        self.IsWindowVisible = _Fn("IsWindowVisible", lambda hwnd: int(self.windows[hwnd][1]))
        self.GetWindowThreadProcessId = _Fn("GetWindowThreadProcessId", self._pid_of)

    def _post(self, hwnd, msg, wparam, lparam) -> int:
        self.posts.append((hwnd, msg, wparam, lparam))
        return 1 if self._accept else 0

    def _enumerate(self, callback, lparam) -> int:
        for hwnd in self.windows:
            if not callback(hwnd, lparam):
                return 0
        return 1

    def _pid_of(self, hwnd, pid_out) -> int:
        # *pid_out* is what ctypes.byref() made; the DWORD is behind _obj.
        pid_out._obj.value = self.windows[hwnd][0]
        return 1  # the thread id: unused


class _FakeKernel32:
    """Processes by pid, each with the path of its program."""

    #: Wider than 32 bits, as a handle may be: taken as an ``int`` it would
    #: come back as 0x0BEEF0 and be closed as that.
    FIRST_HANDLE = 0x1_0000_0BEE_F0

    def __init__(self) -> None:
        self.images: dict[int, str] = {}
        self.opened: dict[int, int] = {}  # handle → pid
        self.closed: list[int] = []
        self.OpenProcess = _Fn("OpenProcess", self._open)
        self.QueryFullProcessImageNameW = _Fn("QueryFullProcessImageNameW", self._image)
        self.CloseHandle = _Fn("CloseHandle", self._close)

    def _open(self, _access, _inherit, pid):
        if pid not in self.images:
            return None  # a NULL handle
        handle = self.FIRST_HANDLE + len(self.opened)
        self.opened[handle] = pid
        return handle

    def _image(self, handle, _flags, buffer, _size) -> int:
        buffer.value = self.images[self.opened[handle]]
        return 1

    def _close(self, handle) -> int:
        self.closed.append(handle)
        return 1


@pytest.fixture
def win(monkeypatch):
    """Pretend to be Windows, with a fake user32 and kernel32.

    Yields the user32; its ``kernel32`` and ``loads`` hold the other library
    and the name of every library asked for.
    """
    monkeypatch.setattr(kf, "_IS_WIN", True)
    monkeypatch.setattr(kf, "_IS_MAC", False)
    # Nothing bound by an earlier test, or on Windows by the real thing.
    monkeypatch.setattr(kf, "_win32", None)
    fake = _FakeUser32()
    fake.kernel32 = _FakeKernel32()
    fake.loads = []
    libraries = {"user32": fake, "kernel32": fake.kernel32}

    def load(name, **_kw):
        fake.loads.append(name)
        return libraries[name]

    monkeypatch.setattr("ctypes.WinDLL", load, raising=False)
    if not hasattr(ctypes, "WINFUNCTYPE"):
        # Off Windows there is one calling convention and ctypes has no
        # name for the other; the callback is built the same way.
        monkeypatch.setattr("ctypes.WINFUNCTYPE", ctypes.CFUNCTYPE, raising=False)
    kf._held.clear()
    yield fake
    kf._held.clear()


def _msgs(fake):
    return [(msg, wparam) for _hwnd, msg, wparam, _lp in fake.posts]


# ── Platform reporting ──────────────────────────────────────────────────────


def test_windows_counts_as_supported(win) -> None:
    assert kf.supported() is True


# ── The lParam a real keystroke carries ─────────────────────────────────────


def test_lparam_carries_the_scan_code(win) -> None:
    """An app identifying keys by scan code — the layout-independent way,
    so the likely way for a DJ app — sees nonsense without this."""
    lparam = kf._win_lparam(VK_A, down=True)
    assert (lparam >> 16) & 0xFF == 0x1E


def test_lparam_repeat_count_is_one(win) -> None:
    assert kf._win_lparam(VK_A, down=True) & 0xFFFF == 1


def test_an_extended_key_sets_bit_24(win) -> None:
    """Without it an app cannot tell Left-arrow from keypad-4."""
    assert kf._win_lparam(VK_LEFT, down=True) & kf._KF_EXTENDED
    assert not kf._win_lparam(VK_A, down=True) & kf._KF_EXTENDED


def test_a_release_sets_the_transition_bits(win) -> None:
    """Bits 30 and 31: previously down, now released."""
    up = kf._win_lparam(VK_A, down=False)
    assert up & 0x80000000, "key-up bit"
    assert up & 0x40000000, "previous-state bit"
    assert not kf._win_lparam(VK_A, down=True) & 0xC0000000


# ── What travels, and how wide ──────────────────────────────────────────────
# With no signature declared ctypes passes every argument as a C int and
# takes the result as one. A window handle, wParam and lParam are the size of
# a pointer, and the lParam of a release has bit 31 set.


def test_a_release_is_posted_with_bit_31_set(win) -> None:
    """0xC01E0001 for "A": more than a signed 32-bit int holds."""
    kf.post_key(VK_A, 0, down=True, pid=0x1234)
    win.posts.clear()

    assert kf.post_key(VK_A, 0, down=False, pid=0x1234) is True

    [(_hwnd, _msg, _wparam, lparam)] = win.posts
    assert lparam == 0xC01E0001


def test_a_window_handle_wider_than_32_bits_is_posted_to_whole(win) -> None:
    assert kf.post_key(VK_A, 0, down=True, pid=0x1_0000_1234) is True
    assert win.posts[0][0] == 0x1_0000_1234


def test_a_message_is_posted_with_each_part_at_its_own_width(win) -> None:
    kf.post_key(VK_A, 0, down=True, pid=0x1234)

    assert win.PostMessageW.argtypes == [
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    ]
    assert win.PostMessageW.restype is wintypes.BOOL
    assert win.MapVirtualKeyW.argtypes == [wintypes.UINT, wintypes.UINT]
    assert win.MapVirtualKeyW.restype is wintypes.UINT


def test_the_libraries_are_loaded_once(win) -> None:
    """Not once a key: every press is two posts and a scan-code lookup."""
    for _ in range(3):
        kf.post_key(VK_A, 0, down=True, pid=0x1234)
        kf.post_key(VK_A, 0, down=False, pid=0x1234)
    kf.traktor_hwnd()

    assert sorted(win.loads) == ["kernel32", "user32"]


# ── Messages ────────────────────────────────────────────────────────────────


def test_a_press_posts_keydown_to_the_window(win) -> None:
    assert kf.post_key(VK_A, 0, down=True, pid=0x1234) is True
    assert _msgs(win) == [(kf._WM_KEYDOWN, VK_A)]
    assert win.posts[0][0] == 0x1234, "addressed to Traktor's window"


def test_a_release_posts_keyup(win) -> None:
    kf.post_key(VK_A, 0, down=True, pid=0x1234)
    win.posts.clear()
    assert kf.post_key(VK_A, 0, down=False, pid=0x1234) is True
    assert _msgs(win) == [(kf._WM_KEYUP, VK_A)]


def test_alt_makes_it_a_system_key_message(win) -> None:
    """An app switching on the message id would drop a plain WM_KEYDOWN."""
    kf.post_key(VK_A, int(Qt.KeyboardModifier.AltModifier.value), down=True, pid=1)
    assert (kf._WM_SYSKEYDOWN, VK_A) in _msgs(win)


def test_a_modifier_is_pressed_before_the_key_and_released_after(win) -> None:
    """A posted message carries no modifier state — the receiver would ask
    GetKeyState, which reports its own thread's view, not ours."""
    mods = int(Qt.KeyboardModifier.ShiftModifier.value)
    kf.post_key(VK_A, mods, down=True, pid=1)
    assert _msgs(win) == [(kf._WM_KEYDOWN, kf._VK_SHIFT), (kf._WM_KEYDOWN, VK_A)]
    win.posts.clear()
    kf.post_key(VK_A, mods, down=False, pid=1)
    assert _msgs(win) == [(kf._WM_KEYUP, VK_A), (kf._WM_KEYUP, kf._VK_SHIFT)]


def test_several_modifiers_are_released_in_reverse(win) -> None:
    mods = int(Qt.KeyboardModifier.ShiftModifier.value) | int(
        Qt.KeyboardModifier.ControlModifier.value
    )
    kf.post_key(VK_A, mods, down=True, pid=1)
    down_order = [w for m, w in _msgs(win) if m == kf._WM_KEYDOWN]
    win.posts.clear()
    kf.post_key(VK_A, mods, down=False, pid=1)
    up_order = [w for m, w in _msgs(win) if m == kf._WM_KEYUP]
    assert down_order[:-1] == list(reversed(up_order[1:]))


# ── The held-key bookkeeping still holds ────────────────────────────────────


def test_a_release_without_a_press_is_not_posted(win) -> None:
    """A key Kommander consumed must never poke Traktor on the way up."""
    assert kf.post_key(VK_A, 0, down=False, pid=1) is False
    assert win.posts == []


def test_release_all_frees_what_is_still_held(win) -> None:
    kf.post_key(VK_A, 0, down=True, pid=1)
    kf.post_key(VK_F1, 0, down=True, pid=1)
    assert kf.held_keys() == frozenset({VK_A, VK_F1})
    win.posts.clear()

    kf.release_all(pid=1)
    assert kf.held_keys() == frozenset()
    assert sorted(w for m, w in _msgs(win) if m == kf._WM_KEYUP) == [VK_A, VK_F1]


def test_key_zero_is_valid_on_windows_too(win) -> None:
    """0 is not a Windows VK, but the guard must reject only negatives —
    rejecting falsy codes silently swallowed "A" on macOS once already."""
    assert kf.post_key(-1, 0, down=True, pid=1) is False
    assert win.posts == []


def test_no_target_means_nothing_is_posted(win, monkeypatch) -> None:
    monkeypatch.setattr(kf, "traktor_hwnd", lambda: None)
    assert kf.post_key(VK_A, 0, down=True) is False
    assert win.posts == []
    assert kf.held_keys() == frozenset()


def test_a_refused_post_does_not_mark_the_key_held(win, monkeypatch) -> None:
    """If Windows rejects the post, Traktor is not holding the key, so
    release_all must not later send a stray key-up for it."""
    monkeypatch.setattr(win, "_accept", False)
    assert kf.post_key(VK_A, 0, down=True, pid=1) is False
    assert kf.held_keys() == frozenset()


# ── Finding Traktor's window ────────────────────────────────────────────────


def test_traktor_hwnd_is_none_off_windows(monkeypatch) -> None:
    monkeypatch.setattr(kf, "_IS_WIN", False)
    assert kf.traktor_hwnd() is None


_TRAKTOR_EXE = r"C:\Program Files\Native Instruments\Traktor Pro 4\Traktor Pro 4.exe"
_EXPLORER_EXE = r"C:\Windows\explorer.exe"


def test_traktors_window_is_the_one_its_program_owns(win) -> None:
    win.windows = {0x100: (11, True), 0x200: (22, True), 0x300: (22, True)}
    win.kernel32.images = {11: _EXPLORER_EXE, 22: _TRAKTOR_EXE}

    assert kf.traktor_hwnd() == 0x200, "the first of Traktor's, and no other"


def test_a_window_that_is_not_shown_is_not_the_one(win) -> None:
    win.windows = {0x100: (22, False), 0x200: (22, True)}
    win.kernel32.images = {22: _TRAKTOR_EXE}

    assert kf.traktor_hwnd() == 0x200


def test_without_traktor_there_is_no_window(win) -> None:
    win.windows = {0x100: (11, True)}
    win.kernel32.images = {11: _EXPLORER_EXE}

    assert kf.traktor_hwnd() is None


def test_a_process_that_cannot_be_opened_is_passed_over(win) -> None:
    """A service, or one of another user's: no handle, and nothing to close."""
    win.windows = {0x100: (99, True), 0x200: (22, True)}
    win.kernel32.images = {22: _TRAKTOR_EXE}

    assert kf.traktor_hwnd() == 0x200
    assert None not in win.kernel32.closed


def test_a_process_handle_is_closed_as_the_handle_it_was(win) -> None:
    """A handle is the size of a pointer. Taken as an int, the one closed is
    not the one that was opened, and the real one is never given back."""
    win.windows = {0x100: (11, True), 0x200: (22, True)}
    win.kernel32.images = {11: _EXPLORER_EXE, 22: _TRAKTOR_EXE}

    kf.traktor_hwnd()

    assert win.kernel32.closed == list(win.kernel32.opened)
    assert all(handle > 0xFFFFFFFF for handle in win.kernel32.closed)
    assert win.kernel32.OpenProcess.restype is wintypes.HANDLE
    assert win.kernel32.CloseHandle.argtypes == [wintypes.HANDLE]


def test_traktor_hwnd_survives_a_broken_api(win, monkeypatch) -> None:
    """Never raise into the key handler — a missing window is normal."""

    def _boom(*a, **k):
        raise OSError("no user32")

    monkeypatch.setattr("ctypes.WinDLL", _boom, raising=False)
    assert kf.traktor_hwnd() is None


def test_the_target_is_the_window_on_windows(win, monkeypatch) -> None:
    monkeypatch.setattr(kf, "traktor_hwnd", lambda: 0xBEEF)
    monkeypatch.setattr(kf, "traktor_pid", lambda: 999)
    assert kf._target() == 0xBEEF


def test_the_target_is_the_pid_on_macos(monkeypatch) -> None:
    monkeypatch.setattr(kf, "_IS_WIN", False)
    monkeypatch.setattr(kf, "_IS_MAC", True)
    monkeypatch.setattr(kf, "traktor_pid", lambda: 999)
    assert kf._target() == 999
