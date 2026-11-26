"""Slow modules are imported off the GUI thread at startup (UI watchdog found
a ~420 ms first import of scipy.ndimage on it)."""

from __future__ import annotations

import importlib
import sys
import threading

import pytest

pytest.importorskip("PySide6.QtWidgets")

from ui import app


def test_warm_imports_load_on_a_background_thread(monkeypatch) -> None:
    seen: list[tuple[str, str]] = []

    def _fake_import(name: str):
        seen.append((name, threading.current_thread().name))

    monkeypatch.setattr(importlib, "import_module", _fake_import)
    app._warm_imports_in_background().join(5)
    assert seen == [(n, "import-warmup") for n in app._WARM_IMPORTS]


def test_a_missing_module_is_not_fatal(monkeypatch) -> None:
    monkeypatch.setattr(app, "_WARM_IMPORTS", ("no_such_module_xyz",))
    app._warm_imports_in_background().join(5)
    assert "no_such_module_xyz" not in sys.modules


def test_the_gil_switch_interval_favours_the_gui_thread() -> None:
    before = sys.getswitchinterval()
    try:
        app._favour_the_gui_thread()
        assert sys.getswitchinterval() <= 0.001
    finally:
        sys.setswitchinterval(before)
