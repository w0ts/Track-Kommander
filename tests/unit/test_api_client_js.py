"""The QML bridge client, run for real in Node.

``ApiClient.js`` is plain ES5 with no QML dependencies beyond ``.pragma
library``, so it can be executed and its behaviour checked rather than its
source inspected. Skipped where Node is not installed.

What is pinned here is the reconnect mode and the reason for each part of it,
all measured on Traktor 4.5.1 for macOS on 2026-09-21:

* The client used to fire as if nothing had happened — 10 Hz poll, 1 Hz post,
  no notion of failure. When the app died, Qt's connection object inside
  Traktor for that host:port never issued a connection again for the life of
  the process. A gentle client — cancel once, go quiet, one probe a second —
  recovers from the app *not being there yet*.
* It does not recover from the app having *died mid-request*: probes hang
  where a refused connection would complete in milliseconds. Qt keys its
  connection cache by the host string, and every ``*.localhost`` resolves to
  loopback, so a hung probe moves probing to ``tk1.localhost`` and on. Three
  kills in a row, three reconnects. Windows (Traktor 4.4.2) recovers on its
  own, so that switch is confined to macOS.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
CLIENT = REPO / "Traktor" / "D2" / "Api" / "ApiClient.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="Node is not installed")

HARNESS = r"""
const fs = require('fs');
const vm = require('vm');

const opened = [];        // every XHR that was opened, in order
const aborted = [];
const logs = [];
const clock = { t: 1_000_000 };

class XHR {
    constructor() { this.readyState = 0; this.status = 0; }
    open(method, url) { this.method = method; this.url = url; opened.push(this); }
    setRequestHeader() {}
    send() {}
    abort() { aborted.push(this); this._finish(0); }
    respond(status, body) { this.responseText = body || ''; this._finish(status); }
    _finish(status) {
        this.status = status;
        this.readyState = 4;
        if (this.onreadystatechange) this.onreadystatechange();
    }
}

const sandbox = {
    XMLHttpRequest: XHR,
    console: { log: (m) => logs.push(String(m)) },
    JSON, Math,
    Date: { now: () => clock.t },
};
if (process.argv[4] === 'windows') sandbox.Qt = { platform: { os: 'windows' } };
if (process.argv[4] === 'osx')     sandbox.Qt = { platform: { os: 'osx' } };

const src = fs.readFileSync(process.argv[2], 'utf8')
              .split('\n').filter(l => !l.startsWith('.pragma')).join('\n');
vm.runInNewContext(src, sandbox);

const host = (xhr) => xhr.url.match(/^https?:\/\/([^/:]+)/)[1];
const path = (xhr) => xhr.url.replace(/^https?:\/\/[^/]+/, '');
const last = () => opened[opened.length - 1];
// One 100 ms poll tick, like the QML Timer.
const tick = (n) => { for (let i = 0; i < n; i++) { clock.t += 100; sandbox.get('command', () => {}); } };
// Answer every outstanding probe with "refused", as a dead port would.
const refuseProbes = () => opened.filter(x => path(x) === '/ping' && x.readyState !== 4).forEach(x => x.respond(0));

const scenario = process.argv[3];
const out = {};

if (scenario === 'connected') {
    tick(3);                                        // never answered
    out.opened = opened.map(path);
    out.host = host(opened[0]);
    opened[0].respond(200, 'null');
    tick(1);
    out.openedAfterReply = opened.length;
    out.successCount = sandbox.successCount;
}

if (scenario === 'refused_goes_quiet_then_probes') {
    tick(1); last().respond(0);
    const before = opened.length;
    tick(25);                                       // 2.5 s: inside the quiet period
    out.openedDuringQuiet = opened.length - before;
    tick(10);                                       // past 3 s
    out.probes = opened.slice(before).map(path);
    out.probeHost = host(last());
    out.logs = logs;
}

if (scenario === 'inflight_cancelled_once') {
    sandbox.get('command', () => {});
    sandbox.send('keepAlive', {a: 1});
    sandbox.get('command', () => {});               // skipped: command is pending
    out.openedBeforeLoss = opened.length;
    opened[0].respond(0);
    out.aborted = aborted.length;
    out.lostLines = logs.filter(l => /LOST|not reachable/.test(l)).length;
}

if (scenario === 'refused_probe_keeps_host') {
    tick(1); last().respond(0);
    tick(35); refuseProbes();                       // probe #1 refused at once
    tick(10); refuseProbes();                       // probe #2
    tick(10);
    const probes = opened.slice(1);
    out.probeHosts = [...new Set(probes.map(host))];
    out.probeCount = probes.length;
}

if (scenario === 'hung_probe_switches_host') {
    tick(1); last().respond(0);
    tick(35);                                       // probe #1 out, never answered
    const first = last();
    tick(35);                                       // 3.5 s later: timed out
    out.firstAborted = aborted.includes(first);
    tick(10);
    out.hosts = opened.slice(1).map(host);
    out.logs = logs;
}

if (scenario === 'windows_never_switches_host') {
    tick(1); last().respond(0);
    tick(300);                                      // 30 s of hung probes
    out.hosts = [...new Set(opened.map(host))];
}

if (scenario === 'answered_probe_restores') {
    tick(1); last().respond(0);
    tick(35);
    last().respond(404);                            // alive; path unknown
    tick(1); last().respond(200, 'null');           // normal traffic resumes
    tick(1);
    out.after = opened.slice(2).map(path);
    out.logs = logs;
}

if (scenario === 'silent_request_is_a_loss') {
    tick(1);                                        // never answered
    tick(35);                                       // 3.5 s: the loss is noticed
    out.logs = logs;
    out.aborted = aborted.length;
    tick(40);                                       // past the quiet period
    out.after = [...new Set(opened.slice(1).map(path))];
}

if (scenario === 'probe_log_volume') {
    tick(1); last().respond(0);
    for (let i = 0; i < 80; i++) { tick(10); refuseProbes(); }
    out.lines = logs.filter(l => l.includes('still reconnecting')).length;
}

// Long poll: GET /command?wait=15 with its own 20 s timeout, keepAlive at 1 Hz
// with the default 3 s one — as ApiModule.qml sends them.
const lpTick = (n, answerKeepAlive) => {
    for (let i = 0; i < n; i++) {
        clock.t += 100;
        sandbox.get('command?wait=' + sandbox.LONG_POLL_S, () => {}, sandbox.LONG_POLL_TIMEOUT_MS);
        if (i % 10 === 0) {
            sandbox.send('keepAlive', {});
            const ka = opened.filter(x => path(x) === '/keepAlive' && x.readyState !== 4);
            if (answerKeepAlive) ka.forEach(x => x.respond(200, ''));
        }
    }
};

if (scenario === 'long_poll_held_is_not_a_loss') {
    lpTick(120, true);                               // 12 s: /command held throughout
    out.commandRequests = opened.filter(x => path(x).startsWith('/command')).length;
    out.lost = logs.filter(l => /LOST/.test(l)).length;
    out.path = path(opened[0]);
}

if (scenario === 'silent_keepalive_still_a_loss') {
    lpTick(40, false);                               // keepAlive unanswered for 4 s
    out.lostLines = logs.filter(l => /LOST|not reachable/.test(l));
    out.heldCommandAborted = aborted.some(x => path(x).startsWith('/command'));
}

console.error(JSON.stringify(out));
"""


def run(scenario: str, tmp_path: Path, platform: str = "") -> dict:
    harness = tmp_path / "harness.js"
    harness.write_text(HARNESS)
    proc = subprocess.run(
        ["node", str(harness), str(CLIENT), scenario, platform],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    return json.loads(proc.stderr.strip().splitlines()[-1])


# ── Connected ────────────────────────────────────────────────────────────────


def test_connected_one_outstanding_request_per_endpoint(tmp_path) -> None:
    """A slow reply skips a tick; it does not stack a second request."""
    out = run("connected", tmp_path)
    assert out["opened"] == ["/command"]
    assert out["host"] == "127.0.0.1"
    assert out["openedAfterReply"] == 2
    assert out["successCount"] == 1


# ── Losing the app ───────────────────────────────────────────────────────────


def test_a_refused_request_goes_quiet_then_probes(tmp_path) -> None:
    """No hammering: silence for the quiet period, then one probe to a path
    the app does not serve, so nothing queued gets popped by a health check."""
    out = run("refused_goes_quiet_then_probes", tmp_path)
    assert out["openedDuringQuiet"] == 0
    assert out["probes"] == ["/ping"], out["probes"]
    assert out["probeHost"] == "127.0.0.1"
    assert any("not reachable yet" in line for line in out["logs"]), out["logs"]


def test_in_flight_requests_are_cancelled_once_at_the_loss(tmp_path) -> None:
    out = run("inflight_cancelled_once", tmp_path)
    assert out["openedBeforeLoss"] == 2, "the third was skipped as pending"
    assert out["aborted"] == 1, "the one still pending after the loss"
    assert out["lostLines"] == 1


def test_a_request_that_never_completes_is_a_loss(tmp_path) -> None:
    """Qt has no default timeout: a dead peer that never resets leaves
    requests hanging silently. Three seconds unanswered is a loss."""
    out = run("silent_request_is_a_loss", tmp_path)
    assert any("no reply within" in line for line in out["logs"]), out["logs"]
    assert out["aborted"] == 1
    assert out["after"] == ["/ping"], out["after"]


# ── Probing ──────────────────────────────────────────────────────────────────


def test_a_refused_probe_keeps_probing_the_same_host(tmp_path) -> None:
    """Refused means the app is down, not that Qt is wedged."""
    out = run("refused_probe_keeps_host", tmp_path)
    assert out["probeHosts"] == ["127.0.0.1"]
    assert out["probeCount"] >= 2


def test_a_hung_probe_moves_to_a_fresh_host(tmp_path) -> None:
    """The wedge signature, and the one recovery that works after a death."""
    out = run("hung_probe_switches_host", tmp_path)
    assert out["firstAborted"]
    assert out["hosts"][0] == "127.0.0.1"
    assert out["hosts"][-1] == "tk1.localhost", out["hosts"]
    assert any("tk1.localhost" in line for line in out["logs"]), out["logs"]


def test_the_host_switch_is_macos_only(tmp_path) -> None:
    """Traktor 4.4.2 on Windows recovers by itself; leave it alone there."""
    out = run("windows_never_switches_host", tmp_path, platform="windows")
    assert out["hosts"] == ["127.0.0.1"]
    out = run("windows_never_switches_host", tmp_path, platform="osx")
    assert "tk1.localhost" in out["hosts"]


def test_any_http_answer_to_the_probe_restores_normal_traffic(tmp_path) -> None:
    out = run("answered_probe_restores", tmp_path)
    assert out["after"] == ["/command", "/command"], out["after"]
    assert any("restored" in line for line in out["logs"]), out["logs"]


def test_probe_failures_do_not_flood_the_log(tmp_path) -> None:
    out = run("probe_log_volume", tmp_path)
    assert 1 <= out["lines"] <= 3


# ── Long poll (D2 v6) ────────────────────────────────────────────────────────


def test_a_held_long_poll_is_not_a_loss(tmp_path) -> None:
    """GET /command?wait=15 is held by the app on purpose: one request for
    12 s, no loss declared, where the 3 s timeout would have fired."""
    out = run("long_poll_held_is_not_a_loss", tmp_path)
    assert out["path"] == "/command?wait=15"
    assert out["commandRequests"] == 1
    assert out["lost"] == 0


def test_a_silent_keepalive_is_still_a_loss(tmp_path) -> None:
    """The heartbeat keeps the 3 s timeout, so a dead app is noticed as
    quickly as before — and the held /command is cancelled with the rest."""
    out = run("silent_keepalive_still_a_loss", tmp_path)
    assert any("keepAlive" in line for line in out["lostLines"]), out["lostLines"]
    assert out["heldCommandAborted"]
