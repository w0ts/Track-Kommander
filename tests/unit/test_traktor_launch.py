"""Unit tests for the Traktor auto-start helpers in ``core.traktor``.

``find_traktor_app`` / ``launch_traktor`` are pure subprocess plumbing — the
installed-app candidates and ``subprocess.Popen`` are monkeypatched so no real
Traktor installation (or launch) is needed.
"""

from __future__ import annotations

import subprocess
import sys

from core import traktor

# ── find_traktor_app ──────────────────────────────────────────────────────────


def test_find_returns_first_existing_candidate(tmp_path, monkeypatch):
    missing = tmp_path / "Traktor Pro 4.app"
    present = tmp_path / "Traktor Pro 3.app"
    present.mkdir()
    monkeypatch.setattr(traktor, "_traktor_app_candidates", lambda: [missing, present])
    assert traktor.find_traktor_app() == present


def test_find_prefers_newest_version(tmp_path, monkeypatch):
    tp4 = tmp_path / "Traktor Pro 4.app"
    tp3 = tmp_path / "Traktor Pro 3.app"
    tp4.mkdir()
    tp3.mkdir()
    monkeypatch.setattr(traktor, "_traktor_app_candidates", lambda: [tp4, tp3])
    assert traktor.find_traktor_app() == tp4


def test_find_returns_none_when_nothing_installed(tmp_path, monkeypatch):
    monkeypatch.setattr(
        traktor,
        "_traktor_app_candidates",
        lambda: [tmp_path / "nope.app", tmp_path / "nada.exe"],
    )
    assert traktor.find_traktor_app() is None


def test_candidates_empty_on_unsupported_platform(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    assert traktor._traktor_app_candidates() == []


# ── launch_traktor ────────────────────────────────────────────────────────────


def test_launch_macos_uses_open_g(tmp_path, monkeypatch):
    """macOS must launch via ``open -g`` so Kommander keeps the foreground."""
    app = tmp_path / "Traktor Pro 4.app"
    app.mkdir()
    monkeypatch.setattr(traktor, "find_traktor_app", lambda: app)
    monkeypatch.setattr(sys, "platform", "darwin")

    calls: list[list[str]] = []
    monkeypatch.setattr(subprocess, "Popen", lambda cmd, **kw: calls.append(list(cmd)))
    assert traktor.launch_traktor() is True
    assert calls == [["open", "-g", str(app)]]


def test_launch_windows_runs_exe(tmp_path, monkeypatch):
    exe = tmp_path / "Traktor Pro 4" / "Traktor Pro 4.exe"
    exe.parent.mkdir()
    exe.touch()
    monkeypatch.setattr(traktor, "find_traktor_app", lambda: exe)
    monkeypatch.setattr(sys, "platform", "win32")

    calls: list[tuple[list[str], dict]] = []
    monkeypatch.setattr(subprocess, "Popen", lambda cmd, **kw: calls.append((list(cmd), kw)))
    assert traktor.launch_traktor() is True
    assert calls[0][0] == [str(exe)]
    assert calls[0][1].get("cwd") == str(exe.parent)


def test_launch_returns_false_when_not_installed(monkeypatch):
    monkeypatch.setattr(traktor, "find_traktor_app", lambda: None)
    assert traktor.launch_traktor() is False


def test_launch_returns_false_on_os_error(tmp_path, monkeypatch):
    app = tmp_path / "Traktor Pro 4.app"
    app.mkdir()
    monkeypatch.setattr(traktor, "find_traktor_app", lambda: app)
    monkeypatch.setattr(sys, "platform", "darwin")

    def _boom(cmd, **kw):
        raise OSError("spawn failed")

    monkeypatch.setattr(subprocess, "Popen", _boom)
    assert traktor.launch_traktor() is False
