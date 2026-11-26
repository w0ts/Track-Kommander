"""TK's BPM and KEY match columns in Traktor's list (User, 2026-11-24: "we miss the key match /
distance and bpm distance like we add in external tk ... like score"): the same text and colours as
TK's own results table."""

from __future__ import annotations

import pytest

from ui.native_match_cells import match_cells
from ui.np_format import _bpm_display, key_cell_text
from ui.style import key_score_color, score_color
from workers import native_columns
from workers.native_columns import MatchCells, set_scores


def _result(path: str, **kw) -> dict:
    row = {"path": path, "bpm": kw.pop("bpm", 126.0), "camelot": kw.pop("camelot", "5A")}
    return {"row": row, "composite": 80.0, **kw}


def test_cells_carry_tks_own_bpm_and_key_text_and_colours() -> None:
    r = _result("/m/a.mp3", effective_bpm=126.0, axis_scores={"bpm": 90.0, "key": 100.0})
    cells = match_cells([r], 120.0, "5A")["/m/a.mp3"]
    assert cells.score == 80.0
    assert cells.bpm == _bpm_display(126.0, 126.0, 1, 120.0)  # "126  +5.0%"
    assert cells.key == key_cell_text("5A", None, "5A")
    assert cells.bpm_argb == score_color(90.0).rgba()
    assert cells.key_argb == key_score_color(100.0).rgba()


def test_an_unscored_axis_gets_tks_dim_colour() -> None:
    cells = match_cells([_result("/m/a.mp3")], 120.0, "5A")["/m/a.mp3"]
    assert cells.bpm_argb == cells.key_argb == 0xFF444466


def test_without_a_source_track_the_cells_show_the_value_alone() -> None:
    cells = match_cells([_result("/m/a.mp3", camelot=None)], 0.0, "")["/m/a.mp3"]
    assert cells.bpm == "126" and cells.key == "—"


class _Fn:
    def __init__(self) -> None:
        self.sent: list[bytes] = []

    def __call__(self, text: bytes) -> None:
        self.sent.append(text)


def test_one_line_per_result_score_bpm_colour_key_colour(monkeypatch: pytest.MonkeyPatch) -> None:
    fn = _Fn()
    monkeypatch.setattr(native_columns, "_call", lambda name: fn)
    cells = MatchCells(
        score=87.4, bpm="126  +5.0%", bpm_argb=0xFF00E676, key="5m 0", key_argb=0xFF123456
    )
    assert set_scores({"/m/a.mp3": cells, "/m/b.mp3": MatchCells(score=None)}) is True
    lines = fn.sent[0].decode().split("\n")
    assert lines[0] == "/m/a.mp3\t87\t126  +5.0%\tff00e676\t5m 0\tff123456"
    assert lines[1] == "/m/b.mp3\t\t\t\t\t"  # no score, no match: Traktor's own colour


def test_a_path_with_a_tab_is_not_sent(monkeypatch: pytest.MonkeyPatch) -> None:
    fn = _Fn()
    monkeypatch.setattr(native_columns, "_call", lambda name: fn)
    set_scores({"/m/a\tb.mp3": MatchCells(score=50.0)})
    assert fn.sent == [b""]
