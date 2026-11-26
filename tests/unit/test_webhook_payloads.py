"""The bridge's numbers cross a network from QML: one bad value must not
cost the rest of the post.

``_handle_post`` runs after the 200 has gone back, so an exception there is
never seen by the bridge — it just silently drops every signal that post
would have carried, including the ping that keeps the connection "alive".
"""

from __future__ import annotations

import json
import logging

import pytest
from PySide6.QtCore import QSettings

from workers import webhook
from workers.webhook import WebhookWatcher, _num


class _Spy:
    def __init__(self) -> None:
        self.seen: list[tuple] = []

    def emit(self, *args) -> None:
        self.seen.append(args)


class _FakeWatcher:
    def __init__(self) -> None:
        for name in (
            "deck_state",
            "deck_loaded",
            "deck_position",
            "deck_phase",
            "deck_key_adjust",
            "deck_tempo",
            "deck_on_air",
            "master_tempo",
            "d2_version",
            "layout_changed",
            "traktor_ping",
            "show_requested",
            "native_track",
            "ui_action",
            "ui_card",
        ):
            setattr(self, name, _Spy())


def _post(path: str, data: dict) -> _FakeWatcher:
    watcher = _FakeWatcher()
    WebhookWatcher._handle_post(watcher, path, json.dumps(data))
    return watcher


def test_num_is_forgiving() -> None:
    assert _num("1.5", 0.0) == 1.5
    assert _num(None, 0.0) == 0.0
    assert _num("fast", 7.0) == 7.0
    assert _num(float("nan"), 0.0) == 0.0
    assert _num({"x": 1}, 0.0) == 0.0


def test_show_raises_the_window_without_a_bridge_ping() -> None:
    # The native opener posts /show when the "Track Kommander" entry is
    # clicked in Traktor's own tree. It must raise us, but a click is not
    # proof the D2 bridge is alive, so it must not flip "Traktor connected".
    w = _post("/show", {})
    assert w.show_requested.seen == [()]
    assert w.traktor_ping.seen == []


def test_show_handles_an_empty_body() -> None:
    # libtk sends a minimal body; an empty one must not drop the signal.
    watcher = _FakeWatcher()
    WebhookWatcher._handle_post(watcher, "/show", "")
    assert watcher.show_requested.seen == [()]


def test_native_track_emits_metadata_without_a_bridge_ping() -> None:
    # libtk read this straight from Traktor's C++ (not the QML bridge). It must
    # reach the UI but not count as a D2 bridge ping.
    w = _post(
        "/nativeTrack",
        {"title": "Strobe", "artist": "deadmau5", "bpm": 128.0, "deck": "a", "source": "deckLoad"},
    )
    assert len(w.native_track.seen) == 1
    (info,) = w.native_track.seen[0]
    assert info == {
        "title": "Strobe",
        "artist": "deadmau5",
        "bpm": 128.0,
        "deck": "A",  # upper-cased
        "source": "deckLoad",
    }
    assert w.traktor_ping.seen == []


def test_ui_action_emits_the_button_without_a_bridge_ping() -> None:
    # A click in libtk's native band inside Traktor; the name is case-folded.
    w = _post("/uiAction", {"action": "settings"})
    assert w.ui_action.seen == [("SETTINGS",)]
    assert w.traktor_ping.seen == []


def test_ui_action_drops_unknown_or_missing_names() -> None:
    for body in ({"action": "rm -rf"}, {"action": 3}, {}):
        w = _post("/uiAction", body)
        assert w.ui_action.seen == []
        assert w.traktor_ping.seen == []


def test_native_track_is_forgiving_of_missing_fields() -> None:
    # The metadata crossed a socket from C; a missing field must not drop it.
    w = _post("/nativeTrack", {"title": "Untitled"})
    (info,) = w.native_track.seen[0]
    assert info == {"title": "Untitled", "artist": "", "bpm": 0.0, "deck": "", "source": ""}


def test_a_malformed_bpm_still_reports_the_load() -> None:
    w = _post("/deckLoaded/a", {"title": "T", "bpm": "fast", "trackLength": "long"})
    assert len(w.deck_loaded.seen) == 1
    deck, info = w.deck_loaded.seen[0]
    assert deck == "A"
    assert info["bpm"] == 0.0 and info["track_length"] == 0.0
    assert w.traktor_ping.seen == [()]


def test_a_malformed_keepalive_keeps_the_good_fields() -> None:
    w = _post(
        "/keepAlive",
        {
            "decks": [
                {"deck": "A", "play": True, "position": "somewhere"},
                {"deck": "B", "play": False, "position": 0.25},
            ],
            "channels": [
                {"index": "1", "onAirLevel": None},
                {"index": "2", "onAirLevel": "loud"},
                {"index": "3", "onAirLevel": 0.5},
            ],
        },
    )
    assert w.deck_state.seen == [("A", True), ("B", False)]
    assert w.deck_position.seen == [("B", 0.25)]
    assert w.deck_on_air.seen == [("A", 0.0), ("B", 0.0), ("C", 0.5)]
    assert w.traktor_ping.seen == [()]


def test_a_malformed_channel_volume_reads_as_closed() -> None:
    w = _post("/updateChannel/A", {"volume": "up"})
    assert w.deck_on_air.seen == [("A", 0.0)]
    assert w.traktor_ping.seen == [()]


def test_a_malformed_master_clock_is_ignored() -> None:
    w = _post("/updateMasterClock", {"bpm": "quick"})
    assert w.master_tempo.seen == []
    assert w.traktor_ping.seen == [()]


# ── A post that is not shaped like one ──────────────────────────────────────
# The 200 has already gone back, so whatever goes wrong here is silent. It
# used to raise on any of these and take the whole post with it.


def _post_raw(path: str, body: str) -> _FakeWatcher:
    watcher = _FakeWatcher()
    WebhookWatcher._handle_post(watcher, path, body)
    return watcher


@pytest.mark.parametrize("body", ["[1, 2]", "7", '"text"', "null", "true"])
def test_json_that_is_not_an_object_is_ignored(body: str) -> None:
    w = _post_raw("/keepAlive", body)
    assert w.traktor_ping.seen == []
    assert w.deck_state.seen == []


def test_a_title_that_is_not_text_is_made_text() -> None:
    w = _post("/deckLoaded/A", {"title": 1999, "artist": None, "key": 8})
    [(deck, info)] = w.deck_loaded.seen
    assert deck == "A"
    assert (info["title"], info["artist"], info["key"]) == ("1999", "", "8")


@pytest.mark.parametrize(
    "path",
    [
        "/deckLoaded/E",
        "/deckLoaded/",
        "/deckLoaded/AA",
        "/updateDeck/..",
        "/updateDeck/drag_deck",
        "/updateChannel/5",
        "/updateChannel/0",
    ],
)
def test_a_deck_traktor_does_not_have_is_ignored(path: str) -> None:
    w = _post(path, {"title": "T", "play": True, "volume": 1.0})
    assert w.deck_loaded.seen == []
    assert w.deck_state.seen == []
    assert w.deck_on_air.seen == []
    assert w.traktor_ping.seen == []


@pytest.mark.parametrize(
    ("path", "deck"),
    [
        ("/updateDeck/a", "A"),
        ("/updateDeck/D", "D"),
        ("/updateChannel/1", "A"),
        ("/updateChannel/4", "D"),
        ("/updateChannel/b", "B"),
    ],
)
def test_the_four_decks_still_are(path: str, deck: str) -> None:
    w = _post(path, {"play": True, "volume": 1.0})
    seen = w.deck_state.seen or w.deck_on_air.seen
    assert seen[0][0] == deck


def test_a_keepalive_skips_what_is_not_a_deck() -> None:
    w = _post(
        "/keepAlive",
        {
            "decks": [
                {"deck": "A", "play": True},
                {"deck": "Z", "play": True},
                "not a deck",
                None,
                {"deck": "B", "play": False},
            ],
            "channels": "not a list",
        },
    )
    assert w.deck_state.seen == [("A", True), ("B", False)]
    assert w.deck_on_air.seen == []
    assert w.traktor_ping.seen == [()]


def test_a_keepalive_whose_decks_are_not_a_list_still_pings() -> None:
    w = _post("/keepAlive", {"version": "8", "decks": {"deck": "A"}})
    assert w.d2_version.seen == [("8",)]
    assert w.deck_state.seen == []
    assert w.traktor_ping.seen == [()]


def test_a_title_cannot_write_lines_in_the_log(caplog) -> None:
    forged = "Title\n2026-09-29 12:00:00 ERROR something alarming"
    with caplog.at_level(logging.INFO, logger="webhook"):
        _post("/deckLoaded/A", {"title": forged, "artist": "x" * 5000})

    [line] = [r.getMessage() for r in caplog.records if r.getMessage().startswith("deckLoaded")]
    assert "\n" not in line
    assert len(line) < 400


@pytest.mark.parametrize(
    "layout", ["two decks", "x" * 200, "a\nb", 7, ["twoDecks"], "null", "undefined", ""]
)
def test_a_layout_that_is_not_a_name_is_not_stored(layout) -> None:
    QSettings().remove("traktor_gui_layout")

    _post("/keepAlive", {"gui_layout": layout})

    assert QSettings().value("traktor_gui_layout") is None


def test_a_layout_name_is_stored() -> None:
    QSettings().remove("traktor_gui_layout")

    _post("/keepAlive", {"gui_layout": "fourDecks"})

    assert QSettings().value("traktor_gui_layout") == "fourDecks"


def test_phase_rows_are_per_request() -> None:
    """Two server threads (IPv4 and IPv6) handle posts at once; a shared
    module dict let one request log, or clear, the other's rows."""
    assert not hasattr(webhook, "_phase_row")


def test_info_diagnostics_reach_the_log() -> None:
    """The webhook logger follows the root level, so "webhook bound: …"
    lines are written at the default INFO."""
    root = logging.getLogger()
    old = root.level
    root.setLevel(logging.INFO)
    try:
        assert webhook.log.isEnabledFor(logging.INFO)
        # Per-request traces stay out of an ordinary log.
        assert not webhook.request_log.isEnabledFor(logging.DEBUG)
    finally:
        root.setLevel(old)


def test_a_card_click_names_a_known_axis() -> None:
    w = _post("/uiAction", {"action": "card:bpm"})
    assert w.ui_action.seen == [("CARD:bpm",)]


def test_a_card_click_for_an_unknown_axis_is_dropped() -> None:
    w = _post("/uiAction", {"action": "CARD:../../etc"})
    assert w.ui_action.seen == []


def test_a_card_slider_from_traktor_is_clamped_and_checked() -> None:
    w = _post("/uiCard", {"key": "bpm", "field": "strength", "value": 140})
    assert w.ui_card.seen == [("bpm", "strength", 100)]
    for bad in (
        {"key": "nope", "field": "value", "value": 1},
        {"key": "bpm", "field": "colour", "value": 1},
        {"key": "bpm", "field": "value", "value": "high"},
    ):
        assert _post("/uiCard", bad).ui_card.seen == []


def test_the_list_button_is_a_known_action() -> None:
    assert _post("/uiAction", {"action": "list"}).ui_action.seen == [("LIST",)]
