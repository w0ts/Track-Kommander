"""Run a device export off the UI thread.

The export used to run inline in the click handler, pumping
``QApplication.processEvents()`` between tracks to keep the window painting.
That works until it doesn't: the event loop is re-entered from inside a long
operation, so a second click, a close, or a repaint that itself takes time
runs *within* the export. It also meant the Stop the underlying functions
already support (:func:`core.device_export.export` and
:func:`workers.waveforms.analyse_many` both take ``should_stop``) could not be
wired to anything, because nothing was there to press.

Everything the export needs is resolved on the UI thread and handed over: the
tracks, the destination, the seed, the playlists. This thread only reads them.
The one shared resource is the waveform cache, which is SQLite with its own
locking and is written a row at a time.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QThread, Signal

from core import m3u, traktor_playlists, waveform_cache
from core.device_export import (
    analysis_plan,
    export,
    find_orphans,
    needs_analysis,
    remove_orphans,
)
from core.eta import Eta
from core.playlists import PlaylistKey
from core.usb_export import strip_macos_metadata
from workers.waveforms import analyse_many

if TYPE_CHECKING:  # pragma: no cover
    from core.device_export import DeviceReport, DeviceTrack

log = logging.getLogger(__name__)


@dataclass
class ExportOutcome:
    """Everything the dialog needs to describe what happened."""

    report: DeviceReport | None = None
    from_cache: int = 0
    cached_now: int = 0
    m3u_written: int = 0
    nml_written: int = 0
    stopped: bool = False
    orphans_found: int = 0
    orphans_bytes: int = 0
    orphans_removed: int = 0
    errors: list[str] = field(default_factory=list)
    no_waveform: list[str] = field(default_factory=list)
    """File names of tracks that went on the key without a waveform,
    because their audio could not be read."""


def _lap(t0: float, what: str) -> float:
    """Log how long a step took; returns the start of the next one."""
    now = time.monotonic()
    log.info("export: %s took %.1fs", what, now - t0)
    return now


class DeviceExportWorker(QThread):
    """Analyse, copy, and write the database for one stick."""

    #: (done, total, what) — *total* counts analysis, copying and writing
    #: the analysis files as one run, a third each.
    progress = Signal(int, int, str)
    #: The export finished, was stopped, or failed cleanly.
    done = Signal(object)
    #: Something went wrong that the export could not carry on from.
    error = Signal(str)

    def __init__(
        self,
        tracks: list[DeviceTrack],
        dest: Path,
        seed: Path,
        *,
        playlists: dict[PlaylistKey, list[int]],
        merge: bool,
        refresh_existing: bool,
        reanalyse: bool,
        sweep_orphans: bool = False,
        nml_path: Path | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._tracks = tracks
        self._dest = Path(dest)
        self._seed = Path(seed)
        self._playlists = playlists
        self._merge = merge
        self._refresh = refresh_existing
        self._reanalyse = reanalyse
        self._sweep = sweep_orphans
        self._nml_path = Path(nml_path) if nml_path else None
        self._stop = False

    def stop(self) -> None:
        """Ask the export to stop at the next track boundary."""
        self._stop = True

    def _should_stop(self) -> bool:
        return self._stop

    # ── The run ─────────────────────────────────────────────────────────────

    def run(self) -> None:
        try:
            self._run()
        except Exception as exc:
            log.exception("device export failed")
            self.error.emit(f"{type(exc).__name__}: {exc}")

    def _run(self) -> None:
        tracks = self._tracks
        n = max(1, len(tracks))
        total = 3 * n
        out = ExportOutcome()

        # Decoding is the largest cost of an export, so work out first which
        # tracks actually need it. A track nothing changed about needs none;
        # one whose cues changed needs none either, because the waveform on
        # the key is still correct and export() reads it back.
        plan = analysis_plan(
            self._dest,
            tracks,
            merge=self._merge,
            refresh_existing=self._refresh,
            reanalyse=self._reanalyse,
        )
        needed = needs_analysis(plan, tracks)

        # The stick's own analysis only helps on the stick that has it, so a
        # second key would decode the whole library again. Our own cache does
        # not care which drive this is: anything analysed for an earlier
        # export is read straight back. One query for the whole selection.
        if not self._reanalyse and needed:
            self.progress.emit(0, total, "Looking for analysis done before…")
            hits = waveform_cache.load_many([t.source for t in needed])
            uncached = []
            for t in needed:
                cached = hits.get(str(t.source))
                if cached is None:
                    uncached.append(t)
                    continue
                t.analysis = cached
                t.waveform = list(cached.preview)
                out.from_cache += 1
            needed = uncached

        if self._stop:
            out.stopped = True
            self.done.emit(out)
            return

        # One decode gives the overview, the scrolling detail and the colour
        # waveforms. They are independent per track and the work is CPU-bound,
        # so they run across the machine's cores rather than one at a time.
        analysed = len(tracks) - len(needed)
        self.progress.emit(analysed, total, f"{analysed} track(s) already analysed")
        # Each phase runs at its own rate — decoding is seconds per track,
        # copying is MB/s — so the estimate is per phase, never over the bar.
        eta = Eta()
        eta.start(len(needed))
        for done, (track, wf) in enumerate(
            analyse_many(needed, should_stop=self._should_stop), start=1
        ):
            analysed += 1
            track.analysis = wf
            track.waveform = list(wf.preview) if wf else []
            # Stored as each one lands rather than in one batch at the end, so
            # an export stopped or crashed half way keeps what it had already
            # paid for.
            if wf is not None:
                waveform_cache.store(track.source, wf)
                out.cached_now += 1
            else:
                # Still exported: a flat waveform is a cosmetic loss. But
                # said, here and in the dialog — it used to pass in silence.
                out.no_waveform.append(track.source.name)
                log.warning("no waveform for %s: its audio could not be read", track.source)
            self.progress.emit(
                analysed,
                total,
                eta.label(f"Analysing {analysed}/{len(tracks)} — {track.source.name}", done),
            )

        if self._stop:
            out.stopped = True
            self.done.emit(out)
            return

        # The copy used to be the last thing the bar heard from; the analysis
        # files, the database and the sweeps ran in silence, which at a few
        # hundred tracks on a slow stick reads as a hang.
        # The callbacks report a unit as it *starts*, so the rate is over the
        # units before it.
        def _progress(copied: int, of: int, name: str) -> None:
            if copied == 1:
                eta.start(of)
            self.progress.emit(
                n + copied, total, eta.label(f"Copying {copied}/{of} — {name}", copied - 1)
            )

        def _analysis(written: int, of: int, name: str) -> None:
            if written == 1:
                eta.start(of)
            self.progress.emit(
                2 * n + written,
                total,
                eta.label(f"Writing analysis {written}/{of} — {name}", written - 1),
            )

        def _phase(what: str) -> None:
            self.progress.emit(total, total, what)

        report = export(
            tracks,
            self._dest,
            self._seed,
            playlists=self._playlists,
            merge=self._merge,
            refresh_existing=self._refresh,
            reanalyse=self._reanalyse,
            progress=_progress,
            should_stop=self._should_stop,
            analysis_progress=_analysis,
            phase=_phase,
        )
        out.report = report
        out.stopped = self._stop
        t0 = time.monotonic()

        # Plain playlists too, for everything that is not a CDJ. Cheap, and
        # a failure here must not cost the export that already succeeded.
        _phase("Writing .m3u8 playlists…")
        try:
            out.m3u_written = len(m3u.write_playlists(self._dest, tracks, self._playlists))
        except Exception as exc:
            report.errors.append(f"m3u8: {exc}")

        # Traktor playlists over the same copies, carrying the collection's
        # own cues and grid. Same rule as the m3u8: never costs the export.
        t0 = _lap(t0, "m3u8 playlists")
        _phase("Writing Traktor playlists…")
        try:
            out.nml_written = len(
                traktor_playlists.write_playlists(
                    self._dest, tracks, self._playlists, self._nml_path
                )
            )
        except Exception as exc:
            report.errors.append(f"Traktor playlists: {exc}")

        # The export sweeps macOS sidecars before returning, but the playlists
        # are written after that — so writing them leaves fresh ._ files
        # behind. Sweep again now that nothing else will touch the stick.
        t0 = _lap(t0, "Traktor playlists")
        _phase("Removing macOS sidecars…")
        report.sidecars_removed += strip_macos_metadata(self._dest)
        t0 = _lap(t0, "second sidecar sweep")

        # Only once the database is written, and only against that database:
        # what counts as an orphan is defined by the rows that now exist.
        # Always *reported*, only removed when asked — this deletes audio.
        if report.db_path is not None:
            self.progress.emit(total, total, "Checking for files no longer listed…")
            try:
                orphans = find_orphans(self._dest)
                out.orphans_found = orphans.count
                out.orphans_bytes = orphans.bytes
                if self._sweep and orphans:
                    out.orphans_removed, errs = remove_orphans(self._dest, orphans)
                    report.errors.extend(errs)
            except Exception as exc:
                report.errors.append(f"orphan sweep: {exc}")
            _lap(t0, "orphan check")

        self.progress.emit(total, total, "Finishing…")
        self.done.emit(out)
