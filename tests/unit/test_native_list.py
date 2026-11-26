"""TK's results reach Traktor's "Track Kommander" list only once that node was used."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from PySide6.QtWidgets import QApplication

from ui.native_list import MAX_TRACKS, NativeDeckMirror, NativeListPusher
from workers.native_cmd import NativeCmdError


@pytest.fixture(scope="module")
def app() -> Iterator[QApplication]:
    yield QApplication.instance() or QApplication([])


class _Sender:
    def __init__(self, fail: Exception | None = None) -> None:
        self.sent: list[list[str]] = []
        self.fail = fail

    def __call__(self, paths: list[str]) -> str:
        if self.fail:
            raise self.fail
        self.sent.append(paths)
        return f"OK {len(paths)}"


def test_nothing_is_sent_before_the_node_is_clicked(app) -> None:
    send = _Sender()
    pusher = NativeListPusher(lambda: ["/a.mp3"], send=send)
    pusher.results_changed()
    assert send.sent == []
    assert not pusher._timer.isActive()


def test_a_click_sends_the_best_results_now(app) -> None:
    send = _Sender()
    paths = [f"/t{i}.mp3" for i in range(MAX_TRACKS + 50)]
    pusher = NativeListPusher(lambda: paths, send=send)
    pusher.activate()
    assert send.sent == [paths[:MAX_TRACKS]]


def test_once_active_a_change_is_sent_after_a_pause(app) -> None:
    send = _Sender()
    paths = ["/a.mp3"]
    pusher = NativeListPusher(lambda: paths, send=send)
    pusher.activate()
    paths.append("/b.mp3")
    pusher.results_changed()
    pusher.results_changed()
    assert pusher._timer.isActive()  # one send for the burst, when the timer fires
    pusher._timer.timeout.emit()
    assert len(send.sent) == 2


def test_an_unchanged_list_is_not_sent_again(app) -> None:
    # Re-sending rebuilds Traktor's list and drops its selection / a drag.
    send = _Sender()
    paths = ["/a.mp3", "/b.mp3"]
    pusher = NativeListPusher(lambda: paths, send=send)
    pusher.activate()
    pusher.push()
    assert len(send.sent) == 1
    paths.reverse()  # a real change (new ranking) goes out
    pusher.push()
    assert send.sent[-1] == ["/b.mp3", "/a.mp3"]


def test_a_click_resends_even_an_unchanged_list(app) -> None:
    # Traktor may have restarted since and lost the list.
    send = _Sender()
    pusher = NativeListPusher(lambda: ["/a.mp3"], send=send)
    pusher.activate()
    pusher.activate()
    assert len(send.sent) == 2


def test_traktor_gone_pauses_until_clicked_again(app) -> None:
    pusher = NativeListPusher(lambda: ["/a.mp3"], send=_Sender(ConnectionRefusedError()))
    pusher.activate()
    assert not pusher.active
    pusher.results_changed()
    assert not pusher._timer.isActive()


def test_a_refusal_keeps_following(app) -> None:
    pusher = NativeListPusher(lambda: ["/a.mp3"], send=_Sender(NativeCmdError("ERR")))
    pusher.activate()
    assert pusher.active


def test_the_source_deck_is_sent_on_change_and_resent_on_request() -> None:
    sent: list[str] = []
    mirror = NativeDeckMirror(send=lambda d: sent.append(d) or "OK")
    mirror.changed("B")
    mirror.changed("")
    mirror.resend()
    assert sent == ["B", "", ""]


def test_the_deck_mirror_shrugs_off_a_closed_traktor() -> None:
    def refuse(_deck: str) -> str:
        raise ConnectionRefusedError

    mirror = NativeDeckMirror(send=refuse)
    mirror.changed("A")  # no exception
    assert mirror.deck == "A"


def test_cards_are_sent_only_when_they_change(app) -> None:
    from ui.native_list import NativeCardsMirror

    state = [("bpm", "BPM", True, 50, -1, 200, 30, "")]
    sent: list = []
    mirror = NativeCardsMirror(lambda: list(state), send=lambda c: sent.append(c) or "OK")
    mirror.poll()
    mirror.poll()
    assert len(sent) == 1
    state[0] = ("bpm", "BPM", False, 50, -1, 200, -1, "")
    mirror.poll()
    assert sent[-1][0][2] is False
    state[0] = (
        "bpm",
        "BPM",
        False,
        50,
        -1,
        200,
        55,
        "",
    )  # the playing track moved: the fill follows
    mirror.poll()
    assert sent[-1][0][6] == 55
    mirror.resend()  # the band was redrawn: send even unchanged
    assert len(sent) == 4


def test_the_auto_mode_is_sent_on_change_and_resent_on_request() -> None:
    from ui.native_list import NativeAutoMirror

    sent: list = []
    mirror = NativeAutoMirror(send=lambda m, b: sent.append((m, b)) or "OK")
    mirror.changed("automix", False)
    mirror.changed("auto", True)
    mirror.resend()
    assert sent == [("automix", False), ("auto", True), ("auto", True)]
