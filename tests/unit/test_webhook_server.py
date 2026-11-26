"""The webhook server must keep serving when one client misbehaves.

Item 11: "with both apps already running the connection sometimes never comes
up, and restarting them in one order works while the reverse doesn't."

The mechanism, reproduced here: the server was a plain
``socketserver.TCPServer`` — **single threaded**. ``serve_forever`` handles
one request at a time, and ``do_POST`` trusts ``Content-Length`` and blocks in
``rfile.read``. A client that opens a connection, promises a body and never
sends it blocks the accept loop *permanently*.

That is what a Traktor process killed mid-request leaves behind, and it fits
the report precisely: the socket stays bound and listening, so the log says
"webhook bound" and the UI shows no error, but nothing is served again. Only
restarting Kommander — which rebinds — clears it. Restarting Traktor does not.

Measured before the fix::

    TCPServer           before: served 200 | one silent client: FAILED | again: FAILED
    ThreadingTCPServer  before: served 200 | one silent client: served | again: served

The QML hits this with ``GET /command`` every 100 ms and ``POST /keepAlive``
every second — 11 requests a second — so a single-threaded server was thin
even without a stuck client.
"""

from __future__ import annotations

import json
import socket
import socketserver
import threading
import time
import urllib.error
import urllib.request

import pytest

from workers.webhook import _make_handler, _ReuseServer


class _FakeWatcher:
    """Only what the handler touches."""

    def __init__(self) -> None:
        self.posts: list[tuple[str, str]] = []
        self.command: dict | None = None
        self._arrived = threading.Condition()

    def _handle_post(self, path: str, body: str) -> None:
        with self._arrived:
            self.posts.append((path, body))
            self._arrived.notify_all()

    def _pop_command(self) -> dict | None:
        return self.command

    def wait_for_posts(self, count: int, timeout: float = 2.0) -> bool:
        """The server answers 200 *before* it hands the post on, so a test
        that looks as soon as it has the answer can look too early."""
        with self._arrived:
            return self._arrived.wait_for(lambda: len(self.posts) >= count, timeout)


@pytest.fixture
def server():
    watcher = _FakeWatcher()
    srv = _ReuseServer(("127.0.0.1", 0), _make_handler(watcher))
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    time.sleep(0.1)
    yield srv, watcher, srv.server_address[1]
    srv.shutdown()
    srv.server_close()
    thread.join(timeout=2)


def _get(port: int, path: str = "/command", timeout: float = 2.0) -> int:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=timeout) as response:
        response.read()
        return response.status


def _post(port: int, path: str, body: bytes, timeout: float = 2.0) -> int:
    request = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=body, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        response.read()
        return response.status


# ── The regression ─────────────────────────────────────────────────────────


def test_one_silent_client_does_not_stop_the_server(server) -> None:
    """The whole of item 11, as a test."""
    _srv, _watcher, port = server
    assert _get(port) == 200, "healthy to begin with"

    # Connect, promise a body, send none — a process that died mid-request.
    stuck = socket.create_connection(("127.0.0.1", port))
    try:
        stuck.sendall(b"POST /keepAlive HTTP/1.1\r\nHost: x\r\nContent-Length: 5000\r\n\r\n")
        time.sleep(0.3)

        assert _get(port) == 200, "a stuck client must not wedge the server"
        assert _get(port) == 200, "and it must stay unwedged"
    finally:
        stuck.close()


def test_several_stuck_clients_still_do_not_stop_it(server) -> None:
    _srv, _watcher, port = server
    stuck = []
    try:
        for _ in range(5):
            s = socket.create_connection(("127.0.0.1", port))
            s.sendall(b"POST /keepAlive HTTP/1.1\r\nHost: x\r\nContent-Length: 5000\r\n\r\n")
            stuck.append(s)
        time.sleep(0.3)
        assert _get(port) == 200
    finally:
        for s in stuck:
            s.close()


def test_the_server_is_threaded() -> None:
    """Stated as a property, so it cannot regress back to single-threaded."""
    assert issubclass(_ReuseServer, socketserver.ThreadingMixIn)
    assert _ReuseServer.daemon_threads, "threads must not outlive the app"


def test_a_stuck_connection_is_eventually_dropped(server) -> None:
    """Threading alone would leak a thread per abandoned connection, one a
    second forever. A socket timeout is what actually reclaims them."""
    _srv, _watcher, port = server
    handler = _make_handler(_FakeWatcher())
    assert handler.timeout is not None and handler.timeout > 0


# ── Still doing its job ────────────────────────────────────────────────────


def test_concurrent_requests_are_all_served(server) -> None:
    """11 requests a second in real use, so overlap is the normal case."""
    _srv, _watcher, port = server
    results: list[int] = []
    lock = threading.Lock()

    def hit() -> None:
        try:
            status = _get(port)
        except (urllib.error.URLError, OSError):
            status = 0
        with lock:
            results.append(status)

    threads = [threading.Thread(target=hit) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)
    assert results.count(200) == 20, results


def test_a_post_reaches_the_watcher(server) -> None:
    _srv, watcher, port = server
    assert _post(port, "/keepAlive", b'{"version": "1"}') == 200
    assert watcher.wait_for_posts(1)
    assert watcher.posts[0][0] == "/keepAlive"
    assert '"version"' in watcher.posts[0][1]


def test_a_command_is_delivered(server) -> None:
    _srv, watcher, port = server
    watcher.command = {"action": "resync"}
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/command", timeout=2) as response:
        assert json.loads(response.read())["action"] == "resync"


def test_the_post_response_declares_its_length(server) -> None:
    """Without Content-Length the client can only tell the body ended when
    the connection closes — fine on HTTP/1.0, a hang on anything keep-alive."""
    _srv, _watcher, port = server
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/keepAlive", data=b"{}", method="POST"
    )
    with urllib.request.urlopen(request, timeout=2) as response:
        assert response.headers.get("Content-Length") is not None


def test_an_unknown_path_is_404(server) -> None:
    _srv, _watcher, port = server
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        _get(port, "/nope")
    assert excinfo.value.code == 404


def test_a_dropped_connection_does_not_print_a_traceback(server, capfd) -> None:
    """socketserver's default handle_error dumps a traceback to stderr. A
    client going away is routine — at 11 requests a second it would bury the
    log in noise for something that is not a fault."""
    _srv, _watcher, port = server
    stuck = socket.create_connection(("127.0.0.1", port))
    stuck.sendall(b"POST /keepAlive HTTP/1.1\r\nHost: x\r\nContent-Length: 5000\r\n\r\n")
    time.sleep(0.2)
    stuck.close()
    _get(port)
    time.sleep(0.2)

    err = capfd.readouterr().err
    assert "Traceback" not in err, err
    assert "Exception occurred" not in err, err


# ── IPv6 loopback ──────────────────────────────────────────────────────────
#
# After a hiccup the bridge probes via tkN.localhost for a fresh Qt
# connection. macOS resolves those names to ::1 first, and Traktor's Qt does
# not fall back to 127.0.0.1: with the app on IPv4 only, every probe was
# refused and the bridge stayed dead until a restart. Measured 2026-09-25: a
# relay on [::1]:12345 received Traktor's probes within two seconds and the
# bridge resynced.


def test_a_request_to_ipv6_loopback_is_served() -> None:
    from workers.webhook import bind_ipv6_loopback

    watcher = _FakeWatcher()
    srv = bind_ipv6_loopback(0, _make_handler(watcher))
    if srv is None:
        pytest.fail("this machine has IPv6 loopback; the bind must succeed")
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        port = srv.server_address[1]
        request = urllib.request.Request(
            f"http://[::1]:{port}/keepAlive", data=b"{}", method="POST"
        )
        with urllib.request.urlopen(request, timeout=2) as response:
            assert response.status == 200
        assert watcher.wait_for_posts(1)
        assert watcher.posts == [("/keepAlive", "{}")]
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=2)


def test_the_ipv6_listener_is_loopback_only() -> None:
    """Never "::" — that would put the bridge on every network interface."""
    from workers.webhook import bind_ipv6_loopback

    srv = bind_ipv6_loopback(0, _make_handler(_FakeWatcher()))
    try:
        assert srv is not None
        assert srv.server_address[0] == "::1"
    finally:
        if srv is not None:
            srv.server_close()


def test_no_ipv6_is_not_fatal(monkeypatch) -> None:
    """A Mac with IPv6 switched off still gets the IPv4 bridge."""
    import workers.webhook_server as server

    def refuse(*_args, **_kwargs):
        raise OSError("Address family not supported")

    monkeypatch.setattr(server, "_ReuseServer6", refuse)
    assert server.bind_ipv6_loopback(0, _make_handler(_FakeWatcher())) is None
