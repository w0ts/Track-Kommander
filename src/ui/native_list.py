"""Keep Traktor's "Track Kommander" list in step with TK's results.

The injected dylib (``traktor-native/``) adds a "Track Kommander" node to
Traktor's own browser tree; selecting it shows a list the dylib fills from
paths TK sends (:func:`workers.native_cmd.set_list`). The first click on that
node (``/uiAction KOMMANDER``) switches this pusher on; from then on every
change to TK's results is sent again, a moment after the last one, so a burst
of re-ranks costs one send. When Traktor is gone the pusher switches off until
the node is clicked again.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from PySide6.QtCore import QObject, QTimer

from workers import native_cmd

log = logging.getLogger("native_list")

#: How many of TK's best results Traktor's list shows.
MAX_TRACKS = 200
#: Results often change several times in a row (re-rank, then filter).
_DEBOUNCE_MS = 300


class NativeListPusher(QObject):
    """Sends ``paths()`` to the dylib when TK's results change, once active."""

    def __init__(
        self,
        paths: Callable[[], list[str]],
        parent: QObject | None = None,
        *,
        send: Callable[[list[str]], str] = native_cmd.set_list,
        also: Callable[[], object] | None = None,
    ) -> None:
        super().__init__(parent)
        self._paths = paths
        self._send = send
        #: Run on every push, list changed or not (the results' scores for TK's column in
        #: Traktor's list: a re-rank changes them without changing the list).
        self._also = also
        self.active = False
        self._last_sent: list[str] | None = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(_DEBOUNCE_MS)
        self._timer.timeout.connect(self.push)

    def activate(self) -> None:
        """The node was clicked in Traktor: send now and follow changes.

        Always sends, even an unchanged list: Traktor may have restarted and
        lost it."""
        self.active = True
        self.push(force=True)

    def results_changed(self) -> None:
        if self.active:
            self._timer.start()

    def push(self, *, force: bool = False) -> None:
        """Send the current paths, unless they are what Traktor already shows.

        TK rebuilds its results far more often than they change (every deck
        position or tempo update re-filters). Re-sending an unchanged list
        rebuilds Traktor's list, which drops its selection and any drag in
        progress, so only a real change goes out."""
        if self._also is not None:
            self._also()
        paths = self._paths()[:MAX_TRACKS]
        if not force and paths == self._last_sent:
            return
        try:
            reply = self._send(paths)
        except OSError as exc:
            # Traktor closed or the dylib is not injected: stop until clicked again.
            log.info("native list: Traktor not reachable (%s); paused", exc)
            self.active = False
            return
        except (native_cmd.NativeCmdError, ValueError) as exc:
            log.warning("native list: not sent: %s", exc)
            return
        if self._last_sent is not None:
            # What changed, so a list that keeps flipping can be traced to its cause.
            old, new = set(self._last_sent), set(paths)
            log.info(
                "native list: %d path(s); added %s; removed %s",
                len(paths),
                sorted(new - old)[:3],
                sorted(old - new)[:3],
            )
        self._last_sent = paths
        log.debug("native list: sent %d path(s): %s", len(paths), reply)


class NativeDeckMirror:
    """Shows TK's source deck on the A–D buttons of the band inside Traktor.

    Sent on every change of source; ``resend`` repeats the last one when the
    band was just drawn (a restarted Traktor starts with none highlighted).
    Nothing is retried when Traktor is not running: the next change or the
    band's own request brings it back in step.
    """

    def __init__(self, *, send: Callable[[str], str] = native_cmd.set_deck) -> None:
        self._send = send
        self.deck = ""

    def changed(self, deck: str) -> None:
        self.deck = deck
        self.resend()

    def resend(self) -> None:
        try:
            self._send(self.deck)
        except OSError:
            log.debug("native deck: Traktor not reachable")
        except (native_cmd.NativeCmdError, ValueError) as exc:
            log.warning("native deck: not sent: %s", exc)


#: How often the cards are compared with what Traktor shows. Polling one small
#: list beats wiring every slider, toggle and panel rebuild to a signal.
_CARDS_POLL_MS = 500


class NativeCardsMirror(QObject):
    """Keeps the matching cards in Traktor's band in step with TK's own.

    Sends only on a change; ``resend`` repeats the last state (the band was
    just drawn). While Traktor is unreachable nothing is retried until the
    cards change or the band asks again.
    """

    def __init__(
        self,
        states: Callable[[], list[tuple[str, str, bool, int, int, int, int, str]]],
        parent: QObject | None = None,
        *,
        send: Callable[
            [list[tuple[str, str, bool, int, int, int, int, str]]], str
        ] = native_cmd.set_cards,
    ) -> None:
        super().__init__(parent)
        self._states = states
        self._send = send
        self._last: list | None = None
        self._timer = QTimer(self)
        self._timer.setInterval(_CARDS_POLL_MS)
        self._timer.timeout.connect(self.poll)
        self._timer.start()

    def poll(self) -> None:
        cards = self._states()
        if cards != self._last:
            self._push(cards)

    def resend(self) -> None:
        self._push(self._states())

    def _push(self, cards: list) -> None:
        try:
            self._send(cards)
        except OSError:
            self._last = cards  # Traktor closed: wait for a change or the band's request
            return
        except (native_cmd.NativeCmdError, ValueError) as exc:
            log.warning("native cards: not sent: %s", exc)
            return
        self._last = cards


class NativeAutoMirror:
    """Shows TK's automation mode on the AUTO button of Traktor's band.

    Like :class:`NativeDeckMirror`: sent on every change, ``resend`` when the
    band was just drawn, nothing retried while Traktor is not running.
    """

    def __init__(self, *, send: Callable[[str, bool], str] = native_cmd.set_auto) -> None:
        self._send = send
        self.mode = "off"
        self.busy = False

    def changed(self, mode: str, busy: bool) -> None:
        self.mode, self.busy = mode, busy
        self.resend()

    def resend(self) -> None:
        try:
            self._send(self.mode, self.busy)
        except OSError:
            log.debug("native auto: Traktor not reachable")
        except (native_cmd.NativeCmdError, ValueError) as exc:
            log.warning("native auto: not sent: %s", exc)
