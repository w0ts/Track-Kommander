"""Geometry tests for the matching-axis cards.

Relational axes (BPM/Key/Genre/Style/Similarity) have no value slider, and the
axes that do carry one also carry the ⟲ sync button. Both differences used to
shift the controls: the sync button grew the header row, and the missing value
slider pulled the strength slider up a row — so the sliders formed two
staggered lines across the panel instead of one. Pin the alignment here; it is
invisible to every behavioural test.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6.QtWidgets")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QSettings
from PySide6.QtWidgets import QApplication, QHBoxLayout, QWidget


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


# One relational axis (single slider) and one scalar axis (value + strength,
# plus the sync button) of each kind that appears in the panel.
_RELATIONAL_KEYS = ("bpm", "key", "genre", "style", "similarity")
_SCALAR_KEYS = ("rating", "valence", "happy")


@pytest.fixture
def cards(qapp, tmp_path):
    """Build one card per axis, laid out side by side like the real panel."""
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path))
    QCoreApplication.setApplicationName("tk-axis-card-test")
    QCoreApplication.setOrganizationDomain("axis-card.invalid")

    from core.features import BY_KEY
    from ui.now_playing import NowPlayingTab

    # The card builder only needs the settings store and the widget registry —
    # building a whole NowPlayingTab would pull in the database and workers.
    tab = NowPlayingTab.__new__(NowPlayingTab)
    tab._settings = QSettings()
    tab._axis_widgets = {}
    tab._source = None

    host = QWidget()
    row = QHBoxLayout(host)
    keys = [k for k in (*_RELATIONAL_KEYS, *_SCALAR_KEYS) if k in BY_KEY]
    for key in keys:
        row.addWidget(tab._build_axis_card(BY_KEY[key]))
    host.resize(900, 120)
    host.show()
    qapp.processEvents()
    yield host, tab, keys
    host.deleteLater()


def _y_in_host(host: QWidget, w: QWidget) -> int:
    return w.mapTo(host, w.rect().topLeft()).y()


def _is_relational(key: str) -> bool:
    from core.features import BY_KEY

    return bool(BY_KEY[key].relational)


def test_every_axis_card_puts_its_label_on_the_same_line(cards):
    host, tab, keys = cards
    ys = {k: _y_in_host(host, tab._axis_widgets[k]["label"]) for k in keys}
    assert len(set(ys.values())) == 1, f"headers are staggered: {ys}"


def test_every_axis_card_puts_its_strength_slider_on_the_same_line(cards):
    host, tab, keys = cards
    ys = {k: _y_in_host(host, tab._axis_widgets[k]["strength"]) for k in keys}
    assert len(set(ys.values())) == 1, f"strength sliders are staggered: {ys}"


def test_relational_cards_reserve_the_value_slider_row(cards):
    """A relational card has no value slider, but keeps its row empty so the
    strength slider stays on the lower line with everyone else."""
    host, tab, keys = cards
    relational = [k for k in keys if _is_relational(k)]
    scalar = [k for k in keys if not _is_relational(k)]
    assert relational and scalar
    for k in relational:
        assert "val" not in tab._axis_widgets[k]
    val_y = {_y_in_host(host, tab._axis_widgets[k]["val"]) for k in scalar}
    strength_y = {_y_in_host(host, tab._axis_widgets[k]["strength"]) for k in keys}
    assert len(val_y) == 1 and len(strength_y) == 1
    assert val_y.pop() < strength_y.pop()


def test_the_reserved_row_is_a_hidden_slider_not_a_bare_spacer(cards):
    """Styles hand a slider a layout rect smaller than the widget (9 px inside
    16 on macOS), so a plain spacer of the same height reserves a *different*
    row and the offset comes back on the native style only — which the y
    assertions above cannot see under the offscreen platform."""
    _host, tab, keys = cards
    from PySide6.QtWidgets import QSlider

    def _value_row(key: str) -> QSlider:
        # Card layout rows: header, value slider (or its placeholder), strength.
        w = tab._axis_widgets[key]["card"].layout().itemAt(1).widget()
        assert isinstance(w, QSlider), f"{key}: value row is {type(w).__name__}"
        return w

    heights = {_value_row(k).height() for k in keys}
    assert len(heights) == 1, f"value rows differ in height: {heights}"
    for key in (k for k in keys if _is_relational(k)):
        gap = _value_row(key)
        assert gap.isHidden()
        assert gap.sizePolicy().retainSizeWhenHidden(), (
            f"{key}: hidden placeholder must still occupy its row"
        )
