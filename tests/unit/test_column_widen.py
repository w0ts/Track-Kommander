"""Raising a default column width has to reach people who already ran the app.

Every column width is remembered per id the first time the table is built, so
a wider default alone changes nothing for anyone but a fresh install — the
stored value wins for ever. This is the one-shot that lets a raised default
through, without flattening a width the user chose on purpose.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QSettings

from ui.now_playing import widen_stored_columns


@pytest.fixture
def settings(tmp_path, monkeypatch):
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path))
    s = QSettings(QSettings.Format.IniFormat, QSettings.Scope.UserScope, "tk-test", "widen")
    s.clear()
    return s


def test_a_narrower_stored_width_is_dropped(settings) -> None:
    settings.setValue("results_colw/m:key", 70)
    assert widen_stored_columns(settings, {"m:key": 104}, flag="w1") == ["m:key"]
    assert settings.value("results_colw/m:key") is None


def test_a_width_the_user_widened_is_kept(settings) -> None:
    """Someone who dragged the column out to 160 meant it."""
    settings.setValue("results_colw/m:key", 160)
    assert widen_stored_columns(settings, {"m:key": 104}, flag="w1") == []
    assert int(settings.value("results_colw/m:key")) == 160


def test_a_column_never_stored_is_left_alone(settings) -> None:
    assert widen_stored_columns(settings, {"m:key": 104}, flag="w1") == []


def test_it_only_runs_once(settings) -> None:
    """Or it would undo every later narrowing, every launch."""
    settings.setValue("results_colw/m:key", 70)
    assert widen_stored_columns(settings, {"m:key": 104}, flag="w1") == ["m:key"]
    settings.setValue("results_colw/m:key", 70)
    assert widen_stored_columns(settings, {"m:key": 104}, flag="w1") == []
    assert int(settings.value("results_colw/m:key")) == 70


def test_a_later_raise_is_its_own_run(settings) -> None:
    """A different flag is a different migration."""
    settings.setValue("results_colw/m:key", 70)
    widen_stored_columns(settings, {"m:key": 104}, flag="w1")
    settings.setValue("results_colw/m:key", 80)
    assert widen_stored_columns(settings, {"m:key": 140}, flag="w2") == ["m:key"]


def test_junk_in_the_stored_width_is_not_fatal(settings) -> None:
    """The settings file is editable by hand and survives a downgrade."""
    settings.setValue("results_colw/m:key", "wide")
    assert widen_stored_columns(settings, {"m:key": 104}, flag="w1") == []
