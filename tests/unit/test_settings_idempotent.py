"""Settings setters that run on a timer or per bridge message must be cheap
when nothing changed.

``set_notices`` runs every 5 s and on every show; ``set_traktor_active`` and
``set_bridge_stats`` run on every post from Traktor, several a second. Each
used to rebuild widgets or restyle them regardless, and the D2 checks re-read
Traktor's multi-megabyte settings file on every show.
"""

from __future__ import annotations

import logging
import os
from unittest.mock import MagicMock

import pytest

pytest.importorskip("PySide6.QtWidgets")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QSettings
from PySide6.QtWidgets import QApplication

import ui.settings as settings_mod
import ui.settings_d2 as d2_mod
import ui.settings_problems as problems_mod
from core.health import Notice


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def tab(qapp, tmp_path):
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path))
    QCoreApplication.setApplicationName("tk-settings-idempotent-test")
    QCoreApplication.setOrganizationDomain("settings-idempotent.invalid")
    w = settings_mod.SettingsTab()
    yield w
    w.deleteLater()


_WARN = Notice(key="k", severity="warning", text="Something is off")


# ── set_notices ─────────────────────────────────────────────────────────────


def test_unchanged_notices_keep_the_rows(tab) -> None:
    tab.set_notices((_WARN,), traktor_up=True, permissions=(True, True))
    rows = list(tab._problem_rows)
    tab.set_notices((_WARN,), traktor_up=True, permissions=(True, True))
    assert tab._problem_rows == rows
    assert all(r is s for r, s in zip(tab._problem_rows, rows, strict=True))


def test_changed_notices_rebuild_the_rows(tab) -> None:
    tab.set_notices((_WARN,), traktor_up=True, permissions=(True, True))
    rows = list(tab._problem_rows)
    tab.set_notices((), traktor_up=True, permissions=(True, True))
    assert tab._problem_rows != rows
    assert tab._problem_rows == []


def test_a_permission_change_rebuilds_the_rows(tab) -> None:
    tab.set_notices((), traktor_up=True, permissions=(True, True))
    assert tab._problem_rows == []
    tab.set_notices((), traktor_up=True, permissions=(False, True))
    assert len(tab._problem_rows) == 1


def test_passed_permissions_are_not_probed_again(tab, monkeypatch) -> None:
    probes: list[str] = []
    monkeypatch.setattr(problems_mod, "_accessibility_granted", lambda: probes.append("ax") or True)
    monkeypatch.setattr(
        problems_mod, "_screen_recording_granted", lambda: probes.append("sr") or True
    )
    tab.set_notices((), traktor_up=True, permissions=(True, True))
    assert probes == []
    tab.set_notices((), traktor_up=False)
    assert probes == ["ax", "sr"]


# ── Per-message setters ─────────────────────────────────────────────────────


def test_an_unchanged_check_is_not_restyled(tab) -> None:
    dot, value = MagicMock(), MagicMock()
    tab.check_rows["api"] = (dot, value)
    tab._painted_checks.pop("api", None)  # new widgets: nothing painted yet
    tab.set_bridge_stats(10, 0.5)
    tab.set_bridge_stats(11, 0.5)  # same check outcome, new count
    assert dot.setStyleSheet.call_count == 1


def test_an_active_bridge_is_not_repainted_per_ping(tab) -> None:
    hint = MagicMock()
    tab.traktor_hint = hint
    tab.set_traktor_active(True)
    tab.set_traktor_active(True)
    tab.set_traktor_active(True)
    assert hint.setVisible.call_count == 1
    assert tab._traktor_timeout.isActive()
    tab.set_traktor_active(False)
    assert hint.setVisible.call_count == 2
    assert not tab._traktor_timeout.isActive()


# ── D2 checks read from disk ────────────────────────────────────────────────


def test_an_unchanged_settings_file_is_not_read_again(tab, monkeypatch, tmp_path) -> None:
    import core.d2_status

    tsi = tmp_path / "Traktor Settings.tsi"
    tsi.write_bytes(b"x" * 10)
    reads: list[object] = []
    real = core.d2_status.controller_check
    monkeypatch.setattr(core.d2_status, "controller_check", lambda p: reads.append(p) or real(p))
    monkeypatch.setattr(tab, "_traktor_settings_path", lambda: tsi)
    tab._refresh_d2_device()
    tab._refresh_d2_device()
    assert len(reads) == 1
    tsi.write_bytes(b"x" * 20)
    tab._refresh_d2_device()
    assert len(reads) == 2


# ── Errors are reported, not swallowed ──────────────────────────────────────


def test_a_failed_folder_open_is_logged_and_told(tab, monkeypatch, tmp_path, caplog) -> None:
    def boom(*_a, **_k):
        raise OSError("no finder")

    import ui.dialogs

    told: list[str] = []
    monkeypatch.setattr(d2_mod.sys, "platform", "darwin")
    monkeypatch.setattr(d2_mod.subprocess, "run", boom)
    monkeypatch.setattr(ui.dialogs, "warn", lambda _p, _t, text: told.append(text))
    with caplog.at_level(logging.WARNING, logger="ui.settings"):
        tab._open_folder(tmp_path)
    assert any("no finder" in r.getMessage() or r.exc_info for r in caplog.records)
    assert told and str(tmp_path) in told[0]
