"""MainWindow wiring: each request reaches Traktor once, a tick asks the
window server once, and quitting leaves no QThread running."""

from __future__ import annotations

import os
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

pytest.importorskip("PySide6.QtWidgets")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication

import core.drag
import ui.main_window as mw
from ui.main_window import MainWindow


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


# ── 1. A deck stop is queued once ────────────────────────────────────────────


class _NowTab(QObject):
    stop_deck_requested = Signal(str)

    def __getattr__(self, name):
        # Every other slot _apply_connection wires to: a no-op.
        return lambda *a, **k: None


def _window_with_watchers(monkeypatch) -> tuple[MagicMock, list[MagicMock]]:
    made: list[MagicMock] = []

    def fake_watcher(port: int) -> MagicMock:
        w = MagicMock(name=f"watcher{len(made)}")
        made.append(w)
        return w

    monkeypatch.setattr(mw, "WebhookWatcher", fake_watcher)
    win = MagicMock()
    win._watcher = None
    win.now_tab = _NowTab()
    # What __init__ does: the stop goes through the handler, which reads the
    # current watcher when it runs.
    win.now_tab.stop_deck_requested.connect(lambda deck: MainWindow._handle_stop_deck(win, deck))
    return win, made


def test_one_stop_request_queues_one_stop(qapp, monkeypatch) -> None:
    win, made = _window_with_watchers(monkeypatch)
    MainWindow._apply_connection(win, 12345)
    win.now_tab.stop_deck_requested.emit("A")
    assert made[0].queue_stop.call_count == 1


def test_a_reconnect_sends_stops_to_the_new_watcher_only(qapp, monkeypatch) -> None:
    win, made = _window_with_watchers(monkeypatch)
    MainWindow._apply_connection(win, 12345)
    MainWindow._apply_connection(win, 12345)
    MainWindow._apply_connection(win, 12345)
    win.now_tab.stop_deck_requested.emit("B")
    assert [w.queue_stop.call_count for w in made] == [0, 0, 1]
    made[2].queue_stop.assert_called_once_with("B")


# ── 6. One window-list read per on-top tick ─────────────────────────────────


def test_the_on_top_tick_reads_the_window_list_once(qapp, monkeypatch) -> None:
    calls = {"bounds": 0, "visible": 0}

    def bounds():
        calls["bounds"] += 1
        return (0, 33, 1728, 1084)

    def visible():
        calls["visible"] += 1
        return True

    monkeypatch.setattr(core.drag, "_traktor_logical_bounds", bounds)
    monkeypatch.setattr(core.drag, "is_traktor_visible", visible)
    monkeypatch.setattr(core.drag, "front_app", lambda: "traktor")
    hover = MagicMock()
    hover.isActive.return_value = False
    win = SimpleNamespace(
        _docked=True,
        _on_top=True,
        _last_front="traktor",
        _traktor_rect=None,
        _hover_timer=hover,
        _keep_on_top=lambda: None,
        _schedule_recalibrate=lambda reason: None,
    )
    MainWindow._update_on_top(win)
    assert calls == {"bounds": 1, "visible": 0}


def test_traktor_gone_turns_on_top_off(qapp, monkeypatch) -> None:
    monkeypatch.setattr(core.drag, "_traktor_logical_bounds", lambda: None)
    monkeypatch.setattr(core.drag, "front_app", lambda: "traktor")
    monkeypatch.setattr(core.drag, "front_app_name", lambda: "Traktor")
    set_on_top: list[bool] = []
    import ui.window_level

    monkeypatch.setattr(ui.window_level, "set_always_on_top", lambda _w, on: set_on_top.append(on))
    win = SimpleNamespace(
        _docked=True,
        _on_top=True,
        _last_front="traktor",
        _traktor_rect=None,
        _hover_timer=None,
        _keep_on_top=lambda: None,
        _schedule_recalibrate=lambda reason: None,
    )
    MainWindow._update_on_top(win)
    assert set_on_top == [False]


# ── 5. Quitting stops every worker thread ───────────────────────────────────


def _worker(running: bool = True) -> MagicMock:
    w = MagicMock()
    w.isRunning.return_value = running
    return w


def test_close_stops_the_export_worker(qapp, monkeypatch) -> None:
    monkeypatch.setattr(mw.keyforward, "release_all", lambda: None)
    export = _worker()
    win = _with_methods(
        SimpleNamespace(
            now_tab=SimpleNamespace(flush_settings=lambda: None, _reanalyse_worker=None),
            lib_tab=SimpleNamespace(_worker=None, _hc_worker=None, _export_worker=export),
            _stats_tab=None,  # Stats never opened (as MainWindow starts)
            _wake_lock=MagicMock(),
            _s4mk2=None,
            _watcher=None,
        )
    )
    MainWindow.closeEvent(win, MagicMock())
    export.stop.assert_called_once()
    export.wait.assert_called_once()


# Real failure: the close waited three seconds for a worker and then went
# ahead. A file on its way to a slow stick takes longer, and Qt ends the
# process when a thread is destroyed while it runs.


def _with_methods(win: SimpleNamespace) -> SimpleNamespace:
    """MainWindow's own helpers, bound to the stand-in window."""
    win._shut = False
    win._workers = lambda: MainWindow._workers(win)
    win._shut_down = lambda: MainWindow._shut_down(win)
    return win


def _closing_window(export) -> SimpleNamespace:
    return _with_methods(
        SimpleNamespace(
            now_tab=SimpleNamespace(flush_settings=lambda: None, _reanalyse_worker=None),
            lib_tab=SimpleNamespace(_worker=None, _hc_worker=None, _export_worker=export),
            _stats_tab=None,  # Stats never opened (as MainWindow starts)
            _wake_lock=MagicMock(),
            _s4mk2=None,
            _watcher=None,
            notice_banner=MagicMock(),
            close=MagicMock(),
        )
    )


def test_quitting_with_the_window_hidden_still_stops_the_threads(qapp, monkeypatch) -> None:
    """Quit while minimised to the button: the window never closes, and Qt
    aborted on the webhook thread still running (2026-10-05)."""
    monkeypatch.setattr(mw.keyforward, "release_all", lambda: None)
    win = _closing_window(_worker())
    win._watcher = MagicMock()
    MainWindow._on_about_to_quit(win)
    win._watcher.stop.assert_called_once()
    win._watcher.wait.assert_called_once()
    MainWindow.closeEvent(win, MagicMock())  # a close after it stops nothing twice
    win._watcher.stop.assert_called_once()


def test_close_waits_for_a_worker_that_is_still_busy(qapp, monkeypatch) -> None:
    monkeypatch.setattr(mw.keyforward, "release_all", lambda: None)
    again: list = []
    monkeypatch.setattr(mw.QTimer, "singleShot", lambda ms, fn: again.append(fn))
    export = _worker()
    export.wait.return_value = False  # mid-file: not done in time
    win = _closing_window(export)
    event = MagicMock()

    MainWindow.closeEvent(win, event)

    event.ignore.assert_called_once()
    event.accept.assert_not_called()
    win._wake_lock.release.assert_not_called()
    win.notice_banner.show_transient.assert_called_once()
    assert again == [win.close], "it has to look again by itself"


def test_close_goes_ahead_once_the_worker_has_finished(qapp, monkeypatch) -> None:
    monkeypatch.setattr(mw.keyforward, "release_all", lambda: None)
    monkeypatch.setattr(mw.QTimer, "singleShot", lambda ms, fn: None)
    export = _worker()
    export.wait.return_value = False
    win = _closing_window(export)
    MainWindow.closeEvent(win, MagicMock())

    export.isRunning.return_value = False  # the file landed
    event = MagicMock()
    MainWindow.closeEvent(win, event)

    event.accept.assert_called_once()
    win._wake_lock.release.assert_called_once()
    win.notice_banner.show_transient.assert_called_once()  # said once


def test_close_does_not_wait_for_ever(qapp, monkeypatch) -> None:
    monkeypatch.setattr(mw.keyforward, "release_all", lambda: None)
    monkeypatch.setattr(mw.QTimer, "singleShot", lambda ms, fn: None)
    clock = {"t": 1000.0}
    monkeypatch.setattr(mw.time, "monotonic", lambda: clock["t"])
    export = _worker()
    export.wait.return_value = False
    win = _closing_window(export)
    MainWindow.closeEvent(win, MagicMock())

    clock["t"] += mw.GIVE_UP_AFTER_S + 1
    event = MagicMock()
    MainWindow.closeEvent(win, event)

    event.accept.assert_called_once()


def test_the_update_check_runs_on_a_daemon_thread(qapp, monkeypatch) -> None:
    """urlopen cannot be interrupted, so the check must never be a QThread
    that Qt could destroy while it runs; a daemon thread just ends with us."""
    import core.update_check

    release = core.update_check.Release(version="9.9.9", url="https://example.invalid")
    started = threading.Event()
    threads: list[threading.Thread] = []

    def check(current: str):
        threads.append(threading.current_thread())
        started.set()
        return release

    monkeypatch.setattr(core.update_check, "check_for_update", check)
    found: list[core.update_check.Release] = []

    class Window(QObject):  # owns the relay, as the real window does
        def _on_update_found(self, r) -> None:
            found.append(r)

    win = Window()
    MainWindow._run_update_check(win)
    assert win.findChildren(QObject)  # the relay: never held by the thread alone
    assert started.wait(5)
    threads[0].join(5)
    assert threads[0].daemon
    for _ in range(20):
        qapp.processEvents()
        if found:
            break
    assert found == [release]


# ── 8. A finished scan leaves the Stats page stale ──────────────────────────


def test_a_library_change_marks_stats_stale(qapp) -> None:
    stats = MagicMock()
    win = SimpleNamespace(_stats_tab=stats)
    MainWindow._on_library_changed(win)
    stats.mark_stale.assert_called_once()


def test_a_library_change_before_stats_was_opened_is_fine(qapp) -> None:
    MainWindow._on_library_changed(SimpleNamespace(_stats_tab=None))


# ── 6. The controller says for itself whether it is connected ───────────────
# The status in Settings came from whether the S4 was plugged in when the
# watcher was started, and never changed after: green for a backend that had
# failed to open, and nothing at all for a controller plugged in later.


def _s4_window(
    monkeypatch, *, enabled: bool, plugged_in: list[bool]
) -> tuple[SimpleNamespace, list, list]:
    from PySide6.QtCore import QSettings

    QSettings().setValue("s4mk2_enabled", enabled)
    made: list[MagicMock] = []
    later: list = []

    def watcher() -> MagicMock:
        made.append(MagicMock(name="S4MK2Watcher"))
        return made[-1]

    monkeypatch.setattr(mw, "S4MK2Watcher", watcher)
    monkeypatch.setattr(mw, "s4mk2_connected", lambda: plugged_in.pop(0))
    monkeypatch.setattr(mw.QTimer, "singleShot", lambda ms, fn: later.append(fn))
    win = SimpleNamespace(_s4mk2=None, settings_tab=MagicMock(), _on_s4_control=lambda _c: None)
    win._apply_s4mk2 = lambda: MainWindow._apply_s4mk2(win)
    win._look_for_s4_again = lambda: MainWindow._look_for_s4_again(win)
    return win, made, later


def test_the_watcher_reports_its_own_link(qapp, monkeypatch) -> None:
    win, made, _later = _s4_window(monkeypatch, enabled=True, plugged_in=[True])

    MainWindow._apply_s4mk2(win)

    [watcher] = made
    watcher.link.connect.assert_called_once_with(win.settings_tab.set_s4mk2_status)
    watcher.start.assert_called_once()
    # Not "Connected" on our say-so: that is the watcher's to report.
    said = [c.args for c in win.settings_tab.set_s4mk2_status.call_args_list]
    assert said == [(False, "Connecting…")]


def test_a_controller_that_is_not_there_is_looked_for_again(qapp, monkeypatch) -> None:
    win, made, later = _s4_window(monkeypatch, enabled=True, plugged_in=[False, True])

    MainWindow._apply_s4mk2(win)
    assert made == [] and len(later) == 1

    later.pop()()  # the timer fires: it is there now

    assert len(made) == 1
    made[0].start.assert_called_once()
    assert later == [], "found: nothing left to look for"


def test_touching_the_setting_does_not_start_a_second_search(qapp, monkeypatch) -> None:
    win, _made, later = _s4_window(monkeypatch, enabled=True, plugged_in=[False, False, False])

    MainWindow._apply_s4mk2(win)
    MainWindow._apply_s4mk2(win)
    MainWindow._apply_s4mk2(win)

    assert len(later) == 1


def test_a_controller_that_is_switched_off_is_not_looked_for(qapp, monkeypatch) -> None:
    win, made, later = _s4_window(monkeypatch, enabled=False, plugged_in=[])

    MainWindow._apply_s4mk2(win)

    assert made == [] and later == []
    win.settings_tab.set_s4mk2_status.assert_called_once_with(False, "Disabled")
