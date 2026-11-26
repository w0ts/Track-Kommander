"""Band icons show what is running (integrated mode, User 2026-11-24): dimmed blue while busy.
An add-on's buttons say whether their job runs (core.extensions.BandButton.busy): read off
Traktor's main thread, as a process check or a folder scan would stall Traktor's UI. TK's own
jobs — hotcue detection, the Discogs/Spotify lookup (Tagging), the USB export — are its Qt
workers, looked at on the main thread. Only a change is pushed to the band (libtk keeps every
picture it is given).
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from PySide6.QtCore import QObject, QTimer, Signal

log = logging.getLogger(__name__)


def worker_running(worker) -> bool:
    """A QThread worker that exists and runs."""
    return worker is not None and bool(worker.isRunning())


POLL_MS = 5_000  # an add-on's states (processes, folders), in a thread
LOCAL_MS = 1_000  # TK's own jobs: a look at its workers, on the main thread


def read_states() -> dict[str, bool]:
    """{band button: busy} for an add-on's buttons. Slow: never on the main thread."""
    from ui.band_extensions import busy_states

    return busy_states()


class BandStates(QObject):
    """Polls :func:`read_states` in a thread; pushes an icon when its state changes."""

    _read = Signal(dict)

    def __init__(
        self,
        push: Callable[[str, bool], bool],
        read: Callable[[], dict[str, bool]] | None = read_states,
        parent: QObject | None = None,
        local: Callable[[], dict[str, bool]] | None = None,
    ) -> None:
        """*read*: slow states (files, processes), read in a thread; None: none. *local*: TK's
        own jobs (a Qt worker running), cheap, read on the main thread every LOCAL_MS."""
        super().__init__(parent)
        self._push = push
        self._read_fn = read
        self._local_fn = local
        self._shown: dict[str, bool] = {}
        self._busy = False
        self._read.connect(self.apply)
        self._timer = QTimer(self)
        self._timer.setInterval(POLL_MS)
        self._timer.timeout.connect(self.poll)
        self._local_timer = QTimer(self)
        self._local_timer.setInterval(LOCAL_MS)
        self._local_timer.timeout.connect(self.poll_local)

    def start(self) -> None:
        if self._read_fn is not None:
            self._timer.start()
            self.poll()
        if self._local_fn is not None:
            self._local_timer.start()
            self.poll_local()

    def poll_local(self) -> None:
        """TK's own jobs, on the main thread (where their workers live)."""
        if self._local_fn is None:
            return
        try:
            states = self._local_fn()
        except Exception:
            log.warning("band states (TK's jobs) could not be read", exc_info=True)
            return
        self._show(states)

    def poll(self) -> None:
        if self._busy or self._read_fn is None:
            return  # the last read is still running
        self._busy = True
        threading.Thread(target=self._read_in_thread, name="band-states", daemon=True).start()

    def _read_in_thread(self) -> None:
        try:
            states = self._read_fn() if self._read_fn is not None else {}
        except Exception:
            log.warning("band states could not be read", exc_info=True)
            states = {}
        self._read.emit(states)  # to the main thread (this object's)

    def apply(self, states: dict) -> None:
        self._busy = False
        self._show(states)

    def _show(self, states: dict) -> None:
        for key, busy in states.items():
            if self._shown.get(key) == busy:
                continue
            if self._push(key, busy):
                self._shown[key] = busy
                log.info("band: %s %s", key, "busy (blue)" if busy else "idle")
