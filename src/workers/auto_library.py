"""Keep TK's library in step with Traktor's collection by itself (integrated mode).

Inside Traktor there is no Scan page (User, 2026-11-24): TK reads the collection
from Traktor's memory and, while no deck is playing,

* drops tracks Traktor no longer lists from its library, and
* analyses the tracks it does not know yet (or not fully), and only those.

Analysis runs in TK's usual worker threads, inside Traktor's process, so it
only runs while Traktor is idle: a deck starting to play stops it at once, and
what is left is picked up at the next idle check.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Iterable

from PySide6.QtCore import QObject, QTimer

log = logging.getLogger(__name__)

CHECK_MS = 60_000  # how often the collection is compared with the library
FIRST_CHECK_MS = 30_000  # after TK started inside Traktor: let the boot settle first
WORKERS = 2  # analysis threads inside Traktor's process: its UI shares the CPU and the GIL


class AutoLibrary(QObject):
    """Compares the collection with the library and analyses what is new, while idle."""

    def __init__(
        self,
        collection: Callable[[], list[str]],
        analysed: Callable[[], set[str]],
        prune: Callable[[Iterable[str]], int],
        playing: Callable[[], bool],
        start_analysis: Callable[[list[str]], object],
        exists: Callable[[str], bool] = os.path.exists,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._collection = collection
        self._analysed = analysed
        self._prune = prune
        self._playing = playing
        self._start_analysis = start_analysis
        self._exists = exists
        self._worker = None
        self._tried: set[str] = set()  # failed this session: not retried every minute
        self._timer = QTimer(self)
        self._timer.setInterval(CHECK_MS)
        self._timer.timeout.connect(self.check)

    def start(self) -> None:
        self._timer.start()
        QTimer.singleShot(FIRST_CHECK_MS, self, self.check)

    def busy(self) -> bool:
        return self._worker is not None and self._worker.isRunning()

    def check(self) -> None:
        if self._playing():
            self.pause()
            return
        if self.busy():
            return
        paths = self._collection()
        if not paths:  # nothing read: never prune the library to empty
            return
        gone = self._prune(paths)
        if gone:
            log.info("library: %d track(s) Traktor no longer lists were removed", gone)
        analysed = self._analysed()
        todo = [p for p in paths if p not in analysed and p not in self._tried and self._exists(p)]
        if not todo:
            return
        log.info("library: analysing %d new track(s) while Traktor is idle", len(todo))
        self._tried.update(todo)
        self._worker = self._start_analysis(todo)
        finished = getattr(self._worker, "finished", None)
        if finished is not None:
            finished.connect(self._on_finished)

    def on_deck_state(self, _deck: str, playing: bool) -> None:
        """A deck started: analysis stops at once (Traktor's UI comes first)."""
        if playing:
            self.pause()

    def pause(self) -> None:
        if self.busy():
            log.info("library: a deck is playing, analysis paused")
            self._worker.stop()
            # Not analysed after all: the next idle check picks them up again.
            self._tried -= set(getattr(self._worker, "paths", ()) or ())

    def _on_finished(self, *_args) -> None:
        analysed = self._analysed()
        self._tried = {p for p in self._tried if p not in analysed}  # only the real failures stay
