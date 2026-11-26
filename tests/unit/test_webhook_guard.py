"""Who the bridge's server will listen to, and how much.

It is bound to loopback, which keeps the network out. It does not keep out a
web page: a page in the user's browser can send requests to 127.0.0.1 as well
as Traktor can, and the server used to take them — forged deck state, and
``GET /command`` taking commands off the queue meant for Traktor.

What a browser cannot help doing is say where it is coming from. Traktor's
bridge names a loopback host and nothing else; a page names its own origin,
and a rebound DNS name names itself.
"""

from __future__ import annotations

import http.client
import json
import threading
import time

import pytest

from workers.webhook import _make_handler, _ReuseServer
from workers.webhook_guard import (
    MAX_BODY_BYTES,
    body_length,
    clean_for_log,
    is_deck,
    is_layout_name,
    is_loopback_host,
    sent_by_a_web_page,
)

# ── Which hosts are us ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "host",
    [
        "127.0.0.1",
        "127.0.0.1:12345",
        "localhost",
        "localhost:12345",
        "LOCALHOST:12345",
        "[::1]",
        "[::1]:12345",
        "tk1.localhost:12345",
        "tk27.localhost",  # the bridge's fresh hosts
        None,
        "",  # HTTP/1.0: no Host at all
    ],
)
def test_loopback_names_are_ours(host) -> None:
    assert is_loopback_host(host)


@pytest.mark.parametrize(
    "host",
    [
        "evil.example",
        "evil.example:12345",
        "127.0.0.1.evil.example",
        "localhost.evil.example:12345",
        "notlocalhost",
        "192.168.1.10:12345",
        "[2001:db8::1]:12345",
        "127.0.0.1:12345@evil.example",
        "evil.example/127.0.0.1",
    ],
)
def test_anything_else_is_not(host: str) -> None:
    assert not is_loopback_host(host)


# ── What only a browser sends ───────────────────────────────────────────────


@pytest.mark.parametrize(
    "headers",
    [
        {"Origin": "https://evil.example"},
        {"Origin": "http://evil.example:8080"},
        {"Origin": "null"},  # a sandboxed frame
        {"Sec-Fetch-Site": "cross-site"},
        {"Sec-Fetch-Site": "same-site"},
        {"Origin": "http://127.0.0.1:12345", "Sec-Fetch-Site": "cross-site"},
    ],
)
def test_a_page_gives_itself_away(headers: dict) -> None:
    assert sent_by_a_web_page(headers)


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"User-Agent": "Mozilla/5.0"},  # Qt sends one of these too
        {"Content-Type": "application/json"},
    ],
)
def test_the_bridge_does_not(headers: dict) -> None:
    assert not sent_by_a_web_page(headers)


# ── How much will be read ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "length"),
    [
        (None, 0),
        ("0", 0),
        ("17", 17),
        (" 17 ", 17),
        (str(MAX_BODY_BYTES), MAX_BODY_BYTES),
    ],
)
def test_a_sane_length_is_taken(raw, length: int) -> None:
    assert body_length(raw) == length


@pytest.mark.parametrize("raw", ["-1", "lots", "1e9", "", "12 34", "0x10"])
def test_a_length_that_is_not_one_is_refused(raw: str) -> None:
    with pytest.raises(ValueError):
        body_length(raw)


def test_a_length_over_the_limit_is_refused() -> None:
    with pytest.raises(OverflowError):
        body_length(str(MAX_BODY_BYTES + 1))


# ── What the rest of the app is handed ──────────────────────────────────────


@pytest.mark.parametrize("deck", ["A", "B", "C", "D"])
def test_the_four_decks_are_decks(deck: str) -> None:
    assert is_deck(deck)


@pytest.mark.parametrize("deck", ["", "E", "AA", "a", "1", "../x", "A\n", None, 4])
def test_nothing_else_is(deck) -> None:
    assert not is_deck(deck)


@pytest.mark.parametrize("name", ["twoDecks", "fourDecks", "layout_3", "Mixer"])
def test_a_layout_name_is_a_short_word(name: str) -> None:
    assert is_layout_name(name)


@pytest.mark.parametrize("name", ["", "two decks", "x" * 65, "a\nb", "a=b", 7, None, ["twoDecks"]])
def test_anything_else_is_not_a_layout(name) -> None:
    assert not is_layout_name(name)


def test_text_for_the_log_stays_on_one_line() -> None:
    forged = "Title\n2026-09-29 12:00:00 ERROR something alarming"
    assert "\n" not in clean_for_log(forged)
    assert "\r" not in clean_for_log("a\rb")


def test_text_for_the_log_has_a_length() -> None:
    assert len(clean_for_log("x" * 5000)) <= 121
    assert clean_for_log("x" * 5000).endswith("…")


def test_ordinary_text_is_logged_as_it_is() -> None:
    assert clean_for_log("Motörhead – Ace of Spades") == "Motörhead – Ace of Spades"
    assert clean_for_log(None) == ""


# ── The server, asked for real ──────────────────────────────────────────────


class _FakeWatcher:
    def __init__(self) -> None:
        self.posts: list[tuple[str, str]] = []
        self.command: dict | None = {"action": "play", "deck": "A"}
        self.taken = 0
        self._arrived = threading.Condition()

    def _handle_post(self, path: str, body: str) -> None:
        with self._arrived:
            self.posts.append((path, body))
            self._arrived.notify_all()

    def _pop_command(self) -> dict | None:
        self.taken += 1
        return self.command

    def wait_for_posts(self, count: int, timeout: float = 2.0) -> bool:
        """The server answers 200 before it hands the post on."""
        with self._arrived:
            return self._arrived.wait_for(lambda: len(self.posts) >= count, timeout)


@pytest.fixture
def server():
    watcher = _FakeWatcher()
    srv = _ReuseServer(("127.0.0.1", 0), _make_handler(watcher))
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    time.sleep(0.1)
    yield watcher, srv.server_address[1]
    srv.shutdown()
    srv.server_close()
    thread.join(timeout=2)


def _ask(
    port: int, method: str, path: str, *, body: bytes | None = None, headers: dict | None = None
) -> int:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
    try:
        conn.request(method, path, body=body, headers=headers or {})
        response = conn.getresponse()
        response.read()
        return response.status
    finally:
        conn.close()


def test_the_bridge_is_served(server) -> None:
    watcher, port = server
    body = json.dumps({"play": True}).encode()

    assert _ask(port, "POST", "/updateDeck/A", body=body) == 200
    assert _ask(port, "GET", "/command") == 200
    assert (
        _ask(port, "POST", "/updateDeck/A", body=body, headers={"Host": f"tk3.localhost:{port}"})
        == 200
    )

    assert watcher.wait_for_posts(2)
    assert len(watcher.posts) == 2 and watcher.taken == 1


def test_a_rebound_name_is_refused(server) -> None:
    watcher, port = server
    headers = {"Host": f"evil.example:{port}"}

    assert _ask(port, "GET", "/command", headers=headers) == 403
    assert _ask(port, "POST", "/updateDeck/A", body=b"{}", headers=headers) == 403

    assert watcher.taken == 0, "a command was taken off the queue for it"
    assert watcher.posts == []


def test_a_web_page_is_refused(server) -> None:
    watcher, port = server
    page = {"Origin": "https://evil.example", "Content-Type": "text/plain"}

    assert _ask(port, "POST", "/deckLoaded/A", body=b'{"title": "x"}', headers=page) == 403
    assert _ask(port, "GET", "/command", headers={"Sec-Fetch-Site": "cross-site"}) == 403

    assert watcher.posts == [] and watcher.taken == 0


def test_a_body_over_the_limit_is_refused_unread(server) -> None:
    watcher, port = server
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
    try:
        # Promise far more than is sent: a server that believed the promise
        # would sit reading until its timeout.
        conn.putrequest("POST", "/keepAlive")
        conn.putheader("Content-Length", str(MAX_BODY_BYTES + 1))
        conn.endheaders()
        started = time.monotonic()
        status = conn.getresponse().status
        waited = time.monotonic() - started
    finally:
        conn.close()

    assert status == 413
    assert waited < 1.0
    assert watcher.posts == []


@pytest.mark.parametrize("length", ["-1", "lots"])
def test_a_length_that_is_not_one_is_a_bad_request(server, length: str) -> None:
    watcher, port = server
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
    try:
        conn.putrequest("POST", "/keepAlive")
        conn.putheader("Content-Length", length)
        conn.endheaders()
        status = conn.getresponse().status
    finally:
        conn.close()

    assert status == 400
    assert watcher.posts == []
