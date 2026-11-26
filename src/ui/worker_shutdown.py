"""Letting go of worker threads without ending the process.

Qt aborts — "QThread: Destroyed while thread is still running" — when a
thread object is destroyed before its thread has finished. A worker says it
is done by emitting a signal from inside ``run()``, which is a moment before
``run()`` returns; dropping the last reference in the slot for that signal
can destroy it in between. And a quit that stops waiting after a few seconds
destroys whatever was still copying a file.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

from PySide6.QtCore import QThread, QTimer

log = logging.getLogger(__name__)

#: How often a kept worker is looked at to see whether it has finished.
_LOOK_AGAIN_MS = 100

#: How long a quit goes on waiting for a worker before it closes anyway. A
#: worker that never finishes must not make the application impossible to
#: quit; by then the alternative is the user forcing it.
GIVE_UP_AFTER_S = 60.0

_kept: set[QThread] = set()


def kept() -> frozenset[QThread]:
    """The workers being held until they finish."""
    return frozenset(_kept)


def keep_until_finished(worker: QThread | None) -> None:
    """Hold *worker* until its thread has ended, then let it go.

    For the caller that is about to drop its own reference from the slot of
    the worker's "done" signal.
    """
    if worker is None or not worker.isRunning():
        return
    _kept.add(worker)
    _let_go_of_the_finished()


def _let_go_of_the_finished() -> None:
    """Drop the kept workers whose threads have ended; look again shortly
    if any are left.

    Asked of the thread itself, not heard from a signal: two of the workers
    have a ``finished`` signal of their own that is emitted from inside
    ``run()``, which is exactly the moment that is too early.
    """
    for worker in [w for w in _kept if not w.isRunning()]:
        _kept.discard(worker)
    if _kept:
        QTimer.singleShot(_LOOK_AGAIN_MS, _let_go_of_the_finished)


def stop_and_wait(workers: Iterable[QThread | None], wait_ms: int) -> list[QThread]:
    """Ask each running worker to stop; return the ones still running after
    *wait_ms* each."""
    busy: list[QThread] = []
    for worker in workers:
        if worker is None or not worker.isRunning():
            continue
        worker.stop()
        if not worker.wait(wait_ms):
            busy.append(worker)
    return busy


def may_close(busy: list, *, waiting_since: float | None, now: float) -> bool:
    """Can the window close, given the workers still running?

    Not while there are any — until the wait has gone on for
    :data:`GIVE_UP_AFTER_S`.
    """
    if not busy:
        return True
    if waiting_since is None:
        return False
    if now - waiting_since >= GIVE_UP_AFTER_S:
        log.warning(
            "quit: gave up waiting for %s after %.0f s",
            ", ".join(type(w).__name__ for w in busy),
            now - waiting_since,
        )
        return True
    return False
