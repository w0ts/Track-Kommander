"""The bridge's HTTP server: the sockets, and the handler for one request.

Loopback only. What a request has to look like to be served at all is in
:mod:`workers.webhook_guard`; what a post means is in
:mod:`workers.webhook_posts`.
"""

from __future__ import annotations

import http.server
import json
import logging
import select
import socket
import socketserver
import sys
import urllib.parse

from core.runtime import in_traktor
from workers.webhook_guard import (
    body_length,
    clean_for_log,
    is_loopback_host,
    sent_by_a_web_page,
)

log = logging.getLogger("webhook")
request_log = logging.getLogger("webhook.requests")


_WINDOWS = sys.platform == "win32"

#: How long a request may sit half-sent before its connection is dropped.
#: The QML posts every second, so anything quiet for this long is not a slow
#: client — it is one that went away.
REQUEST_TIMEOUT_S = 10


class _ReuseServer(socketserver.ThreadingTCPServer):
    """Threaded on purpose. See item 11.

    This used to be a plain ``TCPServer``, which serves one request at a
    time. ``do_POST`` trusts ``Content-Length`` and blocks in ``rfile.read``,
    so a client that promised a body and never sent it — a Traktor process
    killed mid-request — blocked the accept loop **permanently**. The socket
    stayed bound, the log said "webhook bound" and the UI showed no error,
    but nothing was ever served again; only restarting Kommander cleared it.
    That is the reported "works in one order, not the reverse".

    Reproduced in ``tests/unit/test_webhook_server.py``: one silent client,
    single threaded, and every later request times out.
    """

    # SO_REUSEADDR on Unix only bypasses TIME_WAIT, so we can restart the
    # server quickly after a crash without port-sharing concerns. On Windows
    # the same option means something else — see server_bind.
    allow_reuse_address = not _WINDOWS

    #: Never outlive the app — an abandoned handler must not hold it open.
    daemon_threads = True

    def server_bind(self) -> None:
        """Bind, and on Windows keep the port to ourselves.

        There SO_REUSEADDR lets a second program bind a port that is already
        in use and take its connections, so it is left off and the port is
        claimed exclusively. Not run on a Windows machine yet: the option is
        documented by Microsoft, and a bind that fails is retried by the
        watcher like any other.
        """
        exclusive = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
        if _WINDOWS and exclusive is not None:
            self.socket.setsockopt(socket.SOL_SOCKET, exclusive, 1)
        super().server_bind()

    #: The listen backlog. socketserver's default of 5 is thin for a client
    #: that opens a connection every 100 ms: a short burst overflows it and
    #: the kernel refuses the extras, which the QML sees as "no connection"
    #: and the user sees as the bridge flickering. Measured: 20 simultaneous
    #: connections lost 6 at the default, none at this.
    request_queue_size = 128

    def handle_error(self, request, client_address) -> None:
        """A client going away is routine, not an error.

        socketserver's default dumps a traceback to stderr. At 11 requests a
        second from the QML, a Traktor that quits mid-poll would bury the log
        in tracebacks for something that is not a fault.
        """
        exc = sys.exc_info()[1]
        if isinstance(exc, (TimeoutError, ConnectionError, BrokenPipeError)):
            log.debug("dropped connection from %s: %s", client_address, exc)
            return
        log.warning("webhook request from %s failed: %s", client_address, exc, exc_info=True)


class _ReuseServer6(_ReuseServer):
    """The same server on the IPv6 loopback address."""

    address_family = socket.AF_INET6


def bind_ipv6_loopback(port: int, handler) -> _ReuseServer6 | None:
    """A server on ``[::1]:port``, or None when IPv6 loopback is unavailable.

    The bridge's own recovery needs it. After a hiccup the QML probes via
    ``tkN.localhost`` for a Qt connection that has never failed, and macOS
    resolves those names to ``::1`` first. Traktor's Qt does not fall back
    to 127.0.0.1, so with the app on IPv4 alone every probe was refused and
    the bridge stayed dead until something was restarted. Measured
    2026-09-25: a relay on ``[::1]:12345`` got Traktor's probes within two
    seconds and the bridge resynced.

    ``::1`` only — never ``::``, which would open the bridge to the network.
    Failing to bind is not fatal: the IPv4 listener still serves the
    plain-loopback case.
    """
    try:
        return _ReuseServer6(("::1", port), handler)
    except OSError as exc:
        log.warning("no IPv6 loopback listener on port %d: %s", port, exc)
        return None


#: Long enough for a loopback round trip, short enough that a wedged
#: listener cannot hold up startup.
_PROBE_TIMEOUT_S = 0.5
# How often a serve_forever loop checks for shutdown. socketserver's 0.5 s
# default held quit for up to 0.5 s per server (the UI watchdog saw both).
_SHUTDOWN_POLL_S = 0.05

#: Deliberately a path we do **not** serve. ``/command`` would identify us
#: precisely and also pop a queued command off the other instance, stealing
#: work from the very server we are only trying to identify. Our 404 is
#: recognisable enough.
_PROBE_PATH = "/.track-kommander-probe"

#: What ``http.server`` puts in its ``Server:`` header. Not proof, but on a
#: port that is ours by convention it is the right guess to hand the user.
_OUR_SERVER_HEADER = "BaseHTTP"


def port_holder(port: int, host: str = "127.0.0.1") -> str | None:
    """Who is already serving on *port*, or None if nobody is.

    Binding cannot answer this. ``allow_reuse_address`` exists so a restart
    after a crash is not blocked by TIME_WAIT, and the price is that a bind
    can succeed while another process goes on receiving every request — so
    "listening" proves nothing on its own. A connection does: if something
    accepts, the port is taken, whoever owns it.

    Distinguishes our own kind from a stranger, because the fix differs. A
    second Track Kommander means "close the other one"; anything else means
    "pick another port".
    """
    try:
        with socket.create_connection((host, port), timeout=_PROBE_TIMEOUT_S) as sock:
            sock.settimeout(_PROBE_TIMEOUT_S)
            try:
                sock.sendall(f"GET {_PROBE_PATH} HTTP/1.0\r\nHost: {host}\r\n\r\n".encode())
                reply = sock.recv(256).decode("utf-8", "replace")
            except OSError:
                # Accepted the connection and then said nothing useful. Still
                # taken — that is the part that matters.
                return "another program"
    except (TimeoutError, OSError):
        return None  # nothing listening: the port is ours to take

    if _OUR_SERVER_HEADER in reply:
        return "another copy of Track Kommander"
    return "another program"


#: Longest a long-poll /command request is held, whatever the client asks.
#: The client's own timeout for it is longer (ApiClient.js LONG_POLL_TIMEOUT_MS).
_LONG_POLL_MAX_S = 15.0

#: How often a held request checks its client is still connected.
_LONG_POLL_SLICE_S = 0.5


def _long_poll_wait(query: str) -> float:
    """Seconds to hold a /command request: ``wait=N`` in *query*, capped;
    0 without it (answer at once, as for D2 files older than v6)."""
    try:
        raw = urllib.parse.parse_qs(query).get("wait", ["0"])[0]
        return max(0.0, min(float(raw), _LONG_POLL_MAX_S))
    except (TypeError, ValueError):
        return 0.0


def _client_gone(sock) -> bool:
    """True once the peer has closed *sock* (readable, with nothing to read).

    A client that sends nothing more after its GET only becomes readable by
    closing; a held request whose client left must not take a command.
    """
    try:
        readable, _, _ = select.select([sock], [], [], 0)
        if not readable:
            return False
        return sock.recv(1, socket.MSG_PEEK) == b""
    except (OSError, ValueError):
        return True


#: Reasons a request was refused that have already been logged in full.
_refusals_logged: set[tuple[int, str]] = set()


def _log_refusal(status: int, why: str, method: str | None, path: str | None) -> None:
    """Say why a request was refused: loudly the first time, quietly after.

    Loudly, because if this ever refuses Traktor's own bridge the log has to
    say so in words. Quietly after, because a page that keeps trying must not
    be able to fill it.
    """
    first = (status, why) not in _refusals_logged
    _refusals_logged.add((status, why))
    log.log(
        logging.WARNING if first else logging.DEBUG,
        "refused %s %s with %d: %s",
        method,
        clean_for_log(path),
        status,
        why,
    )


def _make_handler(watcher) -> type[http.server.BaseHTTPRequestHandler]:
    """The request handler, bound to one watcher.

    A factory rather than a closure inside :meth:`WebhookWatcher.run` so a
    test can stand a real server up against a fake watcher — which is what
    made the wedging in item 11 reproducible instead of theoretical.
    """

    class Handler(http.server.BaseHTTPRequestHandler):
        #: Drop a connection that stops talking. Threading alone stops one
        #: stuck client blocking the others, but without this each abandoned
        #: connection would hold a thread for the life of the app — and the
        #: QML opens one every 100 ms.
        timeout = REQUEST_TIMEOUT_S

        def _refuse(self, status: int, why: str) -> None:
            """Answer *status* and hang up, without reading what was sent."""
            _log_refusal(status, why, self.command, self.path)
            self.close_connection = True
            self.send_response(status)
            self.send_header("Content-Length", "0")
            self.send_header("Connection", "close")
            self.end_headers()

        def _from_the_bridge(self) -> bool:
            """Is this request one to serve? Answers it if it is not.

            Before anything is read or taken off the queue: see
            :mod:`workers.webhook_guard` for what is refused and why.
            """
            host = self.headers.get("Host")
            if not is_loopback_host(host):
                self._refuse(
                    403, f"asked for host {clean_for_log(host)!r}, which is not this machine"
                )
                return False
            if sent_by_a_web_page(self.headers):
                origin = self.headers.get("Origin") or self.headers.get("Sec-Fetch-Site")
                self._refuse(403, f"sent by a web page ({clean_for_log(origin)})")
                return False
            return True

        def do_POST(self):
            if not self._from_the_bridge():
                return
            try:
                length = body_length(self.headers.get("Content-Length"))
            except OverflowError as exc:
                self._refuse(413, str(exc))
                return
            except ValueError as exc:
                self._refuse(400, str(exc))
                return
            try:
                body = self.rfile.read(length).decode("utf-8", errors="replace")
            except (TimeoutError, OSError):
                # The client promised a body and stopped. Nothing to answer.
                log.debug("POST  %s  abandoned mid-body", self.path)
                return
            # Built only when traced: this runs for every post.
            if request_log.isEnabledFor(logging.DEBUG):
                request_log.debug(
                    "POST  %-30s  body=%s%s",
                    self.path,
                    body[:120].replace("\n", " "),
                    "…" if len(body) > 120 else "",
                )
            payload = b"OK"
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            # Declared explicitly: without it the client can only tell the
            # body ended when the connection closes, which is a hang on
            # anything that keeps the connection alive.
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            watcher._handle_post(self.path, body)

        def do_GET(self):
            if not self._from_the_bridge():
                return
            path, _, query = self.path.partition("?")
            if path == "/command" and in_traktor():
                # Inside Traktor commands go to Traktor directly (the native sink): a D2
                # bridge that polls here gets nothing to do (User, 2026-11-24).
                self._refuse(404, "inside Traktor: no D2 bridge")
                return
            if path == "/command":
                # ?wait=N: hold the request until a command is queued, up to
                # N seconds (long poll). Without it — D2 files older than
                # v6, whose client gives up after 3 s — answer at once.
                wait = _long_poll_wait(query)
                if wait > 0:
                    cmd = watcher._wait_command(wait, lambda: _client_gone(self.connection))
                else:
                    cmd = watcher._pop_command()
                data = json.dumps(cmd).encode()
                if cmd:
                    log.debug("GET   /command  → %s", cmd)
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    self.wfile.flush()
                except OSError:
                    if cmd:
                        log.info("GET   /command  → reply lost, %s requeued", cmd)
                        watcher._requeue(cmd)
            else:
                request_log.debug("GET   %s  → 404", self.path)
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()

        def log_message(self, *_):
            pass  # suppress default Apache-style lines; we use our own

    return Handler
