"""Clicking a distribution card uses or drops that dimension.

The same decision as the rank list, taken where you are actually looking:
the card shows you the shape, so the card should be where you act on it.
A dropped card is dimmed so the grid reads as the set of live dimensions.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def card(qapp):
    from core.stats import FeatureStats
    from ui.stats import _Hist

    st = FeatureStats(
        key="melodic",
        label="Melodic",
        short="Melodic",
        hue=190,
        unit="0–1",
        n=100,
        mean=0.6,
        median=0.6,
        std=0.1,
        min=0.0,
        max=1.0,
        p25=0.5,
        p75=0.7,
        counts=[1, 5, 20, 40, 20, 5],
        edges=[0, 0.2, 0.4, 0.6, 0.8, 0.9, 1.0],
        spread=0.2,
        spread_score=40.0,
    )
    w = _Hist(st, enabled=True)
    w.resize(260, 166)
    return w


def _click(w) -> None:
    pos = QPoint(w.width() // 2, w.height() // 2)
    w.mousePressEvent(
        QMouseEvent(
            QMouseEvent.Type.MouseButtonPress,
            pos,
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
    )


def test_a_click_emits_the_new_state(card) -> None:
    seen: list = []
    card.toggled.connect(lambda key, on: seen.append((key, on)))
    _click(card)
    assert seen == [("melodic", False)]


def test_clicking_twice_turns_it_back_on(card) -> None:
    seen: list = []
    card.toggled.connect(lambda key, on: seen.append(on))
    _click(card)
    _click(card)
    assert seen == [False, True]


def test_the_card_shows_its_state(card) -> None:
    assert card.enabled is True
    _click(card)
    assert card.enabled is False


def test_a_dropped_card_still_paints(card) -> None:
    _click(card)
    card.grab()  # dimmed, not blank


def test_the_card_says_it_can_be_clicked(card) -> None:
    assert "click" in card.toolTip().lower()


# ── The card and the rank row are one decision ──────────────────────────────


def test_the_card_starts_in_the_state_the_setting_holds(qapp, tmp_path, monkeypatch) -> None:
    from PySide6.QtCore import QCoreApplication, QSettings

    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path))
    QCoreApplication.setApplicationName("tk-card-state-test")
    QCoreApplication.setOrganizationDomain("cardstate.invalid")
    from core.features import axis_enabled_key
    from core.stats import FeatureStats
    from ui.stats import _Hist

    QSettings().setValue(axis_enabled_key("melodic"), False)
    st = FeatureStats(
        key="melodic",
        label="Melodic",
        short="Melodic",
        hue=190,
        unit="0–1",
        n=10,
        mean=0.5,
        median=0.5,
        std=0.1,
        min=0.0,
        max=1.0,
        p25=0.4,
        p75=0.6,
        counts=[1, 2, 1],
        edges=[0, 0.3, 0.6, 1.0],
        spread=0.2,
        spread_score=40.0,
    )
    on = QSettings().value(axis_enabled_key("melodic"), True, type=bool)
    assert _Hist(st, enabled=on).enabled is False
