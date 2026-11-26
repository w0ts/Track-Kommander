"""The Discogs client: pacing to the granted rate, retries, errors — on a fake
transport and a fake clock, so no network and no real waiting."""

from __future__ import annotations

import json

import pytest

from core import discogs_api
from core.discogs_api import DiscogsClient, DiscogsError, DiscogsStopped, Response


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, s: float) -> None:
        self.now += s


@pytest.fixture(autouse=True)
def _no_version_lookup(monkeypatch):
    monkeypatch.setattr(discogs_api, "user_agent", lambda: "TrackKommander/test")


def _ok(body: dict, limit: int = 25, remaining: int = 20) -> Response:
    headers = {"x-discogs-ratelimit": str(limit), "x-discogs-ratelimit-remaining": str(remaining)}
    return Response(200, headers, json.dumps(body).encode())


def _client(responses, token="", stop=lambda: False):
    clock = Clock()
    sent = []

    def transport(req):
        sent.append((clock.now, req))
        return responses.pop(0)

    client = DiscogsClient(token, transport, clock, clock.sleep, stop)
    return client, clock, sent


def test_requests_are_spread_evenly_over_the_minute() -> None:
    """Not a minute's worth at once and then a long wait: 60 / 25 s apart."""
    client, _clock, sent = _client([_ok({"results": []}, remaining=25) for _ in range(4)])
    for _ in range(4):
        client.search(q="x")
    gaps = [b - a for (a, _r), (b, _s) in zip(sent, sent[1:], strict=False)]
    assert gaps == [pytest.approx(60 / 25)] * 3


def test_a_token_is_sent_and_its_rate_used() -> None:
    client, _clock, sent = _client([_ok({"results": []}, limit=60, remaining=59)], token="t0k")
    client.search(q="x")
    assert sent[0][1].get_header("Authorization") == "Discogs token=t0k"
    assert client.per_minute == 60


def test_threads_share_one_allowance() -> None:
    """Two clients on one limiter: the window counts both."""
    clock = Clock()
    limiter = discogs_api.RateLimiter(3, clock, clock.sleep)
    sent = []

    def transport(req):
        sent.append(clock.now)
        return Response(200, {}, b"{}")  # no rate headers: the limiter's own count

    a = DiscogsClient("", transport, clock, clock.sleep, limiter=limiter)
    b = DiscogsClient("", transport, clock, clock.sleep, limiter=limiter)
    a.release(1)
    b.release(1)  # 60 / 3 s after a's: the spacing counts both clients
    a.release(1)  # third start inside the window: 3 granted, 2 allowed
    assert sent == [0.0, pytest.approx(20.0), pytest.approx(60.0)]


def test_the_server_count_wins_over_ours() -> None:
    """A run that ended a moment ago used the window: the first answer says
    so, and the limiter waits instead of drawing a 429."""
    client, _clock, sent = _client([_ok({}, remaining=1), _ok({}), _ok({})])
    client.release(1)
    client.release(1)
    assert sent[1][0] == pytest.approx(60.0)


def test_nearly_out_of_requests_slows_down() -> None:
    client, _clock, sent = _client([_ok({}, remaining=0), _ok({})])
    client.release(1)
    client.release(1)
    assert sent[1][0] - sent[0][0] == pytest.approx(60.0)


def test_a_429_waits_a_minute_and_retries() -> None:
    client, _clock, sent = _client([Response(429, {}, b""), _ok({"id": 5})])
    assert client.release(5) == {"id": 5}
    assert sent[1][0] - sent[0][0] >= 60


def test_a_server_error_is_retried_once() -> None:
    client, _clock, _sent = _client([Response(500, {}, b""), _ok({"id": 5})])
    assert client.release(5) == {"id": 5}
    client, _clock, _sent = _client([Response(500, {}, b""), Response(502, {}, b"")])
    with pytest.raises(DiscogsError) as exc:
        client.release(5)
    assert exc.value.status == 502


def test_a_refused_token_says_so() -> None:
    client, _clock, _sent = _client([Response(401, {}, b"")], token="bad")
    with pytest.raises(DiscogsError, match="token"):
        client.release(1)


def test_stop_interrupts_the_wait() -> None:
    stopped = {"now": False}
    client, clock, _sent = _client([_ok({})], stop=lambda: stopped["now"])
    client.release(1)
    stopped["now"] = True
    with pytest.raises(DiscogsStopped):
        client.release(1)
    assert clock.now < 1.0  # did not sit out the spacing first


def test_search_sends_only_the_given_fields() -> None:
    client, _clock, sent = _client([_ok({"results": [{"id": 1}]})])
    assert client.search(artist="Aero", track="Come Again", release_title="") == [{"id": 1}]
    url = sent[0][1].full_url
    assert "artist=Aero" in url and "track=Come+Again" in url and "release_title" not in url
    assert "type=release" in url


def test_four_429s_in_a_row_give_up_as_rate_limited() -> None:
    client, _clock, sent = _client([Response(429, {}, b"") for _ in range(4)])
    with pytest.raises(DiscogsError) as exc:
        client.release(1)
    assert exc.value.status == 429 and len(sent) == 4


def test_a_server_error_after_429s_reports_its_own_status() -> None:
    responses = [Response(429, {}, b"")] * 3 + [Response(503, {}, b""), Response(503, {}, b"")]
    client, _clock, _sent = _client(list(responses))
    with pytest.raises(DiscogsError) as exc:
        client.release(1)
    assert exc.value.status == 503
