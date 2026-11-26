.pragma library

// ── Track Kommander API client ─────────────────────────────────────────────
//
// Traktor talks to the app over plain HTTP on loopback: a long-poll for
// commands (the app holds GET /command?wait=N until it has one) and a 1 Hz
// post of full deck state, which is also the heartbeat.
//
// Two modes. **Connected**: requests go out as the timers fire. **Reconnecting**:
// the timers still fire but nothing is sent except one small probe a second,
// never two at once, until the app answers — then back to connected.
//
// The mode exists because the alternative was measured to be fatal. This
// client used to fire as if nothing had happened: when the app died, dozens
// of requests a second piled onto Qt's connection object for 127.0.0.1:12345,
// and that object (Traktor bundles Qt 6.5.3) never issued a connection again
// for the life of the process — no socket, nothing completing — while a fresh
// host:port worked instantly. Restarting Traktor was the only cure. Giving Qt
// one outstanding request at a time, and never aborting a request mid-connect,
// is what an HTTP client is supposed to do anyway.

var API_PORT = 12345;

// Increment whenever the D2 QML API changes in a way the app needs to detect.
var D2_VERSION = "8";

// ── Fresh host after a death (macOS only) ──────────────────────────────────
//
// Reconnect mode recovers from the app not being there yet. It does not
// recover from the app having *died mid-request*: measured on Traktor 4.5.1
// for macOS, Qt's connection channels for that host:port stay stuck for the
// life of the process, and a probe a second hangs forever. Traktor 4.4.2 on
// Windows recovers on its own, so the switch below is confined to macOS.
//
// The escape is that Qt keys its connection cache by the host string, and
// every name under .localhost resolves to loopback. After a run of
// unanswered probes, probing moves to tk1.localhost, then tk2.localhost, … —
// the same socket on the app's side, a connection object that has never
// failed on Qt's. Nothing on the app side knows or cares.

//: Fallback: unanswered probing for this long presumes the host string is
//: wedged even if no single probe hung. Normally the switch happens sooner,
//: on the first probe that hangs.
var FRESH_HOST_AFTER_MS = 12000;

var _hostSerial = 0;                 // 0 = plain loopback, n = tk<n>.localhost
var _hostSerialAtLoss = 0;
var _lastHostSwitchAt = 0;

function _freshHostsAllowed() {
    try { return Qt.platform.os === "osx"; } catch (e) { return true; }
}

function _host() {
    return _hostSerial === 0 ? "127.0.0.1" : "tk" + _hostSerial + ".localhost";
}

function _baseUrl() {
    return "http://" + _host() + ":" + API_PORT;
}

function _maybeFreshHost(now, probeHung) {
    if (!_freshHostsAllowed()) return;
    var since = Math.max(_lostAt, _lastHostSwitchAt);
    if (!probeHung && now - since < FRESH_HOST_AFTER_MS) return;
    _hostSerial++;
    _lastHostSwitchAt = now;
    // While the app is down every probe hangs, so this fires every few
    // seconds for as long as it is down. Say it once, then about once a
    // minute — the count in the "restored" line tells the full story.
    var switches = _hostSerial - _hostSerialAtLoss;
    if (switches === 1 || switches % 15 === 0) {
        console.log("TrackKommander: no answer for " + Math.round((now - _lostAt) / 1000)
                    + "s — probing via " + _host()
                    + " (fresh Qt connection; macOS Traktor workaround"
                    + (switches > 1 ? ", " + switches + " hosts so far" : "") + ")");
    }
}

//: A request unanswered for this long means the app is gone. Qt has no
//: default timeout of its own; without this a dead request is silent.
var REQUEST_TIMEOUT_MS = 3000;

//: GET /command asks the app to hold it this long waiting for a command
//: (long poll; the app caps it at 15 s) ...
var LONG_POLL_S = 15;
//: ... so it gets its own, longer timeout. Losing the app is still noticed
//: within REQUEST_TIMEOUT_MS: the 1 Hz keepAlive keeps that one.
var LONG_POLL_TIMEOUT_MS = 20000;

//: After a loss, send nothing at all for this long before the first probe:
//: every in-flight request is cancelled at the moment of loss, and Qt's
//: channels get a quiet moment to see their sockets fail and settle. The
//: hammer-through-it approach this replaces never gave them one.
var QUIET_AFTER_LOSS_MS = 3000;

//: Reconnecting: one probe this often.
var PROBE_INTERVAL_MS = 1000;

//: A probe unanswered for this long is dropped and replaced. The only place
//: this client ever aborts a request outside the moment of loss. A refused
//: loopback connection completes with status 0 within milliseconds, so a
//: probe that is still hanging after this long is not "the app is down" —
//: it is the connection object being wedged.
var PROBE_TIMEOUT_MS = 3000;

//: Something the app does not serve. A 404 proves it is alive without
//: touching the command queue — probing /command would pop a queued command
//: and throw it away.
var PROBE_PATH = "/ping";

var successCount = 0;
var _reconnecting = false;
var _lostAt = 0;
var _probe = null;
var _nextProbeAt = 0;
var _probeFailures = 0;
var _inflight = {};                  // endpoint -> newest request, connected mode

function _lost(endpoint, detail) {
    if (_reconnecting) return;
    _reconnecting = true;
    _lostAt = Date.now();
    _nextProbeAt = _lostAt + QUIET_AFTER_LOSS_MS;
    _probeFailures = 0;
    _hostSerialAtLoss = _hostSerial;
    _lastHostSwitchAt = 0;
    // Start clean: cancel everything outstanding, once, so nothing of ours is
    // left queued on the connection that just failed.
    for (var ep in _inflight) {
        var xhr = _inflight[ep];
        if (!xhr || xhr.readyState === 4) continue;
        xhr._tkAborted = true;
        try { xhr.abort(); } catch (e) {}
    }
    _inflight = {};
    console.log(successCount > 0
        ? "TrackKommander: connection LOST after " + successCount
          + " successful request(s) — /" + endpoint + " (" + detail
          + "). Cancelled what was in flight; quiet for "
          + (QUIET_AFTER_LOSS_MS / 1000) + "s, then one probe a second."
        : "TrackKommander: app not reachable yet (/" + endpoint + ": " + detail
          + "). Probing once a second.");
}

function _reconnected() {
    var down = Math.round((Date.now() - _lostAt) / 1000);
    console.log("TrackKommander: connection restored after " + down + "s and "
                + _probeFailures + " unanswered probe(s)");
    _reconnecting = false;
    _probe = null;
    _probeFailures = 0;
}

function _probeFailed(detail) {
    _probeFailures++;
    _nextProbeAt = Date.now() + PROBE_INTERVAL_MS;
    if (_probeFailures === 1 || _probeFailures % 60 === 0) {   // then once a minute
        console.log("TrackKommander: still reconnecting — probe #" + _probeFailures
                    + " " + detail);
    }
}

function _maybeProbe() {
    var now = Date.now();
    if (_probe) {
        if (now - _probe._tkStarted < PROBE_TIMEOUT_MS) return;
        var stale = _probe;
        _probe = null;
        stale._tkAborted = true;
        try { stale.abort(); } catch (e) {}
        _probeFailed("no reply within " + PROBE_TIMEOUT_MS + " ms");
        _maybeFreshHost(now, true);      // hung, not refused: the wedge signature
    }
    if (now < _nextProbeAt) return;
    _maybeFreshHost(now, false);

    var xhr = new XMLHttpRequest();
    xhr._tkStarted = now;
    xhr.onreadystatechange = function() {
        if (xhr.readyState !== 4 || xhr._tkAborted) return;
        if (_probe === xhr) _probe = null;
        // Any HTTP status at all means the app answered; 0 means nobody did.
        if (xhr.status !== 0) _reconnected();
        else _probeFailed("no connection");
    };
    _probe = xhr;
    try {
        xhr.open("GET", _baseUrl() + PROBE_PATH, true);
        xhr.send();
    } catch (e) {
        _probe = null;
        _probeFailed("exception: " + e);
    }
}

// Connected mode: a request that is still unanswered after REQUEST_TIMEOUT_MS
// is the app having gone away without a reset. Nothing is aborted — the
// request is left to fail on its own; we just stop sending more.
function _sweep() {
    var now = Date.now();
    for (var ep in _inflight) {
        var xhr = _inflight[ep];
        var limit = (xhr && xhr._tkTimeout) || REQUEST_TIMEOUT_MS;
        if (xhr && xhr.readyState !== 4 && now - xhr._tkStarted >= limit) {
            _lost(ep, "no reply within " + limit + " ms");
            return;
        }
    }
}

// Flow control. The timers fire at 10 Hz whatever happens; a reply slower
// than that skips a tick rather than stacking a second request behind it,
// and a reply that never comes ages into REQUEST_TIMEOUT_MS and is a loss.
// Replacing the tracked request on every tick — the first version of this —
// meant nothing ever grew old enough to count, so a dead app was never
// noticed at all.
function _pending(endpoint) {
    var xhr = _inflight[endpoint];
    return !!xhr && xhr.readyState !== 4;
}

function _track(endpoint, xhr, timeoutMs) {
    xhr._tkStarted = Date.now();
    xhr._tkTimeout = timeoutMs || REQUEST_TIMEOUT_MS;
    _inflight[endpoint] = xhr;
}

function _done(endpoint, xhr) {
    if (_inflight[endpoint] === xhr) _inflight[endpoint] = null;
}

function _finished(endpoint, xhr) {
    _done(endpoint, xhr);
    if (xhr._tkAborted) return false;        // we cancelled it at the loss
    if (_reconnecting) return false;         // a straggler from before the loss
    if (xhr.status === 0) { _lost(endpoint, "no connection"); return false; }
    if (xhr.status >= 400) {
        // The app is there and said no. Not a connection problem; say so
        // once rather than once per tick.
        if (!_finished._warned) {
            console.log("TrackKommander: /" + endpoint + " answered HTTP " + xhr.status);
            _finished._warned = true;
        }
        return false;
    }
    successCount++;
    return true;
}

// POST JSON to an endpoint (fire-and-forget, but failures are reported).
function send(endpoint, data) {
    if (_reconnecting) { _maybeProbe(); return; }
    _sweep();
    if (_reconnecting) { _maybeProbe(); return; }
    if (_pending(endpoint)) return;          // one outstanding per endpoint
    var xhr = new XMLHttpRequest();
    xhr.onreadystatechange = function() {
        if (xhr.readyState !== 4) return;
        _finished(endpoint, xhr);
    };
    try {
        _track(endpoint, xhr);
        xhr.open("POST", _baseUrl() + "/" + endpoint, true);
        xhr.setRequestHeader("Content-Type", "application/json");
        xhr.send(JSON.stringify(data));
    } catch (e) {
        _done(endpoint, xhr);
        _lost(endpoint, "exception: " + e);
    }
}

// GET an endpoint and call callback(parsedJson) on 200 response.
// timeoutMs: how long before an unanswered request counts as the app gone —
// longer than REQUEST_TIMEOUT_MS for a request the app holds (long poll).
function get(endpoint, callback, timeoutMs) {
    if (_reconnecting) { _maybeProbe(); return; }
    _sweep();
    if (_reconnecting) { _maybeProbe(); return; }
    if (_pending(endpoint)) return;          // one outstanding per endpoint
    var xhr = new XMLHttpRequest();
    xhr.onreadystatechange = function() {
        if (xhr.readyState !== 4) return;
        if (!_finished(endpoint, xhr)) return;
        try { callback(JSON.parse(xhr.responseText)); } catch(e) {}
    };
    try {
        _track(endpoint, xhr, timeoutMs);
        xhr.open("GET", _baseUrl() + "/" + endpoint, true);
        xhr.send();
    } catch (e) {
        _done(endpoint, xhr);
        _lost(endpoint, "exception: " + e);
    }
}
