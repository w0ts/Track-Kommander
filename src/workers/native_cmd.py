"""Send a command to the native injector (libtk) inside Traktor.

``libtk.dylib`` (DYLD-injected into Traktor Pro 4, see ``traktor-native/``)
listens on a loopback TCP port and accepts one newline-terminated text command
per connection, so Track Kommander can drive native actions on demand — the
mirror of the ``/show`` opener the dylib POSTs back to us.

Protocol (must match ``tk_inject.mm``)::

    load <deck> <key>\\n          load collection track <key> into deck <deck>
    read <key>\\n                 read track <key>'s metadata (dylib POSTs /nativeTrack)
    loadsel <deck>\\n             load the track SELECTED in Traktor's browser into <deck>
    readsel\\n                    read the selected track's metadata (POST /nativeTrack)
    load_path <deck> <path>\\n    load the collection track at <path> into <deck>
    read_path <path>\\n           read the collection track at <path> (POST /nativeTrack)
    nativeui\\n                   inject the native cards bar into Traktor's window
    list\\n<path>\\n<path>\\n…      (to EOF) the tracks shown under "Track Kommander"
    deck <A-D|->\\n               highlight TK's source deck in the band ("-": none)
    auto <mode> <0|1>\\n          show the automation mode on the AUTO button (1: transition)
    cards\\n<k|label|on|str|val|hue>…  (to EOF) the matching cards drawn in the band

``<deck>`` is a 0-based deck index (0=A … 3=D) and ``<key>`` a collection track
key (0 means "the first track in the collection", a testing convenience).
``<path>`` is the rest of the line (it may contain spaces) and must be the same
absolute file path Traktor imported the track under. The
dylib replies ``OK\\n`` once the command is parsed and accepted, or ``ERR
<why>\\n`` if it is malformed. *Accepted* is not *finished*: the dylib runs the
Traktor C++ work on its main thread and logs the outcome; this sender only
confirms the command was received and well-formed.

This is the sender TK's UI will later call to load a track natively; it is not
wired into the UI here.
"""

from __future__ import annotations

import socket

from core.constants import NATIVE_CMD_PORT

#: Decks Traktor has (0-based). Used to reject a bad index before any socket.
_DECK_COUNT = 4

#: A command is tiny; a loopback round trip is sub-millisecond. Short enough
#: that a wedged or absent listener fails fast rather than hanging the caller.
_TIMEOUT_S = 2.0

#: Loopback only — the dylib binds 127.0.0.1 exclusively.
_HOST = "127.0.0.1"


class NativeCmdError(RuntimeError):
    """The dylib received the command but refused it (replied ``ERR ...``)."""


def _send_line(line: str, *, port: int, timeout: float) -> str:
    """Send one command line, return the dylib's reply (stripped).

    Raises :class:`OSError` (``ConnectionRefusedError``/``socket.timeout``/…)
    when the listener is unreachable — i.e. Traktor is not running, the dylib
    is not injected, or it is on another port.
    """
    with socket.create_connection((_HOST, port), timeout=timeout) as sock:
        sock.settimeout(timeout)
        # UTF-8 so non-ASCII file paths survive; ASCII commands are unchanged.
        sock.sendall(line.encode("utf-8"))
        reply = sock.recv(256).decode("utf-8", "replace")
    return reply.strip()


def _require_reply_ok(reply: str) -> str:
    """Return *reply* if it is an ``OK`` acknowledgement, else raise."""
    if reply.split(" ", 1)[0] == "OK":
        return reply
    raise NativeCmdError(reply or "no reply from the native command listener")


def load_track(
    deck: int, key: int, *, port: int = NATIVE_CMD_PORT, timeout: float = _TIMEOUT_S
) -> str:
    """Ask the dylib to load collection track *key* into *deck* (0=A … 3=D).

    *key* 0 means "the first track in the collection" (a testing convenience).
    Returns the dylib's acknowledgement. Raises :class:`ValueError` for a bad
    argument, :class:`NativeCmdError` if the dylib refuses the command, and
    :class:`OSError` if the listener is unreachable.
    """
    deck = _check_deck(deck)
    key = _check_key(key)
    return _require_reply_ok(_send_line(f"load {deck} {key}\n", port=port, timeout=timeout))


def read_track(key: int, *, port: int = NATIVE_CMD_PORT, timeout: float = _TIMEOUT_S) -> str:
    """Ask the dylib to read track *key*'s metadata and POST ``/nativeTrack``.

    *key* 0 means "the first track in the collection". Returns the dylib's
    acknowledgement. Raises as :func:`load_track` does.
    """
    key = _check_key(key)
    return _require_reply_ok(_send_line(f"read {key}\n", port=port, timeout=timeout))


def load_selection(deck: int, *, port: int = NATIVE_CMD_PORT, timeout: float = _TIMEOUT_S) -> str:
    """Ask the dylib to load the track selected in Traktor's own browser into *deck*.

    Acts on whatever row the user currently has highlighted in Traktor (#3). The
    dylib refuses (``ERR``) if nothing is selected or the selection is not a
    track. Returns the dylib's acknowledgement; raises as :func:`load_track` does.
    """
    deck = _check_deck(deck)
    return _require_reply_ok(_send_line(f"loadsel {deck}\n", port=port, timeout=timeout))


def read_selection(*, port: int = NATIVE_CMD_PORT, timeout: float = _TIMEOUT_S) -> str:
    """Ask the dylib to read the selected track's metadata and POST ``/nativeTrack``.

    Acts on Traktor's current browser selection (#3). Returns the dylib's
    acknowledgement. Raises :class:`NativeCmdError` if the dylib refuses and
    :class:`OSError` if the listener is unreachable.
    """
    return _require_reply_ok(_send_line("readsel\n", port=port, timeout=timeout))


def load_path(
    deck: int, path: str, *, port: int = NATIVE_CMD_PORT, timeout: float = _TIMEOUT_S
) -> str:
    """Ask the dylib to load the collection track at *path* into *deck* (#2).

    *path* must be the absolute file path Traktor imported the track under; the
    dylib resolves it to the track's collection key. Returns the dylib's
    acknowledgement. Raises :class:`ValueError` for a bad argument,
    :class:`NativeCmdError` if the dylib refuses (e.g. the path is not in the
    collection), and :class:`OSError` if the listener is unreachable.
    """
    deck = _check_deck(deck)
    path = _check_path(path)
    return _require_reply_ok(_send_line(f"load_path {deck} {path}\n", port=port, timeout=timeout))


def read_path(path: str, *, port: int = NATIVE_CMD_PORT, timeout: float = _TIMEOUT_S) -> str:
    """Ask the dylib to read the collection track at *path* and POST ``/nativeTrack`` (#2).

    *path* must be the absolute file path Traktor imported the track under.
    Returns the dylib's acknowledgement. Raises as :func:`load_path` does.
    """
    path = _check_path(path)
    return _require_reply_ok(_send_line(f"read_path {path}\n", port=port, timeout=timeout))


def native_ui(*, port: int = NATIVE_CMD_PORT, timeout: float = _TIMEOUT_S) -> str:
    """Ask the dylib to walk Traktor's widget tree and inject the native cards
    bar (embedded UI). Returns the dylib's acknowledgement; the injection runs on
    Traktor's main thread and logs its tree walk + outcome. Raises as the others.
    """
    return _require_reply_ok(_send_line("nativeui\n", port=port, timeout=timeout))


def set_deck(deck: str, *, port: int = NATIVE_CMD_PORT, timeout: float = _TIMEOUT_S) -> str:
    """Show *deck* ("A".."D", or "" for none) as TK's source deck in Traktor's band."""
    if deck not in ("", "A", "B", "C", "D"):
        raise ValueError(f"deck must be A-D or empty, not {deck!r}")
    return _require_reply_ok(_send_line(f"deck {deck or '-'}\n", port=port, timeout=timeout))


def hide_band_button(
    name: str, hidden: bool = True, *, port: int = NATIVE_CMD_PORT, timeout: float = _TIMEOUT_S
) -> str:
    """Leave band button *name* out of Traktor's band (or put it back)."""
    if not name.replace("_", "").isalnum():
        raise ValueError(f"not a band button name: {name!r}")
    return _require_reply_ok(
        _send_line(f"bandhide {name} {int(hidden)}\n", port=port, timeout=timeout)
    )


#: The automation modes the band's AUTO button can show (ui.np_constants._AUTO_MODES).
AUTO_MODES = ("off", "play_cue", "automix", "auto")


def set_auto(
    mode: str, busy: bool = False, *, port: int = NATIVE_CMD_PORT, timeout: float = _TIMEOUT_S
) -> str:
    """Show TK's automation *mode* on the band's AUTO button (*busy*: a transition runs)."""
    if mode not in AUTO_MODES:
        raise ValueError(f"unknown automation mode {mode!r}")
    return _require_reply_ok(_send_line(f"auto {mode} {int(busy)}\n", port=port, timeout=timeout))


def set_cards(
    cards: list[tuple[str, str, bool, int, int, int, int, str]],
    *,
    port: int = NATIVE_CMD_PORT,
    timeout: float = _TIMEOUT_S,
) -> str:
    """Replace the matching cards drawn in Traktor's band.

    One line per card after ``cards``: ``key|label|active|strength|value|hue|src|desc``
    (strength, value and src 0–100; value -1 when the axis has none; src is
    where the playing source track sits on the axis, the card's background
    fill, -1 when there is no source or the axis is off; desc is the axis's
    description for the band's tooltips, flattened to one line). Read to EOF
    like :func:`set_list`.
    """
    lines = []
    for key, label, active, strength, value, hue, src, desc in cards:
        for text in (key, label):
            if "|" in text or "\n" in text or "\r" in text:
                raise ValueError(f"card text must not contain | or a newline: {text!r}")
        # Free text: a separator or newline in it would break the line, so it is flattened.
        flat = " ".join(desc.replace("|", "/").split())[:240]
        lines.append(f"{key}|{label}|{int(active)}|{strength}|{value}|{hue}|{src}|{flat}\n")
    payload = ("cards\n" + "".join(lines)).encode("utf-8")
    with socket.create_connection((_HOST, port), timeout=timeout) as sock:
        sock.settimeout(timeout)
        sock.sendall(payload)
        sock.shutdown(socket.SHUT_WR)
        reply = sock.recv(256).decode("utf-8", "replace").strip()
    return _require_reply_ok(reply)


def set_list(paths: list[str], *, port: int = NATIVE_CMD_PORT, timeout: float = _TIMEOUT_S) -> str:
    """Replace the list Traktor shows under its "Track Kommander" tree node.

    Unlike the other commands this one is many lines: ``list``, then one file
    path per line; the write side is then closed so the dylib reads to the end.
    Paths Traktor's collection does not hold are skipped there. Nothing is
    written to the collection: the list lives in Traktor's memory only. Returns
    the acknowledgement (``OK <count>``). Raises as the others.
    """
    lines = [_check_path(p) for p in paths]
    payload = ("list\n" + "".join(f"{p}\n" for p in lines)).encode("utf-8")
    with socket.create_connection((_HOST, port), timeout=timeout) as sock:
        sock.settimeout(timeout)
        sock.sendall(payload)
        sock.shutdown(socket.SHUT_WR)
        reply = sock.recv(256).decode("utf-8", "replace").strip()
    return _require_reply_ok(reply)


def _check_deck(deck: int) -> int:
    """Validate a 0-based deck index (0=A … 3=D)."""
    if not isinstance(deck, int) or isinstance(deck, bool):
        raise ValueError(f"deck must be an int, not {type(deck).__name__}")
    if not 0 <= deck < _DECK_COUNT:
        raise ValueError(f"deck {deck} out of range 0..{_DECK_COUNT - 1}")
    return deck


def _check_path(path: str) -> str:
    """Validate a file path: a non-empty, single-line string.

    A newline would terminate the command early (the protocol is one line per
    connection), so it is rejected rather than silently truncated.
    """
    if not isinstance(path, str):
        raise ValueError(f"path must be a str, not {type(path).__name__}")
    if not path:
        raise ValueError("path must not be empty")
    if "\n" in path or "\r" in path:
        raise ValueError("path must not contain a newline (one command per line)")
    return path


def _check_key(key: int) -> int:
    """Validate a collection track key: a non-negative, uint32-range int."""
    if not isinstance(key, int) or isinstance(key, bool):
        raise ValueError(f"key must be an int, not {type(key).__name__}")
    if not 0 <= key <= 0xFFFFFFFF:
        raise ValueError(f"key {key} out of range 0..{0xFFFFFFFF}")
    return key
