"""Long-poll GET /command: Traktor gets a command the moment it is queued.

The D2 bridge used to ask for a command every 100 ms. With ``?wait=N`` the
server holds the request until a command is queued (or N seconds pass);
without it — D2 files older than v6 — it answers at once, as before.
"""

from __future__ import annotations

import json
import socket
import threading
import time
import urllib.request

import pytest

pytest.importorskip("PySide6.QtCore")

from PySide6.QtCore import QCoreApplication

from workers.webhook import (
    _LONG_POLL_MAX_S,
    WebhookWatcher,
    _long_poll_wait,
    _make_handler,
    _ReuseServer,
)


@pytest.fixture(scope="module")
def qapp():
    yield QCoreApplication.instance() or QCoreApplication([])


@pytest.fixture
def served(qapp):
    watcher = WebhookWatcher(port=0)
    srv = _ReuseServer(("127.0.0.1", 0), _make_handler(watcher))
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    time.sleep(0.05)
    yield watcher, srv.server_address[1]
    watcher.stop()
    srv.shutdown()
    srv.server_close()
    thread.join(timeout=2)


def _command(port: int, query: str = "", timeout: float = 5.0):
    url = f"http://127.0.0.1:{port}/command" + (f"?{query}" if query else "")
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read())


def test_a_waiting_command_is_answered_at_once(served) -> None:
    watcher, port = served
    watcher.queue_play("A")
    t0 = time.monotonic()
    got = _command(port, "wait=10")
    assert got["action"] == "play" and time.monotonic() - t0 < 1.0


def test_a_held_request_gets_a_command_queued_later(served) -> None:
    watcher, port = served
    result: dict = {}

    def _ask():
        result["cmd"] = _command(port, "wait=10")
        result["arrived"] = time.monotonic()

    asker = threading.Thread(target=_ask)
    asker.start()
    time.sleep(0.6)  # the request is being held
    queued_at = time.monotonic()
    watcher.queue_stop("B")
    asker.join(timeout=5)
    assert result["cmd"]["action"] == "stop"
    # Delivered right away — not at the next poll, nor at the end of the wait.
    assert result["arrived"] - queued_at < 0.3


def test_an_idle_hold_ends_empty(served) -> None:
    _watcher, port = served
    t0 = time.monotonic()
    assert _command(port, "wait=0.6") is None
    assert 0.5 <= time.monotonic() - t0 < 2.0


def test_old_clients_without_wait_are_answered_at_once(served) -> None:
    """D2 v5 gives up on any request after 3 s: never hold it."""
    _watcher, port = served
    t0 = time.monotonic()
    assert _command(port) is None
    assert time.monotonic() - t0 < 0.5


def test_keepalive_is_served_while_a_command_request_is_held(served) -> None:
    _watcher, port = served
    holder = threading.Thread(target=lambda: _command(port, "wait=3"), daemon=True)
    holder.start()
    time.sleep(0.2)
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/keepAlive", data=b"{}", method="POST"
    )
    t0 = time.monotonic()
    with urllib.request.urlopen(request, timeout=2) as response:
        response.read()
        assert response.status == 200
    assert time.monotonic() - t0 < 1.0


def test_a_dropped_request_does_not_swallow_the_next_command(served) -> None:
    """Traktor abandons a held request at a loss, or when it switches to a
    fresh host name; the command must reach the request that replaces it."""
    watcher, port = served
    raw = socket.create_connection(("127.0.0.1", port))
    raw.sendall(b"GET /command?wait=10 HTTP/1.1\r\nHost: x\r\n\r\n")
    time.sleep(0.2)
    raw.close()  # the client goes away mid-hold
    time.sleep(0.8)  # the held thread notices
    watcher.queue_hotcue("A", 3)
    got = _command(port, "wait=5")
    assert got["action"] == "hotcue"


def test_the_wait_is_capped() -> None:
    assert _long_poll_wait("wait=600") == _LONG_POLL_MAX_S
    assert _long_poll_wait("wait=nonsense") == 0.0
    assert _long_poll_wait("") == 0.0


def test_a_lost_reply_is_put_back_first(qapp) -> None:
    watcher = WebhookWatcher(port=0)
    watcher.queue_play("A")
    watcher._requeue({"action": "stop", "deck": "B"})
    assert watcher._pop_command()["action"] == "stop"
    assert watcher._pop_command()["action"] == "play"
