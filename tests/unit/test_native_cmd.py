"""The TK->dylib command sender: it must put the right bytes on the wire.

:mod:`workers.native_cmd` talks to the native injector over a loopback socket.
These tests stand a tiny stub listener on an ephemeral port in its own thread,
capture exactly what the sender sends, and hand back a chosen reply — so the
request format and the error handling are checked without the dylib or Traktor.
"""

from __future__ import annotations

import socket
import threading
from collections.abc import Iterator

import pytest

from workers import native_cmd
from workers.native_cmd import (
    NativeCmdError,
    load_path,
    load_selection,
    load_track,
    read_path,
    read_selection,
    read_track,
)


class _StubListener:
    """A one-shot loopback server that records one request line and replies.

    Serves connections until closed; each handler reads one newline-terminated
    line into :attr:`received` and writes :attr:`reply` back.
    """

    def __init__(self, reply: bytes = b"OK\n") -> None:
        self.reply = reply
        self.received: list[str] = []
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(8)
        self.port: int = self._sock.getsockname()[1]
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        while True:
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return  # closed
            with conn:
                conn.settimeout(2.0)
                buf = b""
                try:
                    while b"\n" not in buf and len(buf) < 256:
                        chunk = conn.recv(256)
                        if not chunk:
                            break
                        buf += chunk
                    self.received.append(buf.decode("utf-8", "replace"))
                    conn.sendall(self.reply)
                except OSError:
                    pass

    def close(self) -> None:
        self._sock.close()


@pytest.fixture
def stub() -> Iterator[_StubListener]:
    listener = _StubListener()
    try:
        yield listener
    finally:
        listener.close()


def _free_port() -> int:
    """An ephemeral port with nothing listening on it."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


# ── the wire format ──────────────────────────────────────────────────────────


def test_load_sends_load_deck_key(stub: _StubListener) -> None:
    reply = load_track(0, 5, port=stub.port)
    assert stub.received == ["load 0 5\n"]
    assert reply == "OK"


def test_read_sends_read_key(stub: _StubListener) -> None:
    reply = read_track(42, port=stub.port)
    assert stub.received == ["read 42\n"]
    assert reply == "OK"


def test_load_key_zero_is_allowed(stub: _StubListener) -> None:
    # key 0 = "the first track in the collection" (a testing convenience).
    load_track(3, 0, port=stub.port)
    assert stub.received == ["load 3 0\n"]


def test_loadsel_sends_loadsel_deck(stub: _StubListener) -> None:
    reply = load_selection(2, port=stub.port)
    assert stub.received == ["loadsel 2\n"]
    assert reply == "OK"


def test_readsel_sends_readsel(stub: _StubListener) -> None:
    reply = read_selection(port=stub.port)
    assert stub.received == ["readsel\n"]
    assert reply == "OK"


def test_native_ui_sends_nativeui(stub: _StubListener) -> None:
    from workers.native_cmd import native_ui

    reply = native_ui(port=stub.port)
    assert stub.received == ["nativeui\n"]
    assert reply == "OK"


def test_load_path_sends_deck_and_path(stub: _StubListener) -> None:
    reply = load_path(1, "/Users/x/Music/My Track.mp3", port=stub.port)
    # The path keeps its spaces: it is the rest of the line.
    assert stub.received == ["load_path 1 /Users/x/Music/My Track.mp3\n"]
    assert reply == "OK"


def test_read_path_sends_path(stub: _StubListener) -> None:
    reply = read_path("/Users/x/Music/Another Track.aiff", port=stub.port)
    assert stub.received == ["read_path /Users/x/Music/Another Track.aiff\n"]
    assert reply == "OK"


def test_a_non_ascii_path_is_sent_as_utf8(stub: _StubListener) -> None:
    # A Unicode path must survive the wire (the dylib reads raw UTF-8 bytes).
    read_path("/Users/x/Música/Café.mp3", port=stub.port)
    assert stub.received == ["read_path /Users/x/Música/Café.mp3\n"]


# ── argument validation (fails before any socket) ────────────────────────────


@pytest.mark.parametrize("deck", [-1, 4, 99])
def test_load_rejects_a_deck_out_of_range(deck: int) -> None:
    with pytest.raises(ValueError):
        load_track(deck, 1, port=_free_port())


@pytest.mark.parametrize("deck", [True, 1.0, "0", None])
def test_load_rejects_a_non_int_deck(deck: object) -> None:
    with pytest.raises(ValueError):
        load_track(deck, 1, port=_free_port())  # type: ignore[arg-type]


@pytest.mark.parametrize("key", [-1, 0x1_0000_0000])
def test_a_key_out_of_uint32_range_is_rejected(key: int) -> None:
    with pytest.raises(ValueError):
        read_track(key, port=_free_port())


@pytest.mark.parametrize("key", [True, 2.5, "5", None])
def test_a_non_int_key_is_rejected(key: object) -> None:
    with pytest.raises(ValueError):
        read_track(key, port=_free_port())  # type: ignore[arg-type]


@pytest.mark.parametrize("deck", [-1, 4, 99])
def test_loadsel_rejects_a_deck_out_of_range(deck: int) -> None:
    with pytest.raises(ValueError):
        load_selection(deck, port=_free_port())


@pytest.mark.parametrize("deck", [-1, 4])
def test_load_path_rejects_a_deck_out_of_range(deck: int) -> None:
    with pytest.raises(ValueError):
        load_path(deck, "/x.mp3", port=_free_port())


@pytest.mark.parametrize("path", ["", "a\nb", "a\rb"])
def test_load_path_rejects_empty_or_multiline_path(path: str) -> None:
    with pytest.raises(ValueError):
        load_path(0, path, port=_free_port())


@pytest.mark.parametrize("path", ["", "line1\nline2"])
def test_read_path_rejects_empty_or_multiline_path(path: str) -> None:
    with pytest.raises(ValueError):
        read_path(path, port=_free_port())


@pytest.mark.parametrize("path", [None, 123, b"/x.mp3"])
def test_a_non_str_path_is_rejected(path: object) -> None:
    with pytest.raises(ValueError):
        read_path(path, port=_free_port())  # type: ignore[arg-type]


# ── the dylib's reply ────────────────────────────────────────────────────────


def test_an_err_reply_raises(stub: _StubListener) -> None:
    stub.reply = b"ERR usage: load <deck> <key>\n"
    with pytest.raises(NativeCmdError, match="usage"):
        load_track(0, 1, port=stub.port)


def test_an_empty_reply_raises(stub: _StubListener) -> None:
    stub.reply = b""
    with pytest.raises(NativeCmdError):
        read_track(1, port=stub.port)


def test_read_path_err_reply_raises(stub: _StubListener) -> None:
    # The dylib refuses when the path is not in the collection.
    stub.reply = b"ERR no collection track\n"
    with pytest.raises(NativeCmdError):
        read_path("/not/in/collection.mp3", port=stub.port)


def test_no_listener_raises_oserror() -> None:
    # Traktor not running / dylib not injected: connecting is refused.
    with pytest.raises(OSError):
        load_track(0, 1, port=_free_port(), timeout=0.5)


def test_the_default_port_is_not_the_reserved_one() -> None:
    # 8766 is reserved for another local app; the channel must default elsewhere.
    assert native_cmd.NATIVE_CMD_PORT != 8766


# ── set_list: many lines, read to the end ────────────────────────────────────


class _EofListener:
    """Like _StubListener, but reads each request to EOF (the list command)."""

    def __init__(self, reply: bytes) -> None:
        self.reply = reply
        self.received: list[str] = []
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(1)
        self.port: int = self._sock.getsockname()[1]
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self) -> None:
        conn, _ = self._sock.accept()
        with conn:
            conn.settimeout(2.0)
            buf = b""
            while chunk := conn.recv(4096):
                buf += chunk
            self.received.append(buf.decode("utf-8"))
            conn.sendall(self.reply)

    def close(self) -> None:
        self._sock.close()


def test_set_list_sends_one_path_per_line_then_closes() -> None:
    listener = _EofListener(b"OK 2\n")
    try:
        reply = native_cmd.set_list(["/a/one.mp3", "/b/twö.flac"], port=listener.port)
    finally:
        listener.close()
    assert reply == "OK 2"
    assert listener.received == ["list\n/a/one.mp3\n/b/twö.flac\n"]


def test_set_list_of_nothing_clears_the_list() -> None:
    listener = _EofListener(b"OK 0\n")
    try:
        native_cmd.set_list([], port=listener.port)
    finally:
        listener.close()
    assert listener.received == ["list\n"]


def test_set_list_rejects_a_path_with_a_newline() -> None:
    with pytest.raises(ValueError):
        native_cmd.set_list(["/a\n/b.mp3"], port=_free_port())


def test_set_deck_sends_the_letter_or_a_dash(stub: _StubListener) -> None:
    native_cmd.set_deck("C", port=stub.port)
    native_cmd.set_deck("", port=stub.port)
    assert stub.received == ["deck C\n", "deck -\n"]


def test_set_deck_rejects_anything_else() -> None:
    with pytest.raises(ValueError):
        native_cmd.set_deck("E", port=_free_port())


def test_set_cards_sends_one_line_per_card_then_closes() -> None:
    listener = _EofListener(b"OK 2\n")
    try:
        native_cmd.set_cards(
            [
                ("bpm", "BPM", True, 80, -1, 200, 42, "Tempo match.\nIn | BPM"),
                ("vocal", "Vocal", False, 0, 35, 40, -1, ""),
            ],
            port=listener.port,
        )
    finally:
        listener.close()
    assert listener.received == [
        "cards\nbpm|BPM|1|80|-1|200|42|Tempo match. In / BPM\nvocal|Vocal|0|0|35|40|-1|\n"
    ]


def test_set_cards_rejects_a_separator_in_a_label() -> None:
    with pytest.raises(ValueError):
        native_cmd.set_cards([("bpm", "B|PM", True, 1, 1, 1, -1, "")], port=_free_port())


def test_set_auto_sends_mode_and_busy_flag(stub: _StubListener) -> None:
    native_cmd.set_auto("automix", port=stub.port)
    native_cmd.set_auto("auto", True, port=stub.port)
    assert stub.received == ["auto automix 0\n", "auto auto 1\n"]


def test_set_auto_rejects_an_unknown_mode() -> None:
    with pytest.raises(ValueError):
        native_cmd.set_auto("party", port=_free_port())


def test_the_auto_modes_match_the_tabs_own() -> None:
    from ui.np_constants import _AUTO_MODES

    assert tuple(m[0] for m in _AUTO_MODES) == native_cmd.AUTO_MODES


def test_hide_band_button_sends_the_name_and_state(stub: _StubListener) -> None:
    native_cmd.hide_band_button("S_EXTRA", port=stub.port)
    native_cmd.hide_band_button("S_EXTRA", False, port=stub.port)
    assert stub.received[-2:] == ["bandhide S_EXTRA 1\n", "bandhide S_EXTRA 0\n"]


def test_hide_band_button_refuses_anything_but_a_name() -> None:
    with pytest.raises(ValueError):
        native_cmd.hide_band_button("S X; rm", port=_free_port())
