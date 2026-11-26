"""Every control on a matching-axis card explains itself on hover: what it
does, what its current state is, and what clicking or dragging it changes.
The cards are the app's main control surface and nothing else documents them.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6.QtWidgets")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QSettings
from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def tab(qapp, tmp_path):
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path))
    QCoreApplication.setApplicationName("tk-axis-tip-test")
    QCoreApplication.setOrganizationDomain("axis-tip.invalid")
    from core.features import BY_KEY
    from ui.now_playing import NowPlayingTab

    t = NowPlayingTab.__new__(NowPlayingTab)
    t._settings = QSettings()
    t._axis_widgets = {}
    t._source = None
    t._rerank_debounce = None
    t._schedule_refresh = lambda: None
    t._refresh = lambda: None
    from PySide6.QtCore import QTimer

    t._persist_timer = QTimer()
    t._persist_timer.setSingleShot(True)
    for key in ("bpm", "rating"):
        t._build_axis_card(BY_KEY[key])
    return t


def test_the_label_says_what_the_axis_is_and_how_to_toggle_it(tab) -> None:
    tip = tab._axis_widgets["rating"]["label"].toolTip()
    assert "Rating" in tip
    assert "on" in tip.lower() and "click" in tip.lower()


def test_the_label_tip_reflects_on_off(tab) -> None:
    refs = tab._axis_widgets["rating"]
    assert "Click to turn off" in refs["label"].toolTip()
    tab._toggle_axis_active("rating")
    assert "Click to turn on" in refs["label"].toolTip()


def test_the_strength_slider_names_its_weight(tab) -> None:
    refs = tab._axis_widgets["rating"]
    refs["strength"].setValue(70)
    tip = refs["strength"].toolTip()
    assert "70%" in tip
    assert "0" in tip and "off" in tip.lower()


def test_the_value_slider_explains_the_target_and_the_fill(tab) -> None:
    refs = tab._axis_widgets["rating"]
    tip = refs["val"].toolTip()
    assert "target" in tip.lower()
    assert "fill" in tip.lower() or "background" in tip.lower()
    assert "follow" in tip.lower()


def test_the_sync_button_reflects_its_state(tab) -> None:
    refs = tab._axis_widgets["rating"]
    tab._set_axis_sync("rating", True)
    tab._style_axis_row("rating")
    assert "following" in refs["sync_btn"].toolTip().lower()
    tab._set_axis_sync("rating", False)
    tab._style_axis_row("rating")
    assert "held" in refs["sync_btn"].toolTip().lower()


def test_a_relational_axis_says_what_it_compares_against(tab) -> None:
    tip = tab._axis_widgets["bpm"]["label"].toolTip()
    assert "current track" in tip.lower()
    assert "sync_btn" not in tab._axis_widgets["bpm"]
