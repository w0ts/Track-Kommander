"""A small, polite Discogs API client: search and release lookups.

Discogs allows 25 requests a minute without a token and 60 with one (a free
personal access token from discogs.com/settings/developers). Every response
says how many are left in the current minute (``X-Discogs-Ratelimit-*``).
One :class:`RateLimiter` holds that allowance for every thread of a lookup
run, follows the server's count, and makes everyone wait out a 429 instead
of failing tracks. Measured 2026-10-03 without a token: the server counts a
moving 60 s window, and a run gets about 18-20 requests a minute through.

Stdlib only (urllib), certifi for certificates as :mod:`core.update_check`
does, and the transport is injectable so tests run without the network.
"""

from __future__ import annotations

import http.client
import json
import logging
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass

log = logging.getLogger(__name__)

API = "https://api.discogs.com"
_TIMEOUT_S = 20
#: Requests per minute Discogs grants without / with a token. The response
#: headers override these once the first answer is in.
ANONYMOUS_PER_MIN = 25
TOKEN_PER_MIN = 60
#: A 429 says the minute is used up; wait one out, a few times at most.
_RETRY_429 = 3
_WAIT_429_S = 61.0
_WAIT_5XX_S = 3.0


class DiscogsError(Exception):
    """A request Discogs refused or could not answer. ``status`` is the HTTP
    code, 0 for a network failure."""

    def __init__(self, message: str, status: int = 0) -> None:
        super().__init__(message)
        self.status = status


class DiscogsStopped(DiscogsError):
    """The user pressed Stop while a request was waiting its turn."""


@dataclass(frozen=True)
class Response:
    status: int
    headers: Mapping[str, str]
    body: bytes


Transport = Callable[[urllib.request.Request], Response]


def _ssl_context() -> ssl.SSLContext:
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def urllib_transport(req: urllib.request.Request) -> Response:
    """The real network. An HTTP error comes back as a Response, not raised,
    so the client sees its status and rate-limit headers."""
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_S, context=_ssl_context()) as resp:
            return Response(
                resp.status, {k.lower(): v for k, v in resp.headers.items()}, resp.read()
            )
    except urllib.error.HTTPError as exc:
        headers = {k.lower(): v for k, v in (exc.headers or {}).items()}
        return Response(exc.code, headers, exc.read() or b"")
    except (urllib.error.URLError, http.client.HTTPException, TimeoutError, OSError) as exc:
        # HTTPException: a truncated body (IncompleteRead) or a garbled status
        # line — not OSErrors, and just as much "the network failed".
        raise DiscogsError(f"network: {exc}") from exc


def user_agent() -> str:
    """Discogs asks for a name and version it can contact, not a library's."""
    from core.log_bundle import app_version

    return f"TrackKommander/{app_version()} +https://github.com/w0ts/Track-Kommander"


class RateLimiter:
    """The requests Discogs grants per minute, shared by every thread asking.

    Discogs counts requests over a moving 60-second window. The limiter lets
    requests start evenly, 60 / grant seconds apart, and never more than the
    grant (less one, for safety) in any 60 s — so several threads keep the
    allowance busy while each waits on the network, instead of one thread
    idling through every round trip.

    The server is the authority: every answer says how many are left, and if
    that is fewer than the limiter counted (a run that ended a moment ago,
    another copy of the app), the window is filled up to match. A 429 pauses
    everyone.
    """

    WINDOW_S = 60.0

    def __init__(
        self,
        per_minute: int,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.per_minute = per_minute
        self._clock = clock
        self._sleep = sleep
        self._starts: deque[float] = deque()
        self._paused_until = 0.0
        self._last_start = float("-inf")
        self._lock = threading.Lock()

    def _wait_s(self, now: float) -> float:
        while self._starts and self._starts[0] <= now - self.WINDOW_S:
            self._starts.popleft()
        if now < self._paused_until:
            return self._paused_until - now
        if len(self._starts) >= max(1, self.per_minute - 1):
            return self._starts[0] + self.WINDOW_S - now
        # Evenly spread, not in bursts: the same requests a minute, but the
        # progress moves steadily instead of a minute's worth in the first
        # seconds and then a long, dead-looking wait.
        return self._last_start + self.WINDOW_S / self.per_minute - now

    def acquire(self, should_stop: Callable[[], bool] = lambda: False) -> bool:
        """Wait for a free slot and take it; False if *should_stop* says stop
        first. Sleeps in short steps so Stop is prompt."""
        while True:
            with self._lock:
                now = self._clock()
                wait = self._wait_s(now)
                if wait <= 0:
                    self._starts.append(now)
                    self._last_start = now
                    return True
            if should_stop():
                return False
            self._sleep(min(wait, 0.5))

    def learn(self, limit: int, remaining: int) -> None:
        """What the server just said: its limit, and how many are left."""
        with self._lock:
            if limit > 0:
                self.per_minute = limit
            if remaining >= 0:
                now = self._clock()
                self._wait_s(now)  # drop what left the window
                missing = (self.per_minute - remaining) - len(self._starts)
                self._starts.extend([now] * max(0, missing))

    def pause(self, seconds: float) -> None:
        with self._lock:
            self._paused_until = max(self._paused_until, self._clock() + seconds)


class DiscogsClient:
    """Calls to the Discogs API, paced by a :class:`RateLimiter`. A client is
    used by one thread; threads that share an allowance share the limiter."""

    def __init__(
        self,
        token: str = "",
        transport: Transport = urllib_transport,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        should_stop: Callable[[], bool] = lambda: False,
        limiter: RateLimiter | None = None,
    ) -> None:
        self._token = token.strip()
        self._transport = transport
        self._should_stop = should_stop
        self._clock = clock
        self._sleep = sleep
        self.limiter = limiter or RateLimiter(
            TOKEN_PER_MIN if self._token else ANONYMOUS_PER_MIN, clock, sleep
        )

    @property
    def authenticated(self) -> bool:
        return bool(self._token)

    @property
    def per_minute(self) -> int:
        return self.limiter.per_minute

    def search(self, **params: str) -> list[dict]:
        """``/database/search`` results (first page, up to 50)."""
        query = {k: v for k, v in params.items() if v}
        query.setdefault("type", "release")
        query.setdefault("per_page", "50")
        data = self._get("/database/search?" + urllib.parse.urlencode(query))
        results = data.get("results")
        return results if isinstance(results, list) else []

    def release(self, release_id: int) -> dict:
        return self._get(f"/releases/{int(release_id)}")

    def check(self) -> str:
        """Who the token belongs to — the settings page's "Test" button."""
        if not self._token:
            raise DiscogsError("no token", 401)
        return str(self._get("/oauth/identity").get("username", ""))

    # ── plumbing ─────────────────────────────────────────────────────────────

    def _learn_limit(self, headers: Mapping[str, str]) -> None:
        try:
            limit = int(headers.get("x-discogs-ratelimit", "") or 0)
            remaining = int(headers.get("x-discogs-ratelimit-remaining", "") or -1)
        except ValueError:
            return
        self.limiter.learn(limit, remaining)

    def _wait(self, seconds: float) -> None:
        """This thread only (a server hiccup), in steps so Stop is prompt."""
        end = self._clock() + seconds
        while (left := end - self._clock()) > 0 and not self._should_stop():
            self._sleep(min(left, 0.5))

    def _get(self, path: str) -> dict:
        headers = {"User-Agent": user_agent(), "Accept": "application/json"}
        if self._token:
            headers["Authorization"] = f"Discogs token={self._token}"
        waited_429 = 0
        server_retried = False
        while True:
            if not self.limiter.acquire(self._should_stop) or self._should_stop():
                raise DiscogsStopped("stopped")
            req = urllib.request.Request(API + path, headers=headers)
            resp = self._transport(req)
            self._learn_limit(resp.headers)
            if resp.status == 429:
                if waited_429 >= _RETRY_429:
                    raise DiscogsError("rate limited", 429)
                waited_429 += 1
                log.info("discogs: rate limited, every lookup waits a minute")
                self.limiter.pause(_WAIT_429_S)
                continue
            if resp.status >= 500 and not server_retried:
                # Discogs answers the odd request with a 500; the same one a
                # few seconds later usually works.
                server_retried = True
                self._wait(_WAIT_5XX_S)
                continue
            if resp.status == 401:
                raise DiscogsError("Discogs refused the token", 401)
            if resp.status == 404:
                raise DiscogsError("not found", 404)
            if resp.status != 200:
                raise DiscogsError(f"HTTP {resp.status}", resp.status)
            try:
                data = json.loads(resp.body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise DiscogsError(f"unreadable answer: {exc}", resp.status) from exc
            if not isinstance(data, dict):
                raise DiscogsError("unexpected answer", resp.status)
            return data
