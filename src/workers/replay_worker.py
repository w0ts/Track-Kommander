"""ReplayWorker — replays Traktor's play history against matcher setups.

Reads the history and the library once, then ranks every transition under
each configuration (core.history_replay.configurations): the current setup,
and each axis added or taken out. Results come as they finish, so the table
fills while it runs; Stop ends it after the configuration in hand.
"""

from __future__ import annotations

import logging
from datetime import date

from PySide6.QtCore import QThread, Signal

from core.history_replay import Result, configurations, replay, transitions

log = logging.getLogger("replay_worker")


class ReplayWorker(QThread):
    started_replay = Signal(int, int)  # transitions, configurations
    result = Signal(object)  # a core.history_replay.Result
    finished = Signal(str)  # "" when it ran; otherwise why not
    error = Signal(str)

    def __init__(
        self,
        current: list[tuple[str, float]],
        before: date | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._current = current
        self._before = before
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        """Run the replay, and say it has finished however it ended."""
        try:
            self.finished.emit(self._run())
        except Exception as exc:  # whatever it was: reported, never swallowed
            log.exception("history replay failed")
            self.error.emit(f"History replay failed: {exc}")
            self.finished.emit(str(exc))

    def _run(self) -> str:
        from core.db import get_conn
        from core.library_snapshot import snapshot
        from core.traktor import find_traktor_history_dir

        history = find_traktor_history_dir()
        if history is None:
            return "No Traktor History folder was found."
        snap = snapshot(get_conn)
        library = {p for p in snap.paths if p}
        played = transitions(history.glob("*.nml"), library, before=self._before)
        if not played:
            return "No transitions: the history has no pair of tracks played back to back that are both in the library."
        configs = configurations(self._current)
        self.started_replay.emit(len(played), len(configs))
        log.info("history replay: %d transitions, %d configurations", len(played), len(configs))
        for name, spec in configs:
            if self._stop:
                break
            r: Result = replay(snap, played, name, spec)
            self.result.emit(r)
        return ""
