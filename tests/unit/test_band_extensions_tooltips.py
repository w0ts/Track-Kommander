"""ui.band_extensions.push_tooltips: libtk's tk_band_set_tooltip through native_lib (macOS and
Windows alike), nothing when libtk is absent."""

from __future__ import annotations

from core.extensions import BandButton
from ui import band_extensions


def _button(tip: str) -> BandButton:
    return BandButton(tab="Tab", tooltip=tip, icon=lambda _p: None)


def test_each_taken_slot_gets_its_tooltip(monkeypatch):
    calls = []

    def fake_set(slot: bytes, text: bytes) -> int:
        calls.append((slot, text))
        return 0 if slot != b"X2" else -1  # libtk refuses one

    monkeypatch.setattr(
        band_extensions,
        "process_symbol",
        lambda name: fake_set if name == "tk_band_set_tooltip" else None,
    )
    monkeypatch.setattr(
        band_extensions, "assigned", lambda: {"X1": _button("One\nfirst"), "X2": _button("Two")}
    )
    assert band_extensions.push_tooltips() == 1
    assert calls == [(b"X1", b"One\nfirst"), (b"X2", b"Two")]


def test_without_libtk_no_tooltip_is_sent(monkeypatch):
    monkeypatch.setattr(band_extensions, "process_symbol", lambda _name: None)
    monkeypatch.setattr(band_extensions, "assigned", lambda: {"X1": _button("One")})
    assert band_extensions.push_tooltips() == 0
