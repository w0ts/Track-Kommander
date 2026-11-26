"""Inside Traktor (integrated mode) TK takes no screen captures of Traktor's decks."""

from __future__ import annotations

from ui.mw_calibration import CalibrationMixin


class _Host(CalibrationMixin):
    def __init__(self, integrated: bool) -> None:
        self._integrated = integrated
        self._last_auto_calibrate = 0.0
        self._calibrating = False
        self.captured = False

    def _start_capture(self, *_a, **_k) -> None:  # any capture would land here
        self.captured = True


def test_no_deck_calibration_inside_traktor() -> None:
    host = _Host(integrated=True)
    host._auto_calibrate_decks("startup", force=True)
    assert host._calibrating is False
    assert host.captured is False


def test_inside_traktor_a_show_request_shows_no_window() -> None:
    from ui.mw_native import NativeBandMixin

    class _Win(NativeBandMixin):
        _integrated = True
        shown = False

        def isMinimized(self) -> bool:
            return False

        def isVisible(self) -> bool:
            return False

        def show(self) -> None:
            self.shown = True

        def showNormal(self) -> None:
            self.shown = True

        def _force_raise(self) -> None:
            self.shown = True

    win = _Win()
    win._on_show_requested()
    assert win.shown is False


def test_no_auto_mode_tooltip_from_a_hidden_button(monkeypatch) -> None:
    # Inside Traktor TK's window (and its AUTO button) is never shown: no stray tip.
    from ui import np_auto

    shown: list = []
    monkeypatch.setattr(np_auto.QToolTip, "showText", lambda *a, **k: shown.append(a))

    class _Hidden:
        def isVisible(self) -> bool:
            return False

        def setToolTip(self, _t: str) -> None:
            pass

    class _Timer:
        def stop(self) -> None:
            pass

        def start(self) -> None:
            pass

    class _Tab(np_auto._AutoModeMixin):
        def __init__(self) -> None:
            self.auto_btn = _Hidden()
            self._auto_mode_idx = 0
            self._auto_unlock_timer = _Timer()
            self._auto_timer = _Timer()

        def _update_auto_btn_state(self) -> None:
            pass

    _Tab()._apply_auto_mode(show_tooltip=True)
    assert shown == []
