"""Automation inside Traktor (User, 2026-11-24: "I could not get any of the automation to work").

- TK's own loads (Auto DJ, a load from TK) go to Traktor by path, never as a synthesized drag
  from TK's hidden table, and never ask for Accessibility.
- Every load Traktor reports gets the automation mode, except TK's own and the decks' state at
  start.
"""

from __future__ import annotations

import time

import pytest
from PySide6.QtCore import QCoreApplication

from ui.mw_native import NativeBandMixin
from ui.np_actions import _ActionsMixin
from workers.native_deck_control import NativeDeckControl

INSIDE = "/repo/traktor-native/re/embed_tk.py"


@pytest.fixture(scope="module")
def app():
    yield QCoreApplication.instance() or QCoreApplication([])


class _Emitted:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def emit(self, *args) -> None:
        self.calls.append(args)


class _Page(_ActionsMixin):
    """Just what _load_to_deck reads."""

    def __init__(self) -> None:
        self._playing_decks: set[str] = set()
        self._deck_volume: dict[str, float] = {}
        self.load_to_deck_requested = _Emitted()

    def _transpose_for(self, path: str) -> int:
        return 0


def test_inside_traktor_a_tk_load_goes_by_path_without_a_drag_or_a_permission(monkeypatch) -> None:
    monkeypatch.setenv("TK_EMBED", INSIDE)
    monkeypatch.setattr("core.permissions.accessibility_granted", lambda: False)

    def no_drag(*_a, **_k):
        raise AssertionError("a synthesized drag inside Traktor")

    monkeypatch.setattr("core.drag.drag_to_deck", no_drag)
    page = _Page()
    page._load_to_deck("/m/next.mp3", "B")
    assert page.load_to_deck_requested.calls == [("/m/next.mp3", "B", False, 0)]


class _FakeBus:
    def __init__(self) -> None:
        self.values: dict = {}
        self.loads: list = []

    def set(self, path: str, value) -> None:
        self.values[path] = value

    def get(self, path: str):
        return self.values.get(path)

    def load_path(self, deck_index: int, path: str) -> bool:
        self.loads.append((deck_index, path))
        return True


def test_automix_with_no_other_deck_playing_gives_the_bass_back(app) -> None:
    bus = _FakeBus()
    control = NativeDeckControl(bus, lambda: None)
    control.execute({"action": "load", "deck": "A", "path": "/m/a.mp3", "automix": True})
    assert bus.values["app.traktor.mixer.channels.1.eq.low"] == 0.0  # killed for the fade-in
    control._automix.start(0, 1)
    control._automix._done = control._automix._steps - 1
    control._automix._step()  # the last step of phase 1: nothing to fade out
    assert bus.values["app.traktor.mixer.channels.1.volume"] == 1.0
    assert bus.values["app.traktor.mixer.channels.1.eq.low"] == 0.5
    assert not control._automix.active()


def test_tks_own_load_is_recognised_and_a_traktor_load_is_not(app) -> None:
    control = NativeDeckControl(_FakeBus(), lambda: None)
    control.execute({"action": "load", "deck": "A", "path": "/m/a.mp3", "play": False})
    assert control.loaded_by_tk("A", "/m/a.mp3")
    assert not control.loaded_by_tk("A", "/m/other.mp3")
    assert not control.loaded_by_tk("B", "/m/a.mp3")


class _Mode:
    """Stands in for core.prefs.AUTOMATION_MODE (a frozen Pref)."""

    def __init__(self, value: str) -> None:
        self.value = value

    def get(self, *_a, **_k) -> str:
        return self.value


class _Watcher:
    def __init__(self) -> None:
        self.loads: list[tuple] = []

    def queue_load(self, path: str, deck: str, skip_load: bool = False) -> None:
        self.loads.append((path, deck, skip_load))


class _Window(NativeBandMixin):
    def __init__(self, control) -> None:
        self._native_decks = (None, None, control)
        self._watcher = _Watcher()
        self._native_loads_from = 0.0


@pytest.mark.parametrize("mode", ["play_cue", "automix", "auto"])
def test_a_load_in_traktor_gets_the_mode(app, monkeypatch, mode) -> None:
    monkeypatch.setattr("core.prefs.AUTOMATION_MODE", _Mode(mode))
    win = _Window(NativeDeckControl(_FakeBus(), lambda: None))
    win._on_native_deck_loaded("C", {"file_path": "/m/c.mp3"})
    assert win._watcher.loads == [("/m/c.mp3", "C", True)]  # loaded already: the mode only


def test_traktors_restore_of_the_last_session_is_not_a_load(monkeypatch) -> None:
    """Traktor puts the last tracks back ~15 s after it starts: no mode then (it auto-played)."""
    from ui import mw_native

    monkeypatch.setattr(mw_native, "process_age_s", lambda _pid: 10.0)  # Traktor started 10 s ago
    window = mw_native.loads_count_from() - time.monotonic()
    assert mw_native.RESTORE_S - 10.0 - 1.0 < window <= mw_native.RESTORE_S - 10.0
    monkeypatch.setattr(mw_native, "process_age_s", lambda _pid: 3600.0)  # TK reconnected later
    assert mw_native.loads_count_from() - time.monotonic() <= mw_native.STARTUP_LOADS_S
    monkeypatch.setattr(mw_native, "process_age_s", lambda _pid: None)  # unknown: the short window
    assert mw_native.loads_count_from() - time.monotonic() <= mw_native.STARTUP_LOADS_S


def test_off_start_state_and_tks_own_loads_are_left_alone(app, monkeypatch) -> None:
    control = NativeDeckControl(_FakeBus(), lambda: None)
    win = _Window(control)
    monkeypatch.setattr("core.prefs.AUTOMATION_MODE", _Mode("off"))
    win._on_native_deck_loaded("A", {"file_path": "/m/a.mp3"})
    monkeypatch.setattr("core.prefs.AUTOMATION_MODE", _Mode("play_cue"))
    win._native_loads_from = time.monotonic() + 60  # the decks' state at start
    win._on_native_deck_loaded("A", {"file_path": "/m/a.mp3"})
    win._native_loads_from = 0.0
    control.execute({"action": "load", "deck": "B", "path": "/m/b.mp3", "play": False})
    win._on_native_deck_loaded("B", {"file_path": "/m/b.mp3"})  # TK's own: mode already applied
    assert win._watcher.loads == []
