"""Library tab, export: the group of controls and the write-to-USB flow.

Builds the EXPORT TO USB group, runs the device export on its worker thread
and reports the outcome. Mixed into :class:`ui.library.LibraryTab`; the
worker is kept as ``self._export_worker`` because the main window reads it by
that name when the app quits.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QFileSystemWatcher, Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
)

from core.eta import split_eta
from ui.export_summary import summarise
from ui.library_helpers import _total_size
from ui.widgets import CheckList
from ui.worker_shutdown import keep_until_finished
from workers.device_export import DeviceExportWorker


class _ExportMixin:
    # ── USB export ──────────────────────────────────────────────────────────

    def _build_export_group(self) -> QGroupBox:
        grp = QGroupBox("EXPORT TO USB")
        lay = QVBoxLayout(grp)
        lay.setSpacing(8)

        desc = QLabel(
            "One export, three ways to use the stick — all over the same "
            "copied audio, with hotcues, beat grids, BPM, key and ratings:\n"
            "• CDJ / Rekordbox — the PIONEER database (export.pdb and "
            "OneLibrary). Players read it on their own; Rekordbox shows it "
            "under Devices.\n"
            "• Traktor — Traktor/<playlist>.nml, for Import Playlist.\n"
            "• Everything else — PLAYLISTS/<playlist>.m3u8."
        )
        desc.setStyleSheet("font-size: 11px; color: #7777aa;")
        desc.setWordWrap(True)
        lay.addWidget(desc)

        # ── What to export ──────────────────────────────────────────────────
        src_row = QHBoxLayout()
        src_row.setSpacing(8)
        src_lbl = QLabel("Playlists:")
        src_lbl.setStyleSheet("color: #7777aa;")
        src_row.addWidget(src_lbl)
        src_row.addStretch()
        self.rb_all_btn = QPushButton("All")
        self.rb_all_btn.setMinimumWidth(52)
        self.rb_all_btn.clicked.connect(lambda: self._check_all_playlists(True))
        src_row.addWidget(self.rb_all_btn)
        self.rb_none_btn = QPushButton("None")
        self.rb_none_btn.setMinimumWidth(62)
        self.rb_none_btn.clicked.connect(lambda: self._check_all_playlists(False))
        src_row.addWidget(self.rb_none_btn)
        self.rb_reload_btn = QPushButton("Reload")
        self.rb_reload_btn.setMinimumWidth(72)
        self.rb_reload_btn.setToolTip("Re-read the playlists from collection.nml.")
        self.rb_reload_btn.clicked.connect(self.reload_playlists)
        src_row.addWidget(self.rb_reload_btn)
        lay.addLayout(src_row)

        # Tick as many as you like — each becomes a playlist on the export, and
        # a track appearing in several is copied once.
        self.rb_playlists = CheckList()
        self.rb_playlists.setMinimumHeight(110)
        self.rb_playlists.setAlternatingRowColors(False)
        self.rb_playlists.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.rb_playlists.itemChanged.connect(self._on_rb_playlist_toggled)
        # The only stretching row in the group: everything else keeps its height
        # and the list grows with the window.
        lay.addWidget(self.rb_playlists, 1)

        self.rb_source_note = QLabel("")
        self.rb_source_note.setStyleSheet("font-size: 11px; color: #7777aa;")
        self.rb_source_note.setWordWrap(True)
        lay.addWidget(self.rb_source_note)

        # Traktor and Rekordbox don't always agree on where t=0 sits in an MP3
        # (encoder delay), so grids can land a few ms apart. Offer the nudge
        # rather than pretending the conversion is exact.
        off_row = QHBoxLayout()
        off_row.setSpacing(8)
        off_lbl = QLabel("Timing offset:")
        off_lbl.setStyleSheet("color: #7777aa;")
        off_row.addWidget(off_lbl)
        self.rb_offset = QSpinBox()
        self.rb_offset.setRange(-200, 200)
        self.rb_offset.setSuffix(" ms")
        self.rb_offset.setValue(0)
        self.rb_offset.setToolTip(
            "Shift every cue and beat grid on the CDJ / Rekordbox side by\n"
            "this much. The Traktor playlists keep your cues as they are.\n"
            "MP3 encoder delay can put Traktor and Rekordbox a few ms apart;\n"
            "export, check one track in Rekordbox, then adjust if needed."
        )
        off_row.addWidget(self.rb_offset)
        off_row.addStretch()
        lay.addLayout(off_row)

        # Where it goes, remembered — so the usual export is one click and a
        # confirmation, not a folder dialog every time. Sticks show up as
        # they are plugged in; the button is for anything else.
        dest_row = QHBoxLayout()
        dest_row.setSpacing(8)
        dest_lbl = QLabel("Destination:")
        dest_lbl.setStyleSheet("color: #7777aa;")
        dest_row.addWidget(dest_lbl)
        self.rb_dest = QComboBox()
        self.rb_dest.setMinimumWidth(220)
        self.rb_dest.setToolTip("The drive or folder the export is written to.")
        self.rb_dest.currentIndexChanged.connect(self._on_rb_dest_changed)
        dest_row.addWidget(self.rb_dest, 1)
        self.rb_dest_btn = QPushButton("Choose…")
        self.rb_dest_btn.setToolTip("Pick a folder that is not a mounted drive.")
        self.rb_dest_btn.clicked.connect(self._choose_export_destination)
        dest_row.addWidget(self.rb_dest_btn)
        lay.addLayout(dest_row)
        # A mount or unmount changes /Volumes, so that is watched rather than
        # polled: listing the volumes stats every one of them, and a stat on
        # a stick that is being written to can block the main thread for
        # seconds. Where there is no /Volumes (Windows) a slow poll remains.
        self._mount_watcher = QFileSystemWatcher(self)
        self._mount_watcher.directoryChanged.connect(lambda _p: self.refresh_export_destinations())
        self._dest_timer = QTimer(self)
        self._dest_timer.setInterval(5000)
        self._dest_timer.timeout.connect(self.refresh_export_destinations)
        if not self._mount_watcher.addPath("/Volumes"):
            self._dest_timer.start()
        self.refresh_export_destinations()

        ctrl = QHBoxLayout()
        self.rb_cdj_btn = QPushButton("Export to USB…")
        self.rb_cdj_btn.setObjectName("primary")
        self.rb_cdj_btn.setToolTip(
            "Copy the audio and write everything: the PIONEER database and\n"
            "per-track analysis, which players and Rekordbox read directly,\n"
            "plus .m3u8 and Traktor playlists over the same files."
        )
        self.rb_cdj_btn.clicked.connect(self._export_cdj)
        ctrl.addWidget(self.rb_cdj_btn)

        self.rb_stop_btn = QPushButton("Stop")
        self.rb_stop_btn.setEnabled(False)
        self.rb_stop_btn.setToolTip(
            "Stop after the track being written. What is already on the key\n"
            "stays there and stays playable; running the export again adds\n"
            "the rest."
        )
        self.rb_stop_btn.clicked.connect(self._stop_export)
        ctrl.addWidget(self.rb_stop_btn)

        # One decision, not three switches. "Replace without deleting the
        # audio" was reachable before and is a state nobody wants: the old
        # tracks stay on the drive where no player can see them, filling it.
        mode_lbl = QLabel("On the key:")
        mode_lbl.setStyleSheet("color: #7777aa;")
        ctrl.addWidget(mode_lbl)

        self.rb_mode = QComboBox()
        self.rb_mode.addItem("Add to what's there", "add")
        self.rb_mode.addItem("Replace what's there", "replace")
        self.rb_mode.setToolTip(
            "<b>Add</b> keeps the tracks and playlists the key already has "
            "and adds this selection to them. A track exported again is "
            "refreshed — new hotcues, new grid — and a playlist of the same "
            "name is replaced, so dropping a track from it in Traktor drops "
            "it here too.<br><br>"
            "<b>Replace</b> makes the key hold this selection and nothing "
            "else. Audio already on it that is not in the selection is "
            "<b>deleted</b> — you are told how much before it happens."
        )
        ctrl.addWidget(self.rb_mode)

        self.rb_reanalyse = QCheckBox("Re-analyse the audio")
        self.rb_reanalyse.setToolTip(
            "Not normally needed. Analysing a track is the slow part of an "
            "export —\nabout a second each — so the result is kept and reused "
            "the next time,\nwhichever stick that is. Your audio files are "
            "not touched.\n\n"
            "What is kept is dropped by itself when a file is edited or "
            "replaced,\nand when a new version of this app improves the "
            "analysis.\n\n"
            "Tick this to decode every track again regardless — worth doing "
            "if a\nwaveform looks wrong on a player."
        )
        ctrl.addWidget(self.rb_reanalyse)

        ctrl.addStretch()
        lay.addLayout(ctrl)

        self.rb_progress = QProgressBar()
        self.rb_progress.setValue(0)
        self.rb_progress.setVisible(False)
        lay.addWidget(self.rb_progress)

        # The track name changes length on every step; the time left sits
        # on the right so it is not dragged around by it.
        status_row = QHBoxLayout()
        status_row.setSpacing(8)
        self.rb_status = QLabel("")
        self.rb_status.setStyleSheet("font-size: 11px; color: #7777aa;")
        self.rb_status.setWordWrap(True)
        status_row.addWidget(self.rb_status, 1)
        self.rb_eta = QLabel("")
        self.rb_eta.setStyleSheet("font-size: 11px; color: #7777aa;")
        self.rb_eta.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
        status_row.addWidget(self.rb_eta)
        lay.addLayout(status_row)

        self._playlists: list = []
        return grp

    # ── Export to USB ───────────────────────────────────────────────────────

    def _export_cdj(self) -> None:
        """Write one stick: audio, the PIONEER database, analysis, and XML."""
        from core.device_export import (
            audio_not_in,
            device_track_from,
            find_seed,
            sources_of,
        )
        from core.runtime import in_traktor, user_data_dir
        from core.usb_export import free_space
        from ui.dialogs import confirm, warn

        # Resolved once for the whole action: finding it walks the Traktor
        # installs, and the export used to do that three times. Inside Traktor
        # there is none to find: the export reads Traktor's memory.
        inside = in_traktor()
        nml = "" if inside else self._nml()
        if not nml and not inside:
            warn(
                self,
                "No collection",
                "No collection.nml for the Traktor selected in Settings → Traktor.",
            )
            return
        if not self._warn_traktor_open_for_export():
            return

        seed = find_seed(user_data_dir())
        if seed is None:
            warn(
                self,
                "Cannot prepare a database template",
                "Writing a PIONEER database needs a blank export.pdb to "
                "build on. One is normally generated automatically, so this "
                "means it could not be written — most likely the app's data "
                "folder is not writable.\n\nPlugging in a USB stick that "
                "Rekordbox has exported to at least once also works: the "
                "blank is taken from it and kept.",
            )
            return

        dest_root = self._pick_export_destination()
        if dest_root is None:
            return

        self.rb_status.setText("Reading collection…")
        self.rb_status.repaint()
        try:
            src_tracks, playlists, skipped = self._collect_rb_tracks(nml)
        except Exception as exc:
            self.rb_status.setText("")
            warn(self, "Cannot read NML", str(exc))
            return
        if not src_tracks:
            self.rb_status.setText("")
            warn(self, "Nothing to export", "That selection has no usable tracks.")
            return

        offset = float(self.rb_offset.value())
        tracks = [device_track_from(t, offset_ms=offset) for t in src_tracks]

        need = _total_size(t.source for t in tracks)
        free = free_space(dest_root)
        if free and need > free:
            warn(
                self,
                "Not enough space",
                f"This needs {need / 1e9:.2f} GB but the drive has {free / 1e9:.2f} GB free.",
            )
            return

        merge = self.rb_mode.currentData() != "replace"
        # A track that changed is always rewritten now: the export detects
        # what changed by fingerprint, so there is nothing left for a manual
        # "leave it alone" to be right about.
        refresh = True
        reanalyse = self.rb_reanalyse.isChecked()
        if merge:
            what = (
                "Tracks and playlists already on the key are kept. Only "
                "what actually changed is rewritten; a playlist of the "
                "same name is replaced."
            )
        else:
            # Replace deletes audio, so the amount is counted before anything
            # is written: agreeing to a number is not the same as finding out
            # afterwards.
            doomed, doomed_bytes = audio_not_in(dest_root, sources_of(tracks))
            what = "The key will hold this selection and nothing else."
            if doomed:
                what += (
                    f"\n{doomed:,} track(s) "
                    f"({doomed_bytes / 1e9:.2f} GB) already on it are "
                    "not in this selection and will be DELETED."
                )
            else:
                what += " Nothing already on it has to be deleted."
        caching = (
            "Every track is decoded again, whatever was analysed before.\n\n"
            if reanalyse
            else "Tracks analysed for an earlier export are not decoded again.\n\n"
        )
        if not confirm(
            self,
            "Export to USB",
            f"Write {len(tracks):,} tracks ({need / 1e9:.2f} GB) and a PIONEER "
            f"database to\n{dest_root}\n\n"
            f"{len(playlists)} playlist(s).\n\n{what}\n\n{caching}"
            "Tested on an XDJ-1000. Newer OneLibrary players (CDJ-3000X, "
            "XDJ-AZ, …) are untested, so try a spare stick on those.\n\n"
            "Continue?",
            yes_text="Write stick",
        ):
            return

        # Analysing and copying are two passes over the same tracks, so the
        # bar counts both as one run — analysis fills the first half, copying
        # the second — rather than sweeping 0-100% twice.
        self.rb_progress.setVisible(True)
        self.rb_progress.setMaximum(3 * max(1, len(tracks)))
        self.rb_progress.setValue(0)
        self.rb_cdj_btn.setEnabled(False)
        self.rb_stop_btn.setEnabled(True)

        # Everything the export needs is resolved by now, so the thread only
        # reads it. Keeping a reference is what stops Qt collecting the
        # thread while it runs.
        self._export_ctx = {
            "dest": dest_root,
            "playlists": playlists,
            "skipped": skipped,
            "tracks": len(tracks),
        }
        self._export_worker = DeviceExportWorker(
            tracks,
            dest_root,
            seed,
            playlists=playlists,
            merge=merge,
            refresh_existing=refresh,
            reanalyse=reanalyse,
            sweep_orphans=not merge,
            nml_path=Path(nml) if nml else None,
        )
        self._export_worker.progress.connect(self._on_export_progress)
        self._export_worker.done.connect(self._on_export_done)
        self._export_worker.error.connect(self._on_export_error)
        self._export_worker.start()

    def _stop_export(self) -> None:
        worker = getattr(self, "_export_worker", None)
        if worker is not None:
            worker.stop()
        self.rb_stop_btn.setEnabled(False)
        self.rb_status.setText("Stopping after this track…")

    def _on_export_progress(self, done: int, total: int, what: str) -> None:
        if self.rb_progress.maximum() != total:
            self.rb_progress.setMaximum(total)
        self.rb_progress.setValue(done)
        text, eta = split_eta(what)
        self.rb_status.setText(text)
        self.rb_eta.setText(eta)

    def _on_export_error(self, message: str) -> None:
        from ui.dialogs import warn

        self._finish_export()
        self.rb_status.setText("")
        warn(self, "Export failed", message)

    def _finish_export(self) -> None:
        self.rb_progress.setVisible(False)
        self.rb_eta.setText("")
        self.rb_cdj_btn.setEnabled(True)
        self.rb_stop_btn.setEnabled(False)
        # This runs in the slot of the worker's own "done" signal, which it
        # emits a moment before its thread ends. Dropped here and now, the
        # worker could be destroyed while still running, and Qt ends the
        # process for that.
        keep_until_finished(self._export_worker)
        self._export_worker = None
        self.refresh_export_destinations()  # the stick may have gone

    def _on_export_done(self, outcome) -> None:
        """Report what the worker did. Mirrors the old inline summary."""
        from ui.dialogs import info, warn

        ctx = getattr(self, "_export_ctx", {})
        dest_root = ctx.get("dest")
        playlists = ctx.get("playlists", {})
        skipped = ctx.get("skipped", 0)
        report = outcome.report
        self._finish_export()

        if outcome.stopped and report is None:
            self.rb_status.setText("Export stopped.")
            info(
                self,
                "Export stopped",
                "Nothing was written to the key.\n\nTracks analysed before "
                "you stopped were kept, so starting again will not decode "
                "them a second time.",
            )
            return

        if report is None or report.db_path is None:
            self.rb_status.setText("")
            errors = report.errors[:10] if report else []
            warn(self, "Export failed", "\n".join(errors) or "unknown")
            return

        status, details = summarise(
            outcome, playlists=len(playlists), dest_name=dest_root.name, skipped=skipped
        )
        self.rb_status.setText(status)
        info(self, "Export complete", details)
