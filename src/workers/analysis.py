"""AnalysisWorker — QThread that analyses audio files.

Architecture
------------
Each task runs inside a ThreadPoolExecutor:
  read metadata → analyse_file (signal + ML) → upsert_track

soundfile (libsndfile) and PyTorch both release the GIL during their
heavy work, so threading gives real parallelism while sharing a single
CLAP model instance (~600 MB) — vs ProcessPoolExecutor which would
duplicate it per-worker and thrash MPS.

libsndfile's mpg123 backend has a one-time global init that races under
concurrent first-MP3 open. We pre-warm it from the main thread before
launching the pool — see ``core.audio_io.warmup``.

The QThread collects results and emits Qt signals.
"""

from __future__ import annotations

import logging
import os
import sys
from collections import defaultdict
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path

import numpy as np
from PySide6.QtCore import QThread, Signal

from core.analysis import (
    AnalysisCancelled,
    preload_traktor_collection,
    reload_traktor_collection,
)
from core.clap_analysis import clap_wants_gpu
from core.clap_models import ensure_clap_downloaded
from core.db import (
    batch_update_bpm,
    batch_update_key,
    batch_update_metadata,
    get_metadata_batch,
    get_up_to_date_paths,
    remove_tracks_not_in,
    upsert_embedding,
    upsert_track,
)
from core.embeddings import ready_table_models
from core.metadata_refresh import plan_refresh
from core.scan_plan import features_to_compute, stale_paths

log = logging.getLogger("analysis_worker")

# A representative column per compute group. All features are always computed;
# requiring every sentinel means a scan backfills any track missing a feature.
_GROUP_SENTINEL: dict[str, str] = {
    "signal": "danceability",
    "melodic": "melodic",
    # Every shape feature needs the tempo, so a track Traktor has no BPM for
    # leaves these NULL and is offered to every scan. That is the right
    # answer — it becomes analysable the moment Traktor grids it.
    "shape": "sameness",
    "ml": "genres_vector",
}


def _enabled_compute_groups() -> tuple[frozenset[str], list[str]]:
    """All features are always computed. Returns (compute_set, required_columns)."""
    return frozenset(_GROUP_SENTINEL), list(_GROUP_SENTINEL.values())


def _wanted_mask() -> int:
    """Every feature the user has left switched on for computing.

    The first of the two switches: this one decides what a scan costs. The
    second — whether a computed feature ranks tracks — lives in the stats
    dashboard and costs nothing.
    """
    from core.feature_bits import enabled_mask
    from core.scan_plan import computable_features

    return enabled_mask([f.key for f in computable_features()])


def _enabled_embedding_models() -> tuple[str, ...]:
    """Table-storage embedding models a scan computes by default.

    Only models whose ONNX artefact is actually present are returned, so a
    default-on model can't force a pointless full re-scan before it's been
    exported. Once the artefact lands, the next scan backfills it incrementally.
    """
    return tuple(m.key for m in ready_table_models())


# ── Feature metadata for timing display ───────────────────────────────────────
# Maps timing key → (display name, processor label)
FEATURE_INFO: dict[str, tuple[str, str]] = {
    "audio_load": ("Duration", "container header"),
    "audio_decode": ("Audio decode", "soundfile / PyAV, native rate"),
    "librosa_load": ("Resample 22 kHz", "librosa"),
    "bpm": ("BPM", "Traktor metadata"),
    "key": ("Key", "Traktor metadata"),
    "danceability": ("Danceability", "librosa: onset ACF"),
    "dynamic_complexity": ("Dynamic complexity", "librosa: RMS std"),
    "hfc": ("HF energy", "librosa: mean energy >5 kHz"),
    "low_energy": ("Bass energy", "librosa: mean energy <200 Hz"),
    "mid_energy": ("Mid energy", "librosa: mean energy 200–5k Hz"),
    "loudness": ("Loudness", "librosa: mean RMS"),
    "bassiness": ("Bassiness", "librosa: low / total energy ratio"),
    "low_dynamics": ("Bass dynamics", "librosa: RMS std <200 Hz"),
    "mid_dynamics": ("Mid dynamics", "librosa: RMS std 200–5k Hz"),
    "high_dynamics": ("Hi dynamics", "librosa: RMS std >5 kHz"),
    "spectral_centroid": ("Spectral centroid", "librosa: centroid"),
    "melodic": ("Melodic ratio", "librosa: HPSS harmonic/total RMS"),
    # CLAP / PyTorch backend
    "clap_load": ("Resample 48 kHz (CLAP)", "librosa"),
    "clap_embed": ("CLAP embedding", "PyTorch: laion/clap-htsat-fused"),
    "clap_classify": ("CLAP classify", "PyTorch: zero-shot 6 features"),
    "clap_deam": ("Arousal / Valence", "PyTorch: CLAP 9-anchor mean"),
    "clap_genre": ("Genre (CLAP)", "PyTorch: zero-shot Discogs400"),
}

_ML_KEYS = (
    "is_vocal",
    "mood_party",
    "mood_aggressive",
    "mood_happy",
    "mood_relaxed",
    "arousal",
    "valence",
    "is_electronic",
    "genres",
    "genres_vector",
    "melodic",
)


# ── Worker-count autodetect ───────────────────────────────────────────────────

# Memory per worker thread: its decoded audio, librosa's scratch and the CLAP
# forward's activations. The model's weights are shared and not counted.
# Measured over the whole analysis (tests/unit/test_worker_count.py has the
# table): 490–560 MB a thread on ordinary tracks, 630–740 MB on the longest
# in the library. It used to say 250 MB, which no run came near.
_WORKER_RAM_BUDGET_BYTES: int = 750_000_000

#: The most threads when CLAP runs on a GPU. Its forward is serialised there
#: (see core.clap_analysis), so threads beyond this only queue behind it.
GPU_PATH_CAP = 6

#: The most threads when everything runs on the CPU. Every stage is parallel
#: there. Measured on 16 cores: throughput rose to here and then flattened
#: on long tracks (2.78 tracks a second at both 10 and 12 threads) while
#: memory went on climbing.
CPU_PATH_CAP = 10

#: How often the scan loop looks at the stop flag while every thread is busy.
_STOP_POLL_SEC = 0.2


def _available_ram_bytes() -> int:
    """Best-effort available-RAM estimate using only stdlib.

    On macOS, ``vm_stat`` reports free + inactive + speculative + purgeable
    + file-backed pages, all of which can be reclaimed under pressure.
    On Windows, ``GlobalMemoryStatusEx`` reports available physical RAM.
    Falls back to total RAM (then 8 GB) if everything else fails.
    """
    if sys.platform == "win32":
        try:
            import ctypes

            class _MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat = _MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(_MEMORYSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
                return int(stat.ullAvailPhys)
        except Exception:
            pass
        return 8 * 1024**3

    try:
        import re
        import subprocess

        out = subprocess.run(
            ["vm_stat"], capture_output=True, text=True, check=True, timeout=2
        ).stdout
        psize = int(re.search(r"page size of (\d+)", out).group(1))
        keys = (
            "Pages free",
            "Pages inactive",
            "Pages speculative",
            "Pages purgeable",
            "File-backed pages",
        )
        pages = sum(int(re.search(rf"{k}:\s+(\d+)", out).group(1)) for k in keys)
        return pages * psize
    except Exception:
        try:
            return os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
        except (OSError, ValueError, AttributeError):
            return 8 * 1024**3


def _autodetect_n_workers(gpu: bool = True) -> int:
    """Pick a worker count that fits the CPU, the memory and the CLAP path.

    *gpu* says whether CLAP will run on a GPU. The default is the cautious
    answer: the lower cap.

    Bounded by:
      * CPU — keep ≥25 % of cores free for the system and Traktor.
      * RAM — what each worker thread was measured to use.
      * The path — :data:`GPU_PATH_CAP` or :data:`CPU_PATH_CAP`.
    """
    n_cpus = os.cpu_count() or 4
    cpu_cap = max(1, n_cpus - max(1, n_cpus // 4))
    # One probe: on macOS each one forks vm_stat.
    avail = _available_ram_bytes()
    ram_cap = max(1, avail // _WORKER_RAM_BUDGET_BYTES)
    path_cap = GPU_PATH_CAP if gpu else CPU_PATH_CAP
    chosen = min(cpu_cap, ram_cap, path_cap)
    log.info(
        "autodetect workers: cpu=%d ram=%d %s=%d (avail=%.1f GB) → %d",
        cpu_cap,
        ram_cap,
        "gpu" if gpu else "cpu-path",
        path_cap,
        avail / 1e9,
        chosen,
    )
    return chosen


def current_masks() -> dict[str, int]:
    """Every track's computed mask, read once for the whole scan."""
    from core.db import get_conn

    with get_conn() as c:
        return {row[0]: (row[1] or 0) for row in c.execute("SELECT path, computed FROM tracks")}


def computed_mask(result: dict) -> int:
    """The bit of every feature this analysis actually produced.

    Read from the result rather than from what was asked for: a group that
    was skipped — no BPM for the bar-aligned ones, an unreadable file — must
    not be recorded as done, or the track never gets a second chance.
    """
    from core.feature_bits import mask_for
    from core.features import FEATURES

    return mask_for([f.key for f in FEATURES if f.column and result.get(f.column) is not None])


def _analyse_one(
    path: str,
    compute: frozenset[str] | None = None,
    embed_models: tuple[str, ...] = (),
    should_stop: Callable[[], bool] | None = None,
) -> dict:
    """Full analysis for a single file — runs inside a worker thread.

    Raises ``AnalysisCancelled`` when *should_stop* turns True mid-track.
    """
    from core.analysis import analyse_file, read_metadata

    artist, album, title, genre, musical_key, traktor_bpm, rating, extra = read_metadata(path)

    features, timings = analyse_file(
        path,
        traktor_musical_key=musical_key,
        traktor_bpm=traktor_bpm,
        compute=compute,
        embed_models=embed_models,
        should_stop=should_stop,
    )

    result: dict = {
        "path": path,
        "artist": artist,
        "album": album,
        "title": title,
        "genre": genre,
        "rating": rating,
        "_timings": timings,
        **extra,
    }
    result.update(features)
    return result


class AnalysisWorker(QThread):
    progress = Signal(int, int, str)
    finished = Signal(int, int, dict)  # (analysed, meta_updated, timing_stats)
    error = Signal(str)

    def __init__(
        self,
        paths: list[str],
        force: bool = False,
        n_workers: int | None = None,
        parent=None,
        sync_db: bool = False,
    ):
        super().__init__(parent)
        self.paths = paths
        self.force = force
        self._stop = False
        # When True, a completed scan prunes DB rows whose path is not in this
        # run's path list — keeping the DB mirrored to the scanned collection.
        # Only the full library scan sets this; single-track re-analyse must not.
        self.sync_db = sync_db
        # Snapshot enabled compute groups now (caller thread) so the scan and
        # the up-to-date check agree on what counts as fully analysed.
        self._compute, self._required_cols = _enabled_compute_groups()
        self._wanted = _wanted_mask()
        self._embed_models = _enabled_embedding_models()
        log.info("compute groups enabled: %s", sorted(self._compute) or ["core only"])
        if self._embed_models:
            log.info("embedding models enabled: %s", list(self._embed_models))
        # Filled in run(), off the constructing thread: reading every track's
        # mask is a full-table SELECT, and the RAM probe forks vm_stat — too
        # slow for the GUI thread, which builds a worker on a right-click
        # Re-analyse.
        self._masks: dict[str, int] = {}
        self._n_workers: int | None = n_workers

    def groups_for(self, path: str) -> frozenset[str]:
        """The compute groups this track still needs.

        Compute stays grouped — the features share a decode, an HPSS and a
        CQT, so running them one at a time would pay that repeatedly — while
        the accounting is per feature. A forced re-analysis runs everything.
        """
        if self.force:
            return self._compute
        needed = features_to_compute(have=self._masks.get(path), want=self._wanted)
        return frozenset(needed & set(self._compute))

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        """Run the scan, and say it has finished however it ended.

        The window re-enables its controls on ``finished``. A scan that
        raised — a locked database is enough — used to end the thread without
        it, leaving Scan and the results table disabled with no reason given.
        """
        try:
            self._scan()
        except Exception as exc:  # whatever it was: reported, never swallowed
            log.exception("scan failed")
            self.error.emit(f"Scan failed: {exc}")
            self.finished.emit(0, 0, {})

    def _scan(self) -> None:
        # In the compiled (Nuitka) app, apply the frozen-bundle shims BEFORE the
        # librosa imports below: register the missing librosa.core.fft module and
        # install a numba cache locator that works without on-disk sources.
        # Without them the scan crashes with "No module named 'librosa.core.fft'"
        # and "no locator available" for librosa's @guvectorize functions. No-op
        # in dev. (Analysis runs in this process's thread pool, so a one-time
        # call here covers all analysis threads.)
        from core.runtime import is_frozen

        if is_frozen():
            from core.frozen_runtime import apply_frozen_runtime_shims

            apply_frozen_runtime_shims()

        # Ensure CLAP model is present before starting (only when ML is enabled —
        # disabling it is what makes a scan fast and avoids the ~110 MB download).
        if "ml" in self._compute:
            try:

                def _dl_progress(filename: str, current: int, total: int) -> None:
                    self.progress.emit(
                        current, total, f"Downloading model {current + 1}/{total}: {filename}"
                    )

                ensure_clap_downloaded(progress_cb=_dl_progress)
            except Exception as exc:
                log.error("Model download failed: %s", exc, exc_info=True)
                self.error.emit(f"Model download failed: {exc}")
                self.finished.emit(0, 0, {})
                return
            log.info("CLAP model ready — starting analysis of %d tracks", len(self.paths))
        else:
            log.info("ML disabled — starting analysis of %d tracks (no CLAP)", len(self.paths))

        reload_traktor_collection()
        if not self.force:
            self._masks = current_masks()
        if self._n_workers is None:
            self._n_workers = _autodetect_n_workers(gpu="ml" in self._compute and clap_wants_gpu())

        total = len(self.paths)

        # Split: already analysed (metadata refresh only) vs needs audio work
        meta_only: list[str] = []
        todo: list[str] = []
        missing_ct: int = 0
        # Per feature, not a global version: a track is work only when it
        # lacks a feature that is switched on. Changing one feature's
        # definition clears one bit and backfills that feature alone.
        if self.force:
            analyzed_set = set()
        else:
            stale = stale_paths(self._wanted)
            embedded = get_up_to_date_paths([], self._embed_models)
            analyzed_set = {
                p
                for p in self.paths
                if p not in stale and (not self._embed_models or p in embedded)
            }
        for path in self.paths:
            if not Path(path).exists():
                missing_ct += 1
                log.warning("skipping missing file: %s", path)
                continue
            if not self.force and path in analyzed_set:
                meta_only.append(path)
            else:
                todo.append(path)
        if missing_ct:
            log.info("skipped %d missing/non-existent files", missing_ct)

        # ── Batch metadata refresh ────────────────────────────────────────────
        meta_updated = 0
        if meta_only and not self._stop:
            n = len(meta_only)
            self.progress.emit(0, total, f"Checking metadata for {n} tracks…")
            existing = get_metadata_batch(meta_only)
            _meta_step = max(1, n // 100)

            def _meta_tick(meta_idx: int) -> None:
                if meta_idx % _meta_step == 0:
                    self.progress.emit(meta_idx, total, f"Checking metadata [{meta_idx}/{n}]…")

            refresh = plan_refresh(
                meta_only, existing, should_stop=lambda: self._stop, tick=_meta_tick
            )
            if refresh.unlisted:
                log.warning(
                    "metadata: %d of %d track(s) are not in Traktor's "
                    "collection — left as they are",
                    refresh.unlisted,
                    n,
                )
            meta_updates = list(refresh.metadata)
            key_updates = list(refresh.keys)
            bpm_updates = list(refresh.tempos)
            meta_updated = len(meta_updates)
            if meta_updates and not self._stop:
                try:
                    batch_update_metadata(meta_updates)
                except Exception as exc:
                    self.error.emit(f"Metadata batch update: {exc}")
            if key_updates and not self._stop:
                try:
                    batch_update_key(key_updates)
                except Exception as exc:
                    self.error.emit(f"Key batch update: {exc}")
            if bpm_updates and not self._stop:
                try:
                    batch_update_bpm(bpm_updates)
                except Exception as exc:
                    self.error.emit(f"BPM batch update: {exc}")
            self.progress.emit(n, total, f"Metadata: {meta_updated} of {n} changed")

        # ── Audio analysis ────────────────────────────────────────────────────
        analysed = 0
        timing_stats: dict[str, list[float]] = defaultdict(list)
        # Tracks Traktor has no BPM for. Structure and bassline are measured
        # in bars, so they are skipped for these — and silently, per track,
        # unless the scan says so once at the end.
        no_bpm: list[str] = []

        def _save_result(r: dict, path: str) -> None:
            filename = Path(path).name
            if not r.get("bpm"):
                no_bpm.append(filename)
            track_timings: dict = r.pop("_timings", {})
            for feat, elapsed in track_timings.items():
                timing_stats[feat].append(elapsed)
            # Per-model embedding vectors go to the side table, keyed by path.
            embs: dict = r.pop("_embeddings", {}) or {}
            for model_key, blob in embs.items():
                upsert_embedding(r["path"], model_key, blob, len(blob) // 4)
            upsert_track(
                r["path"],
                filename,
                r.get("bpm"),
                r.get("key_num"),
                r.get("is_major"),
                r.get("camelot"),
                r.get("duration"),
                artist=r.get("artist"),
                album=r.get("album"),
                title=r.get("title"),
                genre=r.get("genre"),
                rating=r.get("rating"),
                release_date=r.get("release_date", ""),
                import_date=r.get("import_date", ""),
                label=r.get("label", ""),
                bitrate=r.get("bitrate"),
                play_count=r.get("play_count"),
                danceability=r.get("danceability"),
                dynamic_complexity=r.get("dynamic_complexity"),
                hfc=r.get("hfc"),
                low_energy=r.get("low_energy"),
                mid_energy=r.get("mid_energy"),
                loudness=r.get("loudness"),
                bassiness=r.get("bassiness"),
                low_dynamics=r.get("low_dynamics"),
                mid_dynamics=r.get("mid_dynamics"),
                high_dynamics=r.get("high_dynamics"),
                spectral_centroid=r.get("spectral_centroid"),
                is_vocal=r.get("is_vocal"),
                mood_party=r.get("mood_party"),
                mood_aggressive=r.get("mood_aggressive"),
                mood_happy=r.get("mood_happy"),
                mood_relaxed=r.get("mood_relaxed"),
                arousal=r.get("arousal"),
                valence=r.get("valence"),
                is_electronic=r.get("is_electronic"),
                genres=r.get("genres"),
                genres_vector=r.get("genres_vector"),
                melodic=r.get("melodic"),
                loop_1bar=r.get("loop_1bar"),
                loop_4bar=r.get("loop_4bar"),
                sameness=r.get("sameness"),
                evolution=r.get("evolution"),
                sections_per_min=r.get("sections_per_min"),
                bass_notes_per_bar=r.get("bass_notes_per_bar"),
                bass_range=r.get("bass_range"),
                bass_jump=r.get("bass_jump"),
                bass_rest=r.get("bass_rest"),
                bass_is_major=r.get("bass_is_major"),
                bounce=r.get("bounce"),
                onset_density=r.get("onset_density"),
                computed=computed_mask(r),
            )

        def _handle_exc(exc: Exception, path: str) -> None:
            filename = Path(path).name
            msg = str(exc)
            _unreadable = (
                "No such file" in msg
                or "Could not open file" in msg
                or "swresample" in msg
                or "Invalid data" in msg
                or "moov atom" in msg
                or "Unspecified internal error" in msg  # malformed MP3 frame
                or "Format not recognised" in msg
                or "no audio frames" in msg  # PyAV decoded nothing
            )
            if _unreadable:
                log.warning("skipping unreadable file: %s — %s", filename, msg)
            else:
                log.error("analysis failed for %s: %s", filename, exc, exc_info=True)
            self.error.emit(f"{filename}: {exc}")

        def _thread_init() -> None:
            """Per-thread initializer: macOS scheduling hints."""
            try:
                import ctypes as _ct
                import ctypes.util as _cu

                _pthread = _ct.CDLL(_cu.find_library("pthread") or "libpthread.dylib")
                _pthread.pthread_set_qos_class_self_np(0x11, 0)  # QOS_CLASS_UTILITY
            except Exception:
                pass

        def _run_threaded(paths: list[str], n_threads: int, progress_offset: int) -> int:
            """Threads share one CLAP ONNX session; onnxruntime releases the
            GIL during inference so threading scales.
            """
            # Pre-warm CLAP once on the main thread so workers don't race
            # to load the ONNX session on their first call. Skipped when ML is off.
            if "ml" in self._compute:
                try:
                    from core.clap_analysis import _get_clap_backend

                    _get_clap_backend()
                except Exception as exc:
                    log.warning("CLAP pre-warm failed: %s", exc)

            # Pre-warm libsndfile/mpg123 on the main thread so the global
            # mpg123_init can never race under concurrent first-MP3 opens.
            # Try a few MP3s — some entries may be missing/unreadable.
            from core.audio_io import warmup as _audio_warmup

            for p in paths:
                if not p.lower().endswith(".mp3"):
                    continue
                if not Path(p).is_file():
                    continue
                if _audio_warmup(p):
                    break

            import time as _time

            t_start = _time.monotonic()
            t_last_log = t_start

            # Every track reads the Traktor collection. Parse it once here
            # rather than in whichever pool threads get there first.
            preload_traktor_collection()

            def _should_stop() -> bool:
                return self._stop

            log.info("analysis: dispatching %d files across %d threads", len(paths), n_threads)
            count = 0
            # Not a `with` block: its exit joins every worker, so a stop waited
            # for each track in flight — a full decode and CLAP pass per
            # thread, longer than the main window waits for a closing worker.
            # Instead, tracks in flight see the stop between stages and bail
            # out (AnalysisCancelled), queued ones are cancelled, and whatever
            # still finishes after the stop is never collected, so never saved.
            pool = ThreadPoolExecutor(max_workers=n_threads, initializer=_thread_init)
            try:
                futures = {
                    pool.submit(
                        _analyse_one, p, self.groups_for(p), self._embed_models, _should_stop
                    ): p
                    for p in paths
                }
                pending = set(futures)
                done = 0
                while pending and not self._stop:
                    # A timeout, so a stop is noticed while every thread is
                    # still mid-track.
                    finished, pending = wait(
                        pending, timeout=_STOP_POLL_SEC, return_when=FIRST_COMPLETED
                    )
                    for future in finished:
                        if self._stop:
                            break
                        done += 1
                        path = futures[future]
                        self.progress.emit(progress_offset + done, total, Path(path).name)
                        try:
                            r = future.result()
                            _save_result(r, path)
                            count += 1
                        except AnalysisCancelled:
                            # Only raised once a stop was requested; the track
                            # is left for the next scan.
                            log.debug("analysis cancelled mid-track: %s", path)
                        except Exception as exc:
                            _handle_exc(exc, path)

                    # Stdout heartbeat: every 5 s, summarise progress so a
                    # terminal-only user can see something is happening.
                    now = _time.monotonic()
                    if finished and now - t_last_log >= 5.0:
                        elapsed = now - t_start
                        rate = count / elapsed if elapsed > 0 else 0
                        remain = (len(paths) - done) / rate if rate > 0 else 0
                        log.info(
                            "analysis: %d/%d done (%.1f tracks/s, ~%.0fs remaining)",
                            done,
                            len(paths),
                            rate,
                            remain,
                        )
                        t_last_log = now
            finally:
                # After a stop, return at once: in-flight tracks end at their
                # next stage check and their results are dropped.
                pool.shutdown(wait=not self._stop, cancel_futures=True)
            return count

        if todo and not self._stop:
            analysed = _run_threaded(todo, self._n_workers, meta_updated)

        timing_report = {
            feat: float(np.mean(times)) for feat, times in timing_stats.items() if times
        }

        # Mirror the DB to the scanned collection: prune rows whose path isn't
        # in this scan's list (orphans from a folder rename / removed track,
        # whose audio may still be on disk). Full scans only, and never on a
        # cancelled run or an empty list — both could wrongly wipe the DB.
        if self.sync_db and not self._stop and self.paths:
            try:
                pruned = remove_tracks_not_in(self.paths)
                if pruned:
                    log.info(
                        "library sync: removed %d orphan track(s) no longer in the collection",
                        pruned,
                    )
            except Exception as exc:
                log.error("library sync prune failed: %s", exc, exc_info=True)

        log.info(
            "scan complete — analysed=%d  meta_updated=%d  missing=%d",
            analysed,
            meta_updated,
            missing_ct,
        )
        if no_bpm:
            log.warning(
                "structure/bassline skipped for %d track(s) with no BPM "
                "in Traktor (analyse them in Traktor first), e.g. %s",
                len(no_bpm),
                ", ".join(no_bpm[:5]),
            )
        self.finished.emit(analysed, meta_updated, timing_report)
