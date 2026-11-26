"""Library tab, hotcues: detection, cleanup and recompute of auto hotcues.

Writes ``tk: `` hotcues into ``collection.nml`` on a worker thread and removes
them again. Mixed into :class:`ui.library.LibraryTab`; the worker is kept as
``self._hc_worker`` because the main window reads it by that name when the app
quits.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
)

from core.traktor import cleanup_tk_hotcues, hotcue_eligibility
from workers.hotcue_worker import HotcueWorker

log = logging.getLogger(__name__)


class _HotcueMixin:
    def _build_hotcue_box(self) -> None:
        hc_box = QGroupBox("HOTCUES")
        hc_lay = QVBoxLayout(hc_box)
        from core.runtime import in_traktor

        hc_desc = QLabel(
            "Detect drops & breakdowns and write them as hotcues into Traktor's collection.\n"
            "Only tracks without existing hotcues are processed."
            if in_traktor()  # inside Traktor the cues go into its memory, never the NML
            else "Detect drops & breakdowns and write them as hotcues to collection.nml.\n"
            "Only tracks without existing hotcues are processed.\n"
            "Close Traktor before running — the NML file will be modified."
        )
        hc_desc.setStyleSheet("font-size: 11px; color: #7777aa;")
        hc_desc.setWordWrap(True)
        hc_lay.addWidget(hc_desc)

        hc_ctrl = QHBoxLayout()
        self.hc_btn = QPushButton("Detect Hotcues…")
        self.hc_btn.clicked.connect(self._start_hotcue_detection)
        hc_ctrl.addWidget(self.hc_btn)
        self.hc_stop_btn = QPushButton("Stop")
        self.hc_stop_btn.setEnabled(False)
        self.hc_stop_btn.clicked.connect(self._stop_hotcue_detection)
        hc_ctrl.addWidget(self.hc_stop_btn)
        self.hc_force_cb = QCheckBox("Reprocess existing hotcues")
        hc_ctrl.addWidget(self.hc_force_cb)
        hc_ctrl.addStretch()
        self.hc_cleanup_btn = QPushButton("Cleanup Auto Hotcues")
        self.hc_cleanup_btn.setToolTip(
            "Remove every hotcue we previously generated (name starts with 'tk: ').\n"
            "User-created hotcues are left untouched."
        )
        self.hc_cleanup_btn.clicked.connect(self._cleanup_tk_hotcues)
        hc_ctrl.addWidget(self.hc_cleanup_btn)
        hc_lay.addLayout(hc_ctrl)

        self.hc_progress = QProgressBar()
        self.hc_progress.setValue(0)
        hc_lay.addWidget(self.hc_progress)
        self.hc_status = QLabel("")
        self.hc_status.setStyleSheet("font-size: 11px; color: #7777aa;")
        self.hc_status.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        hc_lay.addWidget(self.hc_status)
        self._add_pinned("Hotcues", hc_box)

    def _hotcue_source(self, include_existing: bool):
        """``(nml, candidates, skipped)`` for a detection run, or None after telling the user
        why not. *candidates* as :func:`core.traktor.hotcue_eligibility` gives them.

        Inside Traktor from Traktor's memory (its BPM and beat grids), never collection.nml
        (User, 2026-11-24); *nml* is "" there, as nothing is written to it."""
        from core.runtime import in_traktor
        from ui.dialogs import warn

        if in_traktor():
            from core.collection_source import collection_cues
            from core.cue_points import eligibility

            tracks = collection_cues(None)
            if not tracks:
                warn(self, "No collection", "Traktor's collection could not be read.")
                return None
            return ("", *eligibility(tracks, include_existing=include_existing))
        nml = self._nml()
        if not nml:
            warn(
                self,
                "No collection",
                "No collection.nml for the Traktor selected in Settings → Traktor.",
            )
            return None
        nml_path = Path(nml)
        if not nml_path.exists():
            warn(self, "File not found", f"NML file not found:\n{nml}")
            return None
        # Single NML parse: get (path, bpm, beat_phase_ms) for eligible tracks,
        # and why the rest are left out — said in the log, so a run that
        # touches few tracks explains itself.
        return (nml, *hotcue_eligibility(nml_path, include_existing=include_existing))

    def _start_hotcue_detection(self) -> None:
        from ui.dialogs import info

        force = self.hc_force_cb.isChecked()
        source = self._hotcue_source(force)
        if source is None:
            return
        nml, candidates, skipped = source
        on_disk = [(p, bpm, phase) for p, bpm, phase in candidates if Path(p).exists()]
        if len(on_disk) < len(candidates):
            skipped["file missing"] = len(candidates) - len(on_disk)
        candidates = on_disk
        self._hc_skipped = skipped
        log.info(
            "hotcues: %d candidate(s); skipped %s",
            len(candidates),
            ", ".join(f"{v} {k}" for k, v in skipped.items()) or "none",
        )
        candidates.sort(key=lambda c: Path(c[0]).stat().st_mtime, reverse=True)

        if not candidates:
            info(
                self,
                "Nothing to do",
                "No tracks found that need hotcues.\n\n"
                "Tracks must have been analysed in Traktor\n"
                "(so their beat grid exists) and have no hotcues yet.",
            )
            return

        from core.runtime import in_traktor
        from ui.dialogs import confirm

        where = (
            "The hotcues go straight into Traktor's collection."
            if in_traktor()
            else "The collection.nml file will be modified in place\n"
            "(a .bak backup is created automatically)."
        )
        proceed = confirm(
            self,
            "Detect Hotcues",
            f"{len(candidates)} track(s) without hotcues will be processed.\n\n{where}\n\nContinue?",
            negative=QMessageBox.StandardButton.Cancel,
        )
        if not proceed:
            return
        if not in_traktor() and not self._confirm_traktor_closed("Writing hotcues"):
            return
        self._start_hc_worker(nml, candidates, force, f"Processing {len(candidates)} tracks…")

    def _start_hc_worker(
        self, nml: str, candidates: list[tuple[str, float, float]], force: bool, status: str
    ) -> None:
        """Run the detection. Inside Traktor its cues are written live into Traktor's collection
        (:meth:`_on_hc_detected`), never into collection.nml, which Traktor overwrites when it
        quits (User, 2026-11-24)."""
        from core.runtime import in_traktor

        self.hc_btn.setEnabled(False)
        self.hc_stop_btn.setEnabled(True)
        self.hc_progress.setMaximum(len(candidates))
        self.hc_progress.setValue(0)
        self.hc_status.setText(status)
        live = in_traktor()
        self._hc_worker = HotcueWorker(nml, candidates, force=force, write_nml=not live)
        self._hc_worker.progress.connect(self._on_hc_progress)
        self._hc_worker.finished.connect(self._on_hc_finished)
        self._hc_worker.error.connect(self._on_hc_error)
        if live:
            self._hc_worker.detected.connect(self._on_hc_detected)
        self._hc_worker.start()

    def _on_hc_detected(self, detected: dict) -> None:
        """Inside Traktor: write the detected cues live (Traktor's main thread is this one)."""
        from core.collection_source import collection_cues
        from workers.native_cues import NativeCues, apply_detected

        cues = NativeCues.attach()
        if cues is None:
            self._on_hc_error("Traktor's cue writer is not there (libtk): nothing written.")
            self._on_hc_finished(0, "")
            return
        worker = self._hc_worker
        force = bool(getattr(worker, "force", False))
        self.hc_status.setText("Writing hotcues into Traktor…")
        changed = apply_detected(cues, detected, collection_cues(None), force)
        count = len(changed)
        if worker is not None:
            worker.outcome["written"] = count
        log.info("hotcues written live into Traktor for %d of %d track(s)", count, len(detected))
        self._on_hc_finished(count, "")
        if changed:
            # Which tracks, with which cues: what the user loads to check them (User, 2026-11-24).
            from ui.dialogs import info
            from workers.native_cues import describe

            info(self, f"Hotcues written for {count} track(s)", describe(changed))

    def recompute_hotcues_for(self, path: str) -> bool:
        """Redo one track's hotcues, replacing what it has — from the result
        row's menu. Same worker as the Hotcues tab, one candidate, forced.
        Returns True when a detection was started."""
        from ui.dialogs import warn

        if self._hc_worker is not None and self._hc_worker.isRunning():
            warn(
                self,
                "Hotcues busy",
                "A hotcue detection is already running — wait for it to "
                "finish, or stop it in Library → Hotcues.",
            )
            return False
        source = self._hotcue_source(True)
        if source is None:
            return False
        nml, eligible, _skipped = source
        candidates = [c for c in eligible if c[0] == path]
        if not candidates:
            warn(
                self,
                "No beat grid",
                f"{Path(path).name} has no beat grid in the collection.\n\n"
                "Load it in Traktor once so the grid exists, then retry.",
            )
            return False
        from core.runtime import in_traktor

        if not in_traktor() and not self._confirm_traktor_closed("Writing hotcues"):
            return False
        self._start_hc_worker(nml, candidates, True, f"Recomputing hotcues for {Path(path).name}…")
        return True

    def _cleanup_tk_hotcues(self) -> None:
        from core.runtime import in_traktor

        if in_traktor():
            self._cleanup_tk_hotcues_live()
            return
        from ui.dialogs import confirm, info, warn

        nml = self._nml()
        if not nml:
            warn(
                self,
                "No collection",
                "No collection.nml for the Traktor selected in Settings → Traktor.",
            )
            return
        nml_path = Path(nml)
        if not nml_path.exists():
            warn(self, "File not found", f"NML file not found:\n{nml}")
            return

        proceed = confirm(
            self,
            "Cleanup Auto Hotcues",
            "Remove every hotcue auto-generated by Track Kommander (name starts with 'tk: ')?\n\n"
            "User-created hotcues are NOT affected.\n"
            "The collection.nml file will be modified in place\n"
            "(a .bak backup is created automatically).\n\n"
            "Continue?",
            negative=QMessageBox.StandardButton.Cancel,
        )
        if not proceed:
            return
        if not self._confirm_traktor_closed("Deleting hotcues"):
            return

        self.hc_cleanup_btn.setEnabled(False)
        self.hc_status.setText("Cleaning up auto hotcues…")
        try:
            tracks, cues = cleanup_tk_hotcues(nml_path)
        except Exception as exc:
            self.hc_status.setText(f"⚠  {exc}")
            warn(self, "Cleanup failed", str(exc))
            self.hc_cleanup_btn.setEnabled(True)
            return
        finally:
            self.hc_cleanup_btn.setEnabled(True)
            self._collection_written()

        if cues == 0:
            self.hc_status.setText("Done — no auto hotcues found.")
            info(self, "Cleanup", "No auto-generated hotcues were found.")
        else:
            self.hc_status.setText(
                f"Done — removed {cues} auto hotcue(s) from {tracks} track(s). "
                "Reload your collection in Traktor."
            )
            info(
                self,
                "Cleanup",
                f"Removed {cues} auto-generated hotcue(s) from {tracks} track(s).\n\n"
                "Reload your collection in Traktor to see the changes.",
            )

    def _cleanup_tk_hotcues_live(self) -> None:
        """Inside Traktor: remove TK's hotcues from Traktor's collection itself."""
        from core.collection_source import collection_cues
        from ui.dialogs import confirm, info, warn
        from workers.native_cues import NativeCues, remove_tk_cues

        cues = NativeCues.attach()
        if cues is None:
            warn(self, "Cleanup", "Traktor's cue writer is not there (libtk): nothing removed.")
            return
        if not confirm(
            self,
            "Cleanup Auto Hotcues",
            "Remove every hotcue auto-generated by Track Kommander (name starts with 'tk: ') "
            "from Traktor's collection?\n\nUser-created hotcues are NOT affected.\n\nContinue?",
            negative=QMessageBox.StandardButton.Cancel,
        ):
            return
        tracks, removed = remove_tk_cues(cues, collection_cues(None))
        text = (
            f"Removed {removed} auto-generated hotcue(s) from {tracks} track(s)."
            if removed
            else "No auto-generated hotcues were found."
        )
        self.hc_status.setText(f"Done — {text}")
        info(self, "Cleanup", text)

    def _stop_hotcue_detection(self) -> None:
        if self._hc_worker:
            self._hc_worker.stop()
        self.hc_stop_btn.setEnabled(False)
        self.hc_status.setText("Stopping…")

    def _on_hc_progress(self, current: int, total: int, filename: str) -> None:
        self.hc_progress.setValue(current)
        name = filename if len(filename) <= 60 else "…" + filename[-57:]
        self.hc_status.setText(f"[{current}/{total}]  {name}")

    def _on_hc_finished(self, count: int, nml_path: str) -> None:
        self._collection_written()
        stopped = not self.hc_stop_btn.isEnabled()  # stop was pressed
        self.hc_btn.setEnabled(True)
        self.hc_stop_btn.setEnabled(False)
        self.hc_progress.setValue(self.hc_progress.maximum())
        worker = getattr(self, "_hc_worker", None)
        outcome = getattr(worker, "outcome", None) or {}
        skipped = getattr(self, "_hc_skipped", None) or {}
        detail = " · ".join(
            [f"{v} {k}" for k, v in outcome.items() if v and k not in ("cued", "written")]
            + [f"skipped {v}: {k}" for k, v in skipped.items() if v]
        )
        tail = f"\n{detail}" if detail else ""
        if count:
            from core.runtime import in_traktor

            prefix = "Stopped — " if stopped else "Done — "
            then = (
                "A track already on a deck shows them when it is loaded again."
                if in_traktor()
                else "Reload your collection in Traktor."
            )
            self.hc_status.setText(f"{prefix}hotcues written for {count} track(s). {then}{tail}")
        else:
            self.hc_status.setText(
                (
                    "Stopped — no hotcues were written."
                    if stopped
                    else "Done — no hotcues were written."
                )
                + tail
            )

    def _on_hc_error(self, msg: str) -> None:
        self.hc_status.setText(f"⚠  {msg}")
