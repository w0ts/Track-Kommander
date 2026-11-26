"""Compute waveforms for many tracks at once.

Measured on a real collection: decode + analyse costs about 0.75 s a track and
is the largest single cost of a device export — 9.1 s against 5.4 s to copy
the same 220 MB. It is CPU-bound (decode plus an FFT per column) and each
track is independent, so it parallelises cleanly across cores.

Threads, not processes. The heavy work happens inside libsndfile/PyAV and
NumPy, all of which release the GIL, so threads get real parallelism without
paying to pickle a ``Waveform`` — tens of thousands of columns — back from a
worker, and without the import cost of a fresh interpreter per process.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from typing import Protocol

from core import anlz

log = logging.getLogger(__name__)


class HasSource(Protocol):
    """Anything with the audio path on it — a ``DeviceTrack``, typically."""

    source: object


def worker_count(n_tracks: int) -> int:
    """How many to run at once.

    Capped at 8: beyond that the decoders contend for memory bandwidth more
    than they gain, and an export should leave the machine usable.
    """
    return max(1, min(8, os.cpu_count() or 1, n_tracks))


def analyse_many[T: HasSource](
    tracks: Iterable[T],
    *,
    should_stop: Callable[[], bool] | None = None,
) -> Iterator[tuple[T, anlz.Waveform | None]]:
    """Yield ``(track, waveform)`` as each finishes — not in input order.

    The caller drives a progress bar from these, so they arrive as soon as
    they are ready. A track whose audio cannot be read yields ``None`` rather
    than raising: a missing waveform is a cosmetic loss, and one bad file in a
    library of hundreds must not sink the export.
    """
    items = list(tracks)
    if not items:
        return

    workers = worker_count(len(items))
    if workers == 1:
        for item in items:
            if should_stop is not None and should_stop():
                return
            yield item, _analyse(item)
        return

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="waveform") as pool:
        pending = {pool.submit(_analyse, item): item for item in items}
        try:
            while pending:
                done, _ = wait(pending, return_when=FIRST_COMPLETED)
                for future in done:
                    # Checked per result, not per batch: with short tracks a
                    # whole batch can land at once, and a stop should take
                    # effect then rather than after all of them.
                    if should_stop is not None and should_stop():
                        return
                    item = pending.pop(future)
                    yield item, future.result()
        finally:
            # Whatever has not started yet is dropped; anything already
            # running finishes as the pool shuts down.
            for future in pending:
                future.cancel()


def _analyse(item: HasSource) -> anlz.Waveform | None:
    try:
        return anlz.analyse_waveform(item.source)  # type: ignore[arg-type]
    except Exception:
        log.debug("waveform failed for %s", item.source, exc_info=True)
        return None
