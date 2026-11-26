"""A track loaded with a transposed match arrives already transposed.

Key-T finds tracks that fit once Traktor's Key knob moves. Telling the DJ
the number and making them dial it in mid-mix would be half a feature, so
the load command carries the shift and the bridge sets it.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def watcher(tmp_path, monkeypatch):
    from PySide6.QtCore import QCoreApplication, QSettings

    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path))
    QCoreApplication.setApplicationName("tk-load-transpose-test")
    QCoreApplication.setOrganizationDomain("loadtranspose.invalid")
    import threading

    import workers.webhook as wh

    monkeypatch.setattr(wh, "traktor_search", lambda _q: None)
    w = wh.WebhookWatcher.__new__(wh.WebhookWatcher)
    w._cmd_lock = threading.Condition()  # as in WebhookWatcher: the long poll waits on it
    w._pending_cmds = []
    return w


def _cmd(watcher, **kw) -> dict:
    watcher._pending_cmds.clear()
    watcher.queue_load("/m/a.mp3", "A", **kw)
    return watcher._pending_cmds[-1]


def test_zero_resets_the_knob(watcher) -> None:
    """Strict Key mode, or a track needing no shift: the deck still has to be
    told, or it keeps the transpose the last track was played at."""
    assert _cmd(watcher, transpose=0)["transpose"] == 0


def test_a_caller_with_no_view_of_the_axis_can_leave_it_alone(watcher) -> None:
    assert _cmd(watcher)["transpose"] is None
    assert _cmd(watcher, transpose=None)["transpose"] is None


def test_the_shift_is_carried(watcher) -> None:
    assert _cmd(watcher, transpose=2)["transpose"] == 2


def test_a_downward_shift_keeps_its_sign(watcher) -> None:
    assert _cmd(watcher, transpose=-3)["transpose"] == -3


def test_an_absurd_shift_is_clamped(watcher) -> None:
    """The axis never asks for more than three, but a stale caller must not
    put a deck two octaves out."""
    assert _cmd(watcher, transpose=25)["transpose"] == 12
    assert _cmd(watcher, transpose=-25)["transpose"] == -12


# ── Traktor's Key knob is normalised, not semitones ─────────────────────────


def test_semitones_are_converted_for_traktor() -> None:
    """``track.key.adjust`` takes a normalised value, not a semitone count:
    1.0 is the full twelve semitones, so one semitone is a twelfth. Measured
    twice on a real deck — 1.0 moved it an octave, a sixth moved it two."""
    from workers.webhook import key_adjust_value

    assert key_adjust_value(1) == pytest.approx(1 / 12, abs=1e-9)
    assert key_adjust_value(2) == pytest.approx(2 / 12, abs=1e-9)
    assert key_adjust_value(-1) == pytest.approx(-1 / 12, abs=1e-9)


def test_a_full_octave_is_the_whole_range() -> None:
    from workers.webhook import key_adjust_value

    assert key_adjust_value(12) == pytest.approx(1.0)


def test_no_shift_is_exactly_zero() -> None:
    from workers.webhook import key_adjust_value

    assert key_adjust_value(0) == 0.0


def test_the_command_carries_the_normalised_value(watcher) -> None:
    cmd = _cmd(watcher, transpose=2)
    assert cmd["transpose"] == 2, "the semitone count stays, for the log and the UI"
    assert cmd["key_adjust"] == pytest.approx(2 / 12, abs=1e-9)


def test_leaving_the_knob_alone_sends_no_value(watcher) -> None:
    assert _cmd(watcher)["key_adjust"] is None
