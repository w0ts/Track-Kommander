"""The one rule for "is this Traktor", and the lookups built on it.

The platform layer (AppKit, Quartz, ``pgrep``/``tasklist``, the Win32
window code) is faked: these tests pin the *matching*, not the OS calls.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import types

import pytest

from core import traktor_process as tp

# ── the rule ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("name", ["Traktor Pro 4", "Traktor Pro 3", "Traktor"])
def test_every_known_traktor_name_matches(name) -> None:
    assert tp.is_traktor_app_name(name)


@pytest.mark.parametrize(
    "name",
    [
        None,
        "",
        "traktor pro 4",
        "Traktor Pro 4 Helper",
        "Track Kommander",
        "Traktor Kontrol S4",
        "Finder",
    ],
)
def test_anything_else_is_not_traktor(name) -> None:
    """Exact and case-sensitive: the name AppKit and the window list report."""
    assert not tp.is_traktor_app_name(name)


@pytest.mark.parametrize(
    "path",
    [
        r"C:\Program Files\Native Instruments\Traktor Pro 4\Traktor Pro 4.exe",
        r"C:\Program Files\Native Instruments\Traktor Pro 3\Traktor Pro 3.exe",
        "Traktor Pro 4.exe",
        "traktor pro 4.exe",
    ],
)
def test_the_traktor_executable_matches(path) -> None:
    assert tp.is_traktor_exe(path)


@pytest.mark.parametrize(
    "path",
    [
        None,
        "",
        # In Traktor's folder, but not Traktor: the full-path match took these.
        r"C:\Program Files\Native Instruments\Traktor Pro 4\crashpad_handler.exe",
        # Starts with "traktor", which keyforward's rule took.
        r"C:\Program Files\Native Instruments\Traktor Kontrol S4 MK3\Traktor Kontrol.exe",
        r"C:\Program Files\Track Kommander\Track Kommander.exe",
        r"C:\Windows\explorer.exe",
    ],
)
def test_other_executables_are_not_traktor(path) -> None:
    assert not tp.is_traktor_exe(path)


# ── is it running ───────────────────────────────────────────────────────────

TASKLIST = (
    '"Traktor Pro 4.exe","4242","Console","1","512 K"\n'
    '"Traktor Pro 4.exe","4243","Console","1","8 K"\n'
    '"Track Kommander.exe","99","Console","1","64 K"\n'
)


def _fake_run(stdout: str, returncode: int = 0, seen: list | None = None):
    def run(argv, **kwargs):
        if seen is not None:
            seen.append(list(argv))
        return subprocess.CompletedProcess(argv, returncode, stdout, "")

    return run


def test_windows_image_names_are_read_once_each(monkeypatch) -> None:
    monkeypatch.setattr(subprocess, "run", _fake_run(TASKLIST))
    assert tp.windows_images() == ["Traktor Pro 4.exe"]


def test_windows_running_follows_the_image_names(monkeypatch) -> None:
    monkeypatch.setattr(tp, "_IS_WIN", True)
    monkeypatch.setattr(subprocess, "run", _fake_run(TASKLIST))
    assert tp.running() is True
    monkeypatch.setattr(subprocess, "run", _fake_run(TASKLIST.splitlines()[2]))
    assert tp.running() is False


def test_macos_running_asks_pgrep_for_the_known_names(monkeypatch) -> None:
    seen: list = []
    monkeypatch.setattr(tp, "_IS_WIN", False)
    monkeypatch.setattr(subprocess, "run", _fake_run("123\n", seen=seen))
    assert tp.running() is True
    pattern = seen[0][-1]
    for name in tp.APP_NAMES:
        assert name in pattern
    assert pattern.endswith("$"), "anchored, so crashpad's argv cannot match"


def test_pgrep_without_a_match_is_not_running(monkeypatch) -> None:
    monkeypatch.setattr(tp, "_IS_WIN", False)
    monkeypatch.setattr(subprocess, "run", _fake_run("", returncode=1))
    assert tp.running() is False


def test_a_missing_tool_reads_as_not_running(monkeypatch) -> None:
    def boom(*a, **k):
        raise FileNotFoundError("pgrep")

    monkeypatch.setattr(tp, "_IS_WIN", False)
    monkeypatch.setattr(subprocess, "run", boom)
    assert tp.running() is False


def test_the_pgrep_pattern_matches_the_bundle_executable() -> None:
    rx = re.compile(tp.PGREP_PATTERN)
    assert rx.search(
        "/Applications/Native Instruments/Traktor Pro 4/"
        "Traktor Pro 4.app/Contents/MacOS/Traktor Pro 4"
    )
    assert rx.search("/Applications/Traktor.app/Contents/MacOS/Traktor")
    assert not rx.search(
        "/Applications/Traktor Pro 4.app/Contents/MacOS/Traktor Pro 4 --crashpad-handler"
    )
    assert not rx.search("/Applications/Track Kommander.app/Contents/MacOS/Track Kommander")


# ── macOS apps ──────────────────────────────────────────────────────────────


class _App:
    def __init__(self, name: str, pid: int) -> None:
        self._name, self._pid = name, pid

    def localizedName(self):
        return self._name

    def processIdentifier(self):
        return self._pid

    def bundleIdentifier(self):
        return "com.example." + self._name.replace(" ", "")


def _fake_appkit(monkeypatch, apps, front=None) -> None:
    workspace = types.SimpleNamespace(
        runningApplications=lambda: apps,
        frontmostApplication=lambda: front,
    )
    appkit = types.ModuleType("AppKit")
    appkit.NSWorkspace = types.SimpleNamespace(sharedWorkspace=lambda: workspace)
    monkeypatch.setitem(sys.modules, "AppKit", appkit)


def test_running_apps_keeps_only_traktor(monkeypatch) -> None:
    apps = [_App("Finder", 1), _App("Traktor Pro 4", 2), _App("Traktor Pro 3", 3)]
    _fake_appkit(monkeypatch, apps)
    assert [a.processIdentifier() for a in tp.running_apps()] == [2, 3]


def test_first_app_gives_pid_and_app(monkeypatch) -> None:
    traktor = _App("Traktor Pro 3", 42)
    _fake_appkit(monkeypatch, [_App("Finder", 1), traktor])
    assert tp.first_app() == (42, traktor)


def test_first_app_is_none_without_traktor(monkeypatch) -> None:
    _fake_appkit(monkeypatch, [_App("Finder", 1)])
    assert tp.first_app() is None


# ── which app is in front ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("front", "want"),
    [
        (_App("Traktor Pro 4", 5), "traktor"),
        (_App("Track Kommander", os.getpid()), "us"),
        (_App("Safari", 6), "other"),
        (None, "other"),
    ],
)
def test_front_app_on_macos(monkeypatch, front, want) -> None:
    monkeypatch.setattr(tp, "_IS_WIN", False)
    monkeypatch.setattr(tp, "_IS_MAC", True)
    _fake_appkit(monkeypatch, [], front=front)
    assert tp.front_app() == want


def test_a_broken_front_check_reads_as_other(monkeypatch) -> None:
    monkeypatch.setattr(tp, "_IS_WIN", False)
    monkeypatch.setattr(tp, "_IS_MAC", True)
    monkeypatch.setitem(sys.modules, "AppKit", None)  # import fails
    assert tp.front_app() == "other"


def test_front_app_name_is_for_the_log(monkeypatch) -> None:
    monkeypatch.setattr(tp, "_IS_MAC", True)
    _fake_appkit(monkeypatch, [], front=_App("Traktor Pro 4", 7))
    assert tp.front_app_name() == "Traktor Pro 4 (com.example.TraktorPro4, pid 7)"


# ── windows (Quartz list on macOS) ──────────────────────────────────────────


def _win(owner, x, y, w, h, number=0, layer=0):
    return {
        "kCGWindowOwnerName": owner,
        "kCGWindowNumber": number,
        "kCGWindowLayer": layer,
        "kCGWindowBounds": {"X": x, "Y": y, "Width": w, "Height": h},
    }


WINDOWS = [
    _win("Track Kommander", 0, 0, 3000, 3000, number=1),
    _win("Traktor Pro 4", 10, 20, 300, 200, number=2),  # a small aux window
    _win("Traktor Pro 4", 0, 25, 1600, 1000, number=3),  # the main one
    _win("traktor pro 4", 0, 0, 5000, 5000, number=4),  # wrong case
]


def test_the_largest_traktor_window_is_the_main_one() -> None:
    assert tp.mac_window_bounds(WINDOWS) == (0.0, 25.0, 1600.0, 1000.0)
    assert tp.mac_window_id(WINDOWS) == 3


def test_no_traktor_window_means_none() -> None:
    assert tp.mac_window_bounds(WINDOWS[:1]) is None
    assert tp.mac_window_id(WINDOWS[:1]) is None


def test_window_bounds_on_macos_reads_the_onscreen_list(monkeypatch) -> None:
    monkeypatch.setattr(tp, "_IS_WIN", False)
    monkeypatch.setattr(tp, "mac_windows", lambda: WINDOWS)
    assert tp.window_bounds() == (0.0, 25.0, 1600.0, 1000.0)


def test_window_bounds_on_windows_asks_the_win32_backend(monkeypatch) -> None:
    fake = types.ModuleType("core.drag_win")
    fake.traktor_window = lambda: (0xAB, (1.0, 2.0, 3.0, 4.0))
    monkeypatch.setitem(sys.modules, "core.drag_win", fake)
    monkeypatch.setattr(tp, "_IS_WIN", True)
    assert tp.window_bounds() == (1.0, 2.0, 3.0, 4.0)
    fake.traktor_window = lambda: None
    assert tp.window_bounds() is None


def test_front_app_on_windows(monkeypatch) -> None:
    fake = types.ModuleType("core.drag_win")
    fake.is_traktor_hwnd = lambda hwnd: hwnd == 0x10
    monkeypatch.setitem(sys.modules, "core.drag_win", fake)
    monkeypatch.setattr(tp, "_IS_WIN", True)
    for hwnd, pid, want in [
        (0x10, 1, "traktor"),
        (0x20, 2, "other"),
        (0x30, os.getpid(), "us"),
        (0, 0, "other"),
    ]:
        monkeypatch.setattr(tp, "_foreground_win", lambda h=hwnd, p=pid: (h, p))
        assert tp.front_app() == want
