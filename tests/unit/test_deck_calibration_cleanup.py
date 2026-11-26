"""The calibration dialog holds a full-window screenshot; it must not outlive
the run that took it."""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6.QtWidgets")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication, QDialog, QWidget

import core.drag
import ui.deck_calibration as dc


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


def _image(w: int, h: int) -> QImage:
    img = QImage(w, h, QImage.Format.Format_RGB32)
    img.fill(0x202020)
    return img


def test_the_dialog_is_freed_after_it_closes(qapp, monkeypatch) -> None:
    monkeypatch.setattr(
        core.drag, "capture_traktor_window", lambda: (_image(2400, 1400), (0.0, 0.0, 1200.0, 700.0))
    )
    monkeypatch.setattr(dc.DeckCalibrationDialog, "exec", lambda self: QDialog.DialogCode.Rejected)
    parent = QWidget()
    assert dc.open_deck_calibration(parent) is False
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert parent.findChildren(dc.DeckCalibrationDialog) == []


def test_the_screenshot_is_scaled_once_to_the_view(qapp) -> None:
    view = dc._DeckImageView(_image(2560, 1600))
    dpr = view.devicePixelRatioF()
    w, h = view._display_size
    assert (w, h) == (1280, 800)
    assert view._pixmap.width() == round(w * dpr)
    assert view._pixmap.height() == round(h * dpr)
    view.grab()  # paints without rescaling
