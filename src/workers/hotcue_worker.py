"""HotcueWorker — detects and writes hotcues for tracks without them.

Flow
----
1. Parse collection.nml once via ``get_hotcue_candidates`` to get
   (path, bpm, beat_phase_ms) for every eligible track — no DB needed.
2. Dispatch tasks to a ProcessPoolExecutor so detection runs in parallel
   (QOS_CLASS_UTILITY / BELOW_NORMAL priority).
3. After all detections, write positions to ``collection.nml`` in a single
   pass via ``core.traktor.write_hotcues_to_nml``.
4. Emit ``finished(count_written, nml_path)`` when done.
"""

from __future__ import annotations

import logging
import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from core.hotcue_job import detect_one
from core.traktor import write_hotcues_to_nml

log = logging.getLogger("hotcue_worker")


# ── Worker-process helpers (must be top-level / picklable) ────────────────────


def _hotcue_init(n_threads: int = 1) -> None:
    """Set process priority and thread budget for a hotcue worker process."""
    try:
        os.nice(10)
    except Exception:
        pass

    try:
        import ctypes as _ct
        import ctypes.util as _cu

        _pthread = _ct.CDLL(_cu.find_library("pthread") or "libpthread.dylib")
        _QOS_CLASS_UTILITY = 0x11
        _pthread.pthread_set_qos_class_self_np(_QOS_CLASS_UTILITY, 0)
    except Exception:
        pass

    t = str(n_threads)
    for _k in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
        os.environ[_k] = t


# ── Worker thread ─────────────────────────────────────────────────────────────


class HotcueWorker(QThread):
    progress = Signal(int, int, str)  # current, total, filename
    finished = Signal(int, str)  # count_written, nml_path
    error = Signal(str)
    #: Inside Traktor (write_nml=False): {path: [(start_ms, name, cue_type), …]} for the UI thread
    #: to write live into Traktor (workers.native_cues) — instead of ``finished`` after a write.
    detected = Signal(dict)

    def __init__(
        self,
        nml_path: str,
        candidates: list[tuple[str, float, float]],
        force: bool = False,
        parent=None,
        write_nml: bool = True,
    ):
        """*candidates* is a list of ``(path, bpm, beat_phase_ms)`` tuples
        as returned by ``get_hotcue_candidates``.
        When *force* is True existing hotcues (slots 1–7) are replaced.
        With *write_nml* False nothing is written here: ``detected`` carries the cues."""
        super().__init__(parent)
        self.nml_path = nml_path
        self.candidates = candidates
        self.force = force
        self.write_nml = write_nml
        self._stop = False
        self._pool: ProcessPoolExecutor | None = None
        #: How the run went, for the log and the status line: tracks that got
        #: cues, that had none detected, that failed.
        self.outcome: dict[str, int] = {"cued": 0, "failed": 0}

        n_cpus = os.cpu_count() or 4
        free = max(1, n_cpus // 4)  # keep ≥1 and ≥25% for system + Traktor
        avail = max(1, n_cpus - free)
        self._n_workers = min(avail, max(1, len(candidates)))
        self._threads_per_worker = max(1, avail // self._n_workers)

    def stop(self) -> None:
        self._stop = True
        if self._pool is not None:
            try:
                for proc in self._pool._processes.values():
                    try:
                        proc.terminate()
                    except Exception:
                        pass
                self._pool.shutdown(wait=False, cancel_futures=True)
            except Exception:
                pass

    def run(self) -> None:
        """Run the detection, and say it has finished however it ended —
        the window waits on ``finished`` to enable its controls again."""
        try:
            self._detect()
        except Exception as exc:  # whatever it was: reported, never swallowed
            log.exception("hotcue detection failed")
            self.error.emit(f"Hotcue detection failed: {exc}")
            self.finished.emit(0, self.nml_path)

    def _detect(self) -> None:
        total = len(self.candidates)

        if not self.candidates or self._stop:
            self.finished.emit(0, self.nml_path)
            return

        # ── Parallel detection ────────────────────────────────────────────
        hotcues_by_path: dict[str, list[tuple[float, str, int]]] = {}
        done = 0

        self._pool = ProcessPoolExecutor(
            max_workers=self._n_workers,
            initializer=_hotcue_init,
            initargs=(self._threads_per_worker,),
        )
        try:
            futures = {
                self._pool.submit(detect_one, path, bpm, phase_ms, 7): path
                for path, bpm, phase_ms in self.candidates
            }
            for future in as_completed(futures):
                done += 1
                path = futures[future]
                name = Path(path).name
                self.progress.emit(done, total, name)
                if self._stop:
                    self._pool.shutdown(wait=False, cancel_futures=True)
                    break
                try:
                    _, cues, reason = future.result()
                    if cues:
                        hotcues_by_path[path] = cues
                        self.outcome["cued"] += 1
                        log.debug("detected %d cues for %s", len(cues), name)
                    else:
                        key = f"none: {reason or 'no reason given'}"
                        self.outcome[key] = self.outcome.get(key, 0) + 1
                        log.debug("no cues detected for %s (%s)", name, reason)
                except Exception as exc:
                    self.outcome["failed"] += 1
                    log.warning("hotcue detection failed for %s: %s", name, exc)
                    self.error.emit(f"{name}: {exc}")
        finally:
            self._pool.shutdown(wait=False, cancel_futures=True)
            self._pool = None
        log.info(
            "hotcue detection on %d of %d track(s): %s",
            done,
            total,
            ", ".join(f"{v} {k}" for k, v in self.outcome.items()),
        )

        if not hotcues_by_path:
            self.finished.emit(0, self.nml_path)
            return
        if not self.write_nml:
            # Inside Traktor: the cues go in live, on Traktor's main thread (the receiver's).
            self.detected.emit(hotcues_by_path)
            return

        # ── Single-pass NML write (runs even after a user stop) ───────────
        try:
            self.progress.emit(done, total, "Writing hotcues to collection.nml…")
            count = write_hotcues_to_nml(Path(self.nml_path), hotcues_by_path, force=self.force)
            self.outcome["written"] = count
            log.info(
                "wrote hotcues for %d of %d cued track(s) → %s",
                count,
                len(hotcues_by_path),
                self.nml_path,
            )
            self.finished.emit(count, self.nml_path)
        except Exception as exc:
            log.error("NML write failed: %s", exc, exc_info=True)
            self.error.emit(f"NML write failed: {exc}")
            self.finished.emit(0, self.nml_path)
