"""Library tab, scan: analyse the collection's tracks and report on it.

Starts the analysis worker, shows progress, the time left and the per-feature
timing, and keeps the library summary current. Mixed into
:class:`ui.library.LibraryTab`; the worker is kept as ``self._worker`` because
the main window reads it by that name when the app quits.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
)

from core.collection_source import collection_paths
from core.db import _db_path, analysis_gaps, remove_missing_tracks
from ui.library_helpers import _mtime_or_zero
from workers.analysis import FEATURE_INFO, AnalysisWorker

log = logging.getLogger(__name__)


class _ScanMixin:
    def _build_scan_top(self, root: QVBoxLayout) -> None:
        src = QGroupBox("TRACK SOURCE")
        src_lay = QVBoxLayout(src)
        src_lay.setSpacing(6)

        # The collection isn't picked anywhere — it's the library of the
        # Traktor selected in Settings. This only reports what that resolved to.
        self.nml_label = QLabel("")
        self.nml_label.setStyleSheet("color: #9aa6b6; font-size: 11px;")
        self.nml_label.setWordWrap(True)
        src_lay.addWidget(self.nml_label)

        hint = QLabel("Follows the Traktor chosen in Settings → Traktor.")
        hint.setStyleSheet("color: #7777aa; font-size: 11px;")
        src_lay.addWidget(hint)
        root.addWidget(src)

        ctrl_row = QHBoxLayout()
        ctrl_row.setSpacing(10)
        self.scan_btn = QPushButton("Scan && Analyse")
        self.scan_btn.setObjectName("primary")
        self.scan_btn.clicked.connect(self._start_scan)
        ctrl_row.addWidget(self.scan_btn)
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._stop_scan)
        ctrl_row.addWidget(self.stop_btn)
        self.skip_cb = QCheckBox("Skip already-analysed tracks")
        self.skip_cb.setChecked(True)
        ctrl_row.addWidget(self.skip_cb)
        ctrl_row.addStretch()
        self.check_lib_btn = QPushButton("Check Library…")
        self.check_lib_btn.setToolTip(
            "Try to load every track in Traktor (Deck A & B, muted) and report "
            "any that fail to load or play."
        )
        self.check_lib_btn.clicked.connect(self._open_library_checker)
        ctrl_row.addWidget(self.check_lib_btn)
        root.addLayout(ctrl_row)

        prog = QGroupBox("PROGRESS")
        prog_lay = QVBoxLayout(prog)
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        prog_lay.addWidget(self.progress_bar)
        self.status_lbl = QLabel("Ready")
        self.status_lbl.setStyleSheet("color: #7777aa; font-size: 11px;")
        self.status_lbl.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        prog_lay.addWidget(self.status_lbl)
        root.addWidget(prog)

        stats = QGroupBox("LIBRARY")
        stats_lay = QVBoxLayout(stats)
        self.stats_lbl = QLabel("—")
        self.stats_lbl.setStyleSheet("font-size: 13px; color: #dde1f0;")
        self.stats_lbl.setWordWrap(True)  # names each missing group
        stats_lay.addWidget(self.stats_lbl)
        self.db_path_lbl = QLabel(str(_db_path()))
        self.db_path_lbl.setStyleSheet("font-size: 10px; color: #777799;")
        stats_lay.addWidget(self.db_path_lbl)
        root.addWidget(stats)

    def _build_timing_box(self, root: QVBoxLayout) -> None:
        timing_box = QGroupBox("FEATURE TIMING (avg per track)")
        timing_box.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        timing_lay = QVBoxLayout(timing_box)
        self.timing_lbl = QLabel("Run a scan to see per-feature timing stats.")
        self.timing_lbl.setStyleSheet("font-size: 10px; color: #777799;")
        timing_lay.addWidget(self.timing_lbl)
        self.timing_text = QTextEdit()
        self.timing_text.setReadOnly(True)
        self.timing_text.setVisible(False)
        self.timing_text.setStyleSheet(
            "font-family: monospace; font-size: 11px; background: #1a1a2e; color: #aabbcc;"
            "border: none;"
        )
        self.timing_text.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.timing_text.setMinimumHeight(120)
        timing_lay.addWidget(self.timing_text, 1)
        root.addWidget(timing_box, 1)

    def _collect_paths(self) -> list[str]:
        from ui.dialogs import warn

        nml = self._nml()
        if not nml:
            warn(
                self,
                "No collection",
                "No collection.nml for the Traktor selected in Settings → Traktor.",
            )
            return []
        paths = collection_paths(Path(nml))
        if not paths:
            warn(self, "Empty NML", "No audio tracks found in the NML file.")
            return []
        # Most recently modified files first — new additions get analysed first
        paths.sort(key=_mtime_or_zero, reverse=True)
        return paths

    def _open_library_checker(self) -> None:
        paths = self._collect_paths()
        if not paths:
            return
        # Only check tracks that exist on disk — missing files are a separate
        # category and would just count as load failures every time.
        existing = [p for p in paths if Path(p).exists()]
        if not existing:
            from ui.dialogs import warn

            warn(self, "No tracks", "None of the NML entries exist on disk.")
            return
        self.check_library_requested.emit(existing)

    def _start_scan(self) -> None:
        if not self._warn_traktor_open_for_scan():
            return
        paths = self._collect_paths()
        if not paths:
            return

        removed = remove_missing_tracks()
        if removed:
            self.status_lbl.setText(f"Removed {removed} missing tracks…")

        force = not self.skip_cb.isChecked()
        n_workers: int | None = None

        self._n_tracks = len(paths)
        self.status_lbl.setText(f"Found {self._n_tracks} tracks — checking models…")
        self.scan_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.progress_bar.setMaximum(13)  # will be reset to n_tracks after downloads
        self.progress_bar.setValue(0)
        self._analysis_start = None
        self._analysis_done = 0
        # Full library scan: mirror the DB to this collection (prune orphans
        # left by folder renames / removed tracks) when the scan completes.
        self._worker = AnalysisWorker(paths, force=force, n_workers=n_workers, sync_db=True)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        self._worker.error.connect(self._on_error)
        self._worker.start()
        self.timing_text.setVisible(False)
        self.timing_lbl.setText("Scanning…")

    def _stop_scan(self) -> None:
        if self._worker:
            self._worker.stop()
        self.stop_btn.setEnabled(False)
        self.status_lbl.setText("Stopping…")

    def _on_progress(self, current: int, total: int, filename: str) -> None:
        downloading = filename.startswith("Downloading")

        # Switch progress bar range from model-download mode to track-analysis mode
        if self.progress_bar.maximum() != self._n_tracks and not downloading:
            self.progress_bar.setMaximum(self._n_tracks)
            self._analysis_start = time.monotonic()
            self._analysis_done = 0

        self.progress_bar.setValue(current)

        if not downloading:
            self._analysis_done += 1

        name = filename if len(filename) <= 64 else "…" + filename[-61:]
        eta_str = self._eta_str(current, total, downloading)
        self.status_lbl.setText(f"[{current}/{total}]{eta_str}  {name}")

    def _eta_str(self, current: int, total: int, downloading: bool) -> str:
        """Return a formatted '  ~2h 03m' or '  ~45s' remaining string, or ''."""
        if downloading or self._analysis_start is None or self._analysis_done < 2:
            return ""
        elapsed = time.monotonic() - self._analysis_start
        rate = self._analysis_done / elapsed  # tracks per second
        remaining = (total - current) / rate if rate > 0 else 0
        if remaining < 60:
            return f"  ~{int(remaining)}s"
        elif remaining < 3600:
            return f"  ~{int(remaining // 60)}m {int(remaining % 60):02d}s"
        else:
            h = int(remaining // 3600)
            m = int((remaining % 3600) // 60)
            return f"  ~{h}h {m:02d}m"

    def _on_finished(self, analysed: int, meta_updated: int, timing_report: dict) -> None:
        self.scan_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.progress_bar.setValue(self.progress_bar.maximum())
        parts = [f"{analysed} analysed"]
        if meta_updated:
            parts.append(f"{meta_updated} metadata refreshed")
        self.status_lbl.setText("Done — " + ", ".join(parts))
        self._update_stats()
        self._show_timing(timing_report)
        self.library_changed.emit()
        # Tracks a Spotify playlist placed are in the library now: Discogs.
        self.auto_lookup_playlist_tracks()

    def _show_timing(self, timing_report: dict) -> None:
        if not timing_report:
            self.timing_lbl.setText("No timing data (no new tracks analysed).")
            return

        total_avg = sum(timing_report.values())
        lines = [
            f"{'Feature':<28} {'Processor':<42} {'Avg (s)':>8}",
            "─" * 82,
        ]
        # Sort by descending cost so the slow ones are visible first
        for feat, avg in sorted(timing_report.items(), key=lambda x: -x[1]):
            display_name, processor = FEATURE_INFO.get(feat, (feat, "—"))
            lines.append(f"{display_name:<28} {processor:<42} {avg:>8.3f}")
        lines += [
            "─" * 82,
            f"{'TOTAL per track':<28} {'':42} {total_avg:>8.3f}",
        ]

        self.timing_text.setPlainText("\n".join(lines))
        self.timing_text.setVisible(True)
        self.timing_lbl.setText(
            f"Avg total per track: {total_avg:.2f}s — "
            "disable slow features in core/analysis.py to speed up scans."
        )

    def _on_error(self, msg: str) -> None:
        self.status_lbl.setText(f"⚠  {msg}")

    def _update_stats(self) -> None:
        from core.analysis_status import describe

        try:
            self.stats_lbl.setText(describe(analysis_gaps()))
        except Exception:
            log.warning("could not read the analysis state", exc_info=True)
            self.stats_lbl.setText("—")
