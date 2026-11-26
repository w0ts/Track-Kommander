"""Hotcue detection for one file, as a job a worker process can run.

The Hotcues button and ``--selftest-hotcues`` both hand files to a process
pool and this is what the pool runs. It lives here, not with the worker
thread, because the self-test is in ``core`` and ``core`` does not import
from ``workers``.
"""

from __future__ import annotations


def detect_one(path: str, bpm: float, beat_phase_ms: float, max_cues: int) -> tuple[str, list, str]:
    """Detect hotcues for one file — top-level function for pickling.

    Returns the path, the cues, and why there are none when there are none.

    Runs in a spawned worker process: main-process shims don't carry over, so
    the frozen-bundle shims (missing librosa.core.fft + numba cache locator)
    must be applied here, before the librosa import below. No-op in dev / after
    the first call in this process.
    """
    from core.runtime import is_frozen

    if is_frozen():
        from core.frozen_runtime import apply_frozen_runtime_shims

        apply_frozen_runtime_shims()

    from core.hotcues import detect_drop_positions

    why: list[str] = []
    cues = detect_drop_positions(path, bpm, beat_phase_ms=beat_phase_ms, max_cues=max_cues, why=why)
    # Coarse reason only: the exact duration would make every track its own
    # bucket in the summary.
    reason = why[0].split(" (")[0] if why else ""
    return path, cues, reason
