"""Refusing to claim a port another process already owns.

``allow_reuse_address`` is set so a restart after a crash is not blocked by
TIME_WAIT. The price is that a second instance *binds successfully* while the
first goes on receiving every request — so the bind cannot tell us whether we
will hear anything, and emitting "Webhook server listening on port 12345" off
the back of it is a claim the code cannot support.

That is not hypothetical. A copy running from the IDE held the port while the
installed app reported itself healthy and sat there receiving nothing; the
bridge looked broken, Traktor looked broken, and the actual fault was two
copies and one port. The comment in ``webhook.py`` had described this hazard
for a long time — nothing acted on it.

So the port is probed *before* binding: if anything accepts a connection, the
port is taken, and the user is told which of the two fixes applies.
"""

from __future__ import annotations

import http.server
import socket
import threading

import pytest

pytest.importorskip("PySide6.QtCore")

from workers.webhook import (
    _PROBE_PATH,
    WebhookWatcher,
    port_holder,
)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _Quiet(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(404)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args):
        pass


@pytest.fixture
def http_listener():
    """A real ``http.server``, which is what our own webhook is."""
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Quiet)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield srv.server_address[1]
    srv.shutdown()
    srv.server_close()


def test_a_free_port_has_no_holder() -> None:
    assert port_holder(_free_port()) is None


def test_an_http_listener_is_recognised_as_one_of_ours(http_listener) -> None:
    """Our webhook is an ``http.server``, so its Server: header names it."""
    assert port_holder(http_listener) == "another copy of Track Kommander"


def test_a_silent_listener_still_counts_as_taken() -> None:
    """Accepting and saying nothing is the ambiguous case, and it is taken.

    Whatever it is, we will not be the one receiving Traktor's posts, which
    is the only thing this check is deciding.
    """
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    try:
        assert port_holder(port) == "another program"
    finally:
        srv.close()


def test_the_probe_does_not_pop_a_queued_command() -> None:
    """It must not steal work from the instance it is identifying.

    ``/command`` would name us precisely and also dequeue a pending command
    from the running copy — a resync or a sync the user asked for, silently
    consumed by a health check.
    """
    assert _PROBE_PATH != "/command"
    assert not _PROBE_PATH.startswith("/command")


def test_a_refused_connection_is_not_reported_as_a_holder() -> None:
    """A closed port must read as free, or the app never starts at all."""
    port = _free_port()
    for _ in range(3):
        assert port_holder(port) is None


def test_the_watcher_refuses_to_claim_an_occupied_port(http_listener) -> None:
    """The whole point: no "listening" message when we are not the owner.

    Runs the real ``run()`` loop against a port somebody else holds and
    reads what the user would have been told.
    """
    watcher = WebhookWatcher(port=http_listener)
    said: list[str] = []
    watcher.status.connect(said.append)
    watcher.is_running.connect(
        lambda up: pytest.fail("announced a server it does not own") if up else None
    )

    # One pass: the loop retries forever otherwise.
    watcher.status.connect(lambda _msg: setattr(watcher, "_stop", True))
    watcher.run()

    assert said, "a refusal has to say something"
    assert not any("listening" in m.lower() for m in said), said
    assert any(str(http_listener) in m and "in use" in m for m in said), said


# ── waiting while the port is held ───────────────────────────────────────────


def test_a_held_port_is_logged_once_then_once_a_minute():
    from workers.webhook import _HeldPort

    held = _HeldPort()
    times = [0.0, 5.0, 15.0, 45.0, 59.0, 61.0, 100.0]
    logged = [held.should_log("another copy of Track Kommander", t) for t in times]
    assert logged == [True, False, False, False, False, True, False]


def test_a_different_holder_is_logged_at_once():
    from workers.webhook import _HeldPort

    held = _HeldPort()
    assert held.should_log("another copy of Track Kommander", 0.0)
    assert held.should_log("another program", 1.0)
    assert not held.should_log("another program", 2.0)


def test_the_wait_grows_to_a_limit_and_resets_when_the_port_is_free():
    from workers.webhook import _HeldPort

    held = _HeldPort()
    waits = []
    for _ in range(6):
        waits.append(held.delay_s)
        held.waited()
    assert waits == [5.0, 10.0, 20.0, 30.0, 30.0, 30.0]
    held.clear()
    assert held.delay_s == _HeldPort.FIRST_DELAY_S
    assert held.holder is None
