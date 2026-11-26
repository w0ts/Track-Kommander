"""TK's own columns in Traktor's list: what TK sends for them."""

from __future__ import annotations

from workers.native_columns import MatchCells, score_text, set_roots, set_scores


def test_scores_read_as_tks_own_table_shows_them() -> None:
    assert score_text(87.4) == "87" and score_text(None) == ""


def test_outside_traktor_nothing_is_sent() -> None:
    assert set_scores({"/m/a.mp3": MatchCells(score=80.0)}) is False
    assert set_roots(["/Users/me/Music/Tracks"]) is False


def test_the_pusher_sends_the_scores_on_every_push(qtbot=None) -> None:
    from PySide6.QtWidgets import QApplication

    from ui.native_list import NativeListPusher

    QApplication.instance() or QApplication([])
    calls: list[str] = []
    pusher = NativeListPusher(
        lambda: ["/a.mp3"], send=lambda p: "OK", also=lambda: calls.append("scores")
    )
    pusher.activate()
    pusher.push()  # an unchanged list: still the scores (a re-rank changes them alone)
    assert calls == ["scores", "scores"]
