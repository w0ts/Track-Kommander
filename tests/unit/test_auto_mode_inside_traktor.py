"""The AUTO button inside Traktor: each click moves to the next of the four modes, as in
standalone TK, and shows them in a panel with that one ticked, where a click picks a mode
(User, 2026-11-25: "the big popup was nice... if we can have it AND the button to work").
Also Auto DJ's timing.

A click must not cut a running fade (2026-11-24: three quick clicks took Auto DJ to AutoMix
and cut deck A's fade-in): only Off cancels it.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QApplication

from ui.auto_mode_popup import AutoModePopup
from ui.np_auto import _AutoModeMixin
from ui.np_constants import _AUTO_MODES


@pytest.fixture(scope="module")
def app():
    yield QApplication.instance() or QApplication([])


def test_the_panel_lists_the_four_modes_and_ticks_the_current_one(app) -> None:
    popup = AutoModePopup(lambda _key: None)
    popup.show_mode(QPoint(100, 100), "automix")
    assert popup.mode_keys() == [m[0] for m in _AUTO_MODES]
    assert popup.checked_key() == "automix" and popup.isVisible() and popup.hides_soon()
    popup.close()


def test_the_next_click_ticks_the_next_mode_where_the_panel_is(app) -> None:
    popup = AutoModePopup(lambda _key: None)
    popup.show_mode(QPoint(100, 100), "off")
    where = popup.pos()
    popup.show_mode(QPoint(400, 300), "play_cue")  # open: ticked in place, not moved, not closed
    assert popup.isVisible() and popup.checked_key() == "play_cue" and popup.pos() == where
    popup.close()
    assert not popup.hides_soon()


def test_a_mode_clicked_in_the_panel_is_passed_on_and_closes_it(app) -> None:
    chosen: list[str] = []
    popup = AutoModePopup(chosen.append)
    popup.show_mode(QPoint(100, 100), "off")
    popup.choose("auto")
    assert chosen == ["auto"]
    assert not popup.isVisible()


class _Signal:
    def __init__(self) -> None:
        self.count = 0

    def emit(self, *_a) -> None:
        self.count += 1


class _Timer:
    def __init__(self) -> None:
        self.running = False

    def start(self, *_a) -> None:
        self.running = True

    def stop(self) -> None:
        self.running = False


class _Tab(_AutoModeMixin):
    """Just what _set_auto_mode touches."""

    def __init__(self, mode: str) -> None:
        self._auto_mode_idx = [m[0] for m in _AUTO_MODES].index(mode)
        self._settings = None
        self.cancel_automix_requested = _Signal()
        self._auto_timer = _Timer()
        self._auto_unlock_timer = _Timer()
        self._auto_loading = False
        self.applied: list[str] = []

    def _apply_auto_mode(self, show_tooltip: bool = False) -> None:
        self.applied.append(_AUTO_MODES[self._auto_mode_idx][0])


class _Pref:
    def __init__(self) -> None:
        self.saved: list[str] = []

    def set(self, value: str, _settings=None) -> None:
        self.saved.append(value)


class _AutoDJ(_AutoModeMixin):
    """Deck A on air, 300 s long; just what _auto_mode_tick reads."""

    def __init__(self, progress: float | None, started_s_ago: float) -> None:
        import time

        self._auto_mode_idx = [m[0] for m in _AUTO_MODES].index("auto")
        self._auto_loading = False
        self._playing_decks = {"A"}
        self._deck_volume = {"A": 1.0, "B": 1.0}
        self._deck_info = {"A": {"track_length": 300.0, "bpm": 120.0, "file_path": "/m/a.mp3"}}
        self._deck_progress = {} if progress is None else {"A": progress}
        self._deck_play_start = {"A": time.time() - started_s_ago}
        self._master_bpm = 120.0
        self._settings = None
        self._auto_timer = _Timer()
        self._auto_unlock_timer = _Timer()
        self._filtered_results = [{"row": {"path": "/m/next.mp3"}}]
        self._session_played: set[str] = set()
        self.loads: list[tuple[str, str]] = []

    def _update_auto_btn_state(self) -> None:
        pass

    def _load_to_deck(self, path: str, deck: str) -> None:
        self.loads.append((path, deck))


def test_auto_dj_mixes_by_the_playhead_not_by_when_play_started(monkeypatch) -> None:
    monkeypatch.setattr("ui.np_auto.RAMP_BARS", _Bars())
    near_end = _AutoDJ(progress=0.95, started_s_ago=5.0)  # started late in the track
    near_end._auto_mode_tick()
    assert near_end.loads == [("/m/next.mp3", "B")]
    early = _AutoDJ(progress=0.10, started_s_ago=290.0)  # rewound: most of the track left
    early._auto_mode_tick()
    assert early.loads == []


def test_auto_dj_mixes_at_the_last_hotcue(monkeypatch) -> None:
    monkeypatch.setattr("ui.np_auto.RAMP_BARS", _Bars())
    before = _AutoDJ(progress=200.0 / 300.0, started_s_ago=5.0)
    before._deck_info["A"]["last_hotcue_s"] = 240.0
    before._auto_mode_tick()
    assert before.loads == []  # 40 s before the cue
    at_cue = _AutoDJ(progress=238.0 / 300.0, started_s_ago=5.0)
    at_cue._deck_info["A"]["last_hotcue_s"] = 240.0
    at_cue._auto_mode_tick()
    assert at_cue.loads == [("/m/next.mp3", "B")]  # the load's lead time before the cue


def test_without_a_playhead_auto_dj_counts_from_the_play_start(monkeypatch) -> None:
    monkeypatch.setattr("ui.np_auto.RAMP_BARS", _Bars())
    deck = _AutoDJ(progress=None, started_s_ago=290.0)
    deck._auto_mode_tick()
    assert deck.loads == [("/m/next.mp3", "B")]


class _Bars:
    def get(self, *_a) -> int:
        return 4


def test_choosing_a_mode_keeps_a_running_fade_unless_it_is_off(monkeypatch) -> None:
    pref = _Pref()
    monkeypatch.setattr("ui.np_auto.AUTOMATION_MODE", pref)
    tab = _Tab("automix")
    tab._set_auto_mode("auto")
    assert (pref.saved, tab.applied, tab.cancel_automix_requested.count) == (["auto"], ["auto"], 0)
    tab._set_auto_mode("off")
    assert tab.cancel_automix_requested.count == 1
    tab._set_auto_mode("nonsense")
    assert pref.saved == ["auto", "off"]  # an unknown key changes nothing


def test_a_click_inside_traktor_moves_to_the_next_mode_and_keeps_the_fade(monkeypatch) -> None:
    pref = _Pref()
    monkeypatch.setattr("ui.np_auto.AUTOMATION_MODE", pref)
    keys = [m[0] for m in _AUTO_MODES]
    tab = _Tab(keys[1])
    tab._next_auto_mode()
    assert tab.applied == [keys[2]] and tab.cancel_automix_requested.count == 0
    tab._auto_mode_idx = len(keys) - 1
    tab._next_auto_mode()  # after the last: back to the first (Off), which stops the fade
    assert tab.applied[-1] == keys[0] == "off" and tab.cancel_automix_requested.count == 1
