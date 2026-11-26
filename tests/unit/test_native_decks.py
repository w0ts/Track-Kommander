"""Deck state and deck commands by direct calls into Traktor (integrated mode).

A fake property bus stands in for libtk: a dict of Traktor's property paths, the
callbacks registered on them, and the loads asked for.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator

import pytest
from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from workers.native_deck_control import NativeDeckControl, deck_index, ramp_steps
from workers.native_deck_state import NativeDeckState, on_air_level
from workers.native_props import NativeProps
from workers.webhook import WebhookWatcher
from workers.webhook_posts import handle_post, key_adjust_value


@pytest.fixture(scope="module")
def app() -> Iterator[QApplication]:
    yield QApplication.instance() or QApplication([])


class FakeBus:
    def __init__(self, values: dict | None = None) -> None:
        self.values: dict = dict(values or {})
        self.watchers: dict[str, list[Callable[[], None]]] = {}
        self.sets: list[tuple[str, object]] = []
        self.loads: list[tuple[int, str]] = []

    def get(self, path: str):
        return self.values.get(path)

    def set(self, path: str, value) -> bool:
        self.values[path] = value
        self.sets.append((path, value))
        return True

    def watch(self, path: str, fn: Callable[[], None]) -> bool:
        self.watchers.setdefault(path, []).append(fn)
        return True

    def load_path(self, deck_index: int, path: str) -> bool:
        self.loads.append((deck_index, path))
        return True

    def change(self, path: str, value) -> None:
        """Traktor changes a property: the value moves, then the callbacks run."""
        self.values[path] = value
        for fn in self.watchers.get(path, ()):
            fn()


class Recorder:
    def __init__(self, watcher: WebhookWatcher) -> None:
        self.events: list[tuple] = []
        for name in (
            "deck_loaded",
            "deck_state",
            "deck_on_air",
            "deck_tempo",
            "deck_key_adjust",
            "master_tempo",
            "deck_position",
            "deck_phase",
        ):
            getattr(watcher, name).connect(lambda *a, n=name: self.events.append((n, *a)))

    def of(self, name: str) -> list[tuple]:
        return [e[1:] for e in self.events if e[0] == name]


def wait(ms: int) -> None:
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


D1 = "app.traktor.decks.1."


def started(values: dict | None = None):
    bus = FakeBus(values)
    watcher = WebhookWatcher(port=0)
    rec = Recorder(watcher)
    state = NativeDeckState(bus, watcher)
    state.start()
    return bus, watcher, rec, state


# ── state ────────────────────────────────────────────────────────────────────


def test_a_load_reports_the_track_from_traktors_properties(app) -> None:
    bus, _w, rec, state = started()
    bus.values.update(
        {
            D1 + "content.title": "Song",
            D1 + "content.artist": "Artist",
            D1 + "tempo.base_bpm": 124.0,
            D1 + "content.musical_key": "8A",
            D1 + "track.content.track_length": 300.0,
        }
    )
    bus.change(D1 + "track.content.file_path", "/music/song.mp3")
    wait(120)  # the load settles first
    loaded = rec.of("deck_loaded")
    assert loaded and loaded[-1][0] == "A"
    info = loaded[-1][1]
    assert info["file_path"] == "/music/song.mp3"
    assert info["title"] == "Song" and info["bpm"] == 124.0 and info["key"] == "8A"
    assert info["last_hotcue_s"] is None  # no hotcues
    state.stop()


def test_a_load_carries_the_tracks_last_hotcue(app) -> None:
    bus, _w, rec, state = started()
    cue = D1 + "track.cue.hotcues."
    bus.values.update(
        {
            D1 + "track.content.track_length": 300.0,
            cue + "1.exists": True,
            cue + "1.type": 4,  # the grid marker
            cue + "1.start_pos": 0.1,
            cue + "2.exists": True,
            cue + "2.type": 0,
            cue + "2.start_pos": 250.0,
            cue + "3.exists": True,
            cue + "3.type": 0,
            cue + "3.start_pos": 120.0,
            cue + "4.exists": False,  # an empty slot keeps a stale position
            cue + "4.type": -1,
            cue + "4.start_pos": 290.0,
        }
    )
    bus.change(D1 + "track.content.file_path", "/music/song.mp3")
    wait(120)
    assert rec.of("deck_loaded")[-1][1]["last_hotcue_s"] == 250.0
    state.stop()


def test_the_same_track_loaded_again_is_reported_again(app) -> None:
    bus, _w, rec, state = started({D1 + "track.content.file_path": "/music/a.mp3"})
    state._loaded_at["A"] -= 10  # the first load was a while ago
    before = len(rec.of("deck_loaded"))
    for _ in range(3):  # Traktor pulses it several times per load
        bus.change(D1 + "is_loaded_signal", True)
    wait(120)
    assert len(rec.of("deck_loaded")) == before + 1
    state.stop()


def test_a_late_load_pulse_for_the_same_track_is_not_a_second_load(app) -> None:
    bus, _w, rec, state = started()
    bus.change(D1 + "track.content.file_path", "/music/b.mp3")
    wait(120)
    bus.change(D1 + "is_loaded_signal", True)  # Traktor's pulse, ~1 s after the path
    wait(120)
    assert [e for e in rec.of("deck_loaded") if e[0] == "A"] != []
    assert len([e for e in rec.of("deck_loaded") if e[0] == "A"]) == 1
    state.stop()


def test_play_is_reported_once_per_change(app) -> None:
    bus, _w, rec, state = started()
    bus.change(D1 + "play", True)
    bus.change(D1 + "play", True)
    bus.change(D1 + "play", False)
    assert [e for e in rec.of("deck_state") if e[0] == "A"][-2:] == [("A", True), ("A", False)]
    state.stop()


def test_a_tempo_change_arrives_at_once_and_jitter_does_not(app) -> None:
    bus, _w, rec, state = started()
    bus.change(D1 + "tempo.true_bpm", 126.0)
    bus.change(D1 + "tempo.true_bpm", 126.001)
    bus.change(D1 + "tempo.true_bpm", 127.5)
    assert [e[1] for e in rec.of("deck_tempo") if e[0] == "A"] == [126.0, 127.5]
    state.stop()


def test_key_knob_is_reported_in_semitones(app) -> None:
    bus, _w, rec, state = started()
    bus.change(D1 + "track.key.adjust", key_adjust_value(-2))
    assert rec.of("deck_key_adjust")[-1] == ("A", -2)
    state.stop()


def test_crossfader_cuts_the_on_air_level() -> None:
    assert on_air_level(1.0, False, False, 1.0) == 1.0  # not assigned: the fader alone
    assert on_air_level(1.0, True, False, 1.0) == 0.0  # left channel, xfader hard right
    assert on_air_level(0.8, True, False, 0.75) == pytest.approx(0.4)
    assert on_air_level(1.0, False, True, 0.5) == 1.0


def test_the_crossfader_moving_updates_every_channel(app) -> None:
    bus, _w, rec, state = started(
        {
            "app.traktor.mixer.channels.1.volume": 1.0,
            "app.traktor.mixer.channels.1.xfader_assign.left": True,
        }
    )
    bus.change("app.traktor.mixer.xfader.adjust", 1.0)
    assert ("A", 0.0) in rec.of("deck_on_air")
    state.stop()


def test_the_tick_reports_position_and_phase(app) -> None:
    bus, _w, rec, state = started(
        {
            D1 + "track.content.track_length": 200.0,
            D1 + "track.player.playhead_position": 50.0,
            D1 + "tempo.phase": 0.125,
        }
    )
    assert ("A", 0.25) in rec.of("deck_position")
    assert ("A", 0.125) in rec.of("deck_phase")
    state.stop()


# ── commands ─────────────────────────────────────────────────────────────────


def control_for(bus: FakeBus) -> tuple[NativeDeckControl, list[int]]:
    resyncs: list[int] = []
    return NativeDeckControl(bus, lambda: resyncs.append(1)), resyncs


def test_load_mutes_cues_and_loads_by_path_then_syncs_and_plays(app) -> None:
    bus = FakeBus()
    control, _ = control_for(bus)
    control.execute(
        {"action": "load", "deck": "B", "path": "/m/x.mp3", "play": True, "mute": True, "cue": True}
    )
    assert bus.loads == [(1, "/m/x.mp3")]
    assert bus.values["app.traktor.mixer.channels.2.volume"] == 0.0
    assert bus.values["app.traktor.mixer.channels.2.cue"] is True
    assert bus.values["app.traktor.mixer.channels.1.cue"] is False
    assert "app.traktor.decks.2.play" not in bus.values  # not yet: the track is still loading
    wait(700)
    assert bus.values["app.traktor.decks.2.sync.enabled"] is True
    assert bus.values["app.traktor.decks.2.play"] is True


def test_a_skipped_load_only_runs_the_automation(app) -> None:
    bus = FakeBus()
    control, _ = control_for(bus)
    control.execute(
        {"action": "load", "deck": "A", "path": "", "play": False, "mute": True, "skip_load": True}
    )
    assert bus.loads == []
    assert bus.values["app.traktor.mixer.channels.1.volume"] == 0.0


def test_load_sets_the_key_knob_only_when_asked(app) -> None:
    bus = FakeBus()
    control, _ = control_for(bus)
    control.execute(
        {"action": "load", "deck": "A", "path": "/a", "play": False, "key_adjust": None}
    )
    assert D1 + "track.key.adjust" not in bus.values
    control.execute({"action": "load", "deck": "A", "path": "/a", "play": False, "key_adjust": 0.0})
    assert bus.values[D1 + "track.key.adjust"] == 0.0


def test_hotcue_and_beatjump_are_pressed_then_released(app) -> None:
    bus = FakeBus()
    control, _ = control_for(bus)
    control.execute({"action": "hotcue", "deck": "C", "index": 3})
    control.execute({"action": "beatjump", "deck": "A", "value": -10})
    assert bus.values["app.traktor.decks.3.track.cue.select_or_set_hotcue"] == 3
    assert bus.values[D1 + "beatjump"] == -10
    wait(120)
    assert bus.values["app.traktor.decks.3.track.cue.select_or_set_hotcue"] == 0
    assert bus.values[D1 + "beatjump"] == 0


def test_out_of_range_commands_do_nothing(app) -> None:
    bus = FakeBus()
    control, _ = control_for(bus)
    control.execute({"action": "hotcue", "deck": "C", "index": 9})
    control.execute({"action": "play", "deck": "E"})
    control.execute({"action": "explode"})
    assert bus.sets == []


def test_sync_without_a_deck_arms_all_four(app) -> None:
    bus = FakeBus()
    control, _ = control_for(bus)
    control.execute({"action": "sync", "enabled": True})
    assert [bus.values[f"app.traktor.decks.{n}.sync.enabled"] for n in range(1, 5)] == [True] * 4


def test_resync_goes_to_the_state(app) -> None:
    control, resyncs = control_for(FakeBus())
    control.execute({"action": "resync"})
    assert resyncs == [1]


def test_automix_raises_the_new_deck_then_fades_and_stops_the_old(app) -> None:
    bus = FakeBus(
        {
            "app.traktor.masterclock.tempo": 120.0,
            "app.traktor.decks.1.play": True,
            "app.traktor.mixer.channels.1.volume": 0.8,
        }
    )
    control, _ = control_for(bus)
    automix = control._automix
    automix.start(1, 1)  # deck B, one bar at 120 BPM = 2 s = 20 steps
    for _ in range(20):
        automix._step()
    assert bus.values["app.traktor.mixer.channels.2.volume"] == 1.0
    assert bus.values["app.traktor.mixer.channels.1.eq.low"] == 0.0  # bass swapped
    assert bus.values["app.traktor.mixer.channels.2.eq.low"] == 0.5
    for _ in range(20):
        automix._step()
    assert bus.values["app.traktor.mixer.channels.1.volume"] == pytest.approx(0.0)
    assert bus.values["app.traktor.decks.1.play"] is False
    assert not automix.active()


def test_ramp_length_follows_the_master_tempo() -> None:
    assert ramp_steps(120.0, 4) == 80
    assert ramp_steps(0.0, 4) == 80  # no master clock: 120 BPM
    assert deck_index("D") == 3 and deck_index(None) is None


# ── the watcher in native mode ───────────────────────────────────────────────


def test_attached_watcher_hands_commands_to_the_sink_not_the_queue(app) -> None:
    watcher = WebhookWatcher(port=0)
    got: list[dict] = []
    watcher.attach_native(got.append)
    watcher.queue_stop("A")
    watcher.request_resync()
    assert got == [{"action": "stop", "deck": "A"}, {"action": "resync"}]
    assert not watcher._pending_cmds and not watcher._resync_pending


def test_attached_watcher_ignores_the_bridges_state_posts(app) -> None:
    watcher = WebhookWatcher(port=0)
    rec = Recorder(watcher)
    watcher.attach_native(lambda _cmd: None)
    handle_post(watcher, "/updateDeck/A", '{"play": true}')
    handle_post(watcher, "/updateMasterClock", '{"bpm": 125}')
    assert rec.events == []


INSIDE = "/repo/traktor-native/re/embed_tk.py"


def test_inside_traktor_bridge_posts_are_dropped_even_without_native_state(
    app, monkeypatch
) -> None:
    """No D2 bridge inside Traktor (User, 2026-11-24): its posts never count, attached or not."""
    monkeypatch.setenv("TK_EMBED", INSIDE)
    watcher = WebhookWatcher(port=0)
    rec = Recorder(watcher)
    handle_post(watcher, "/updateDeck/A", '{"play": true}')
    handle_post(watcher, "/keepAlive", "{}")
    assert rec.events == []


def test_inside_traktor_libtks_own_posts_still_arrive(app, monkeypatch) -> None:
    monkeypatch.setenv("TK_EMBED", INSIDE)
    watcher = WebhookWatcher(port=0)
    shown: list[bool] = []
    watcher.show_requested.connect(lambda: shown.append(True))
    handle_post(watcher, "/show", "{}")
    assert shown == [True]


def test_inside_traktor_without_the_sink_commands_are_dropped_not_queued(app, monkeypatch) -> None:
    monkeypatch.setenv("TK_EMBED", INSIDE)
    watcher = WebhookWatcher(port=0)
    watcher.queue_stop("A")
    watcher.request_resync()
    assert not watcher._pending_cmds and not watcher._resync_pending


def test_outside_traktor_there_is_no_property_api() -> None:
    assert NativeProps.attach() is None
    QCoreApplication.processEvents()
