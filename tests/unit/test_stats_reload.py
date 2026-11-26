"""The Stats page follows the library, and draws cheaply.

MainWindow builds the page once and keeps it, so without a way to go stale
it showed the library as it was the first time Stats was opened, however
many scans ran since.
"""

from __future__ import annotations

import os
import sqlite3

import numpy as np
import pytest

pytest.importorskip("PySide6.QtWidgets")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QTimer
from PySide6.QtGui import QShowEvent
from PySide6.QtWidgets import QApplication

import ui.stats as stats_mod
from core import stats as core_stats


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


def _library(n: int = 40) -> tuple[dict, list]:
    rng = np.random.default_rng(1)
    values = {f.key: rng.random(n) for f in core_stats.NUMERIC_FEATURES}
    return values, ["8A", "9B"] * (n // 2)


@pytest.fixture
def loads(monkeypatch) -> list[int]:
    calls: list[int] = []

    def load():
        calls.append(1)
        return _library()

    monkeypatch.setattr(stats_mod, "load_library_values", load)
    return calls


def test_the_page_reads_the_library_once_per_reload(qapp, loads) -> None:
    tab = stats_mod.StatsTab()
    assert loads == [1]
    tab.deleteLater()


def test_showing_again_does_not_reload(qapp, loads) -> None:
    tab = stats_mod.StatsTab()
    tab.showEvent(QShowEvent())
    tab.showEvent(QShowEvent())
    assert len(loads) == 1
    tab.deleteLater()


def test_a_stale_page_reloads_on_the_next_show_only(qapp, loads) -> None:
    tab = stats_mod.StatsTab()
    tab.mark_stale()
    assert len(loads) == 1  # hidden: nothing to redraw yet
    tab.showEvent(QShowEvent())
    assert len(loads) == 2
    tab.showEvent(QShowEvent())
    assert len(loads) == 2
    tab.deleteLater()


def test_reloading_keeps_one_preview_timer(qapp, loads) -> None:
    tab = stats_mod.StatsTab()
    timer = tab._preview_timer
    tab.reload()
    tab.reload()
    assert tab._preview_timer is timer
    assert len(tab.findChildren(QTimer)) == 1
    tab.deleteLater()


def test_an_unreadable_library_is_logged(qapp, monkeypatch, caplog) -> None:
    def broken():
        raise sqlite3.OperationalError("no such table: tracks")

    monkeypatch.setattr(stats_mod, "load_library_values", broken)
    with caplog.at_level("WARNING", logger="ui.stats"):
        tab = stats_mod.StatsTab()
    assert any(r.exc_info for r in caplog.records)
    tab.deleteLater()


def test_load_library_values_reads_features_and_keys_together() -> None:
    conn = sqlite3.connect(":memory:")
    cols = [f.column for f in core_stats.NUMERIC_FEATURES]
    conn.execute(f"CREATE TABLE tracks ({', '.join(cols)}, camelot TEXT)")
    conn.execute(
        f"INSERT INTO tracks VALUES ({', '.join('?' * (len(cols) + 1))})",
        [0.5] * len(cols) + ["8A"],
    )
    conn.execute(
        f"INSERT INTO tracks VALUES ({', '.join('?' * (len(cols) + 1))})",
        [None] * len(cols) + [None],
    )
    values, keys = core_stats.load_library_values(conn)
    assert keys == ["8A", None]
    first = core_stats.NUMERIC_FEATURES[0].key
    assert values[first][0] == 0.5 and np.isnan(values[first][1])
    assert set(values) == set(core_stats.load_feature_values(conn))


# ── Scatter ─────────────────────────────────────────────────────────────────


def test_the_scatter_is_drawn_once_per_size(qapp, monkeypatch) -> None:
    xs = np.linspace(0, 1, 500)
    w = stats_mod._Scatter(xs, xs[::-1], "A", "B", 0.1, 0.2, -1.0)
    w.resize(420, 380)
    renders: list[tuple[int, int]] = []
    real = stats_mod._Scatter._draw_plot

    def spy(self, painter, width, height):
        renders.append((width, height))
        return real(self, painter, width, height)

    monkeypatch.setattr(stats_mod._Scatter, "_draw_plot", spy)
    first = w.grab().toImage()
    second = w.grab().toImage()
    assert len(renders) == 1
    assert first == second
    w.resize(500, 400)
    w.grab()
    assert len(renders) == 2
