"""Library tab, beat-grid fixes: the queue captured while mixing.

Lists the phase offsets captured from the phase meter and writes them into
``collection.nml`` on request. Mixed into :class:`ui.library.LibraryTab`.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QVBoxLayout,
)

from core.grid_fix import GridQueue, apply_grid_fixes, grid_queue_path


class _GridFixMixin:
    def _build_grid_fix_box(self) -> None:
        grid_box = QGroupBox("BEAT GRID FIXES")
        grid_lay = QVBoxLayout(grid_box)
        grid_desc = QLabel(
            "Offsets captured from the phase meter during a mix: nudge a track "
            "into place by ear with the tempos locked, then right-click that "
            'deck and choose "Fix phase".\n'
            "Close Traktor before applying — it rewrites collection.nml from "
            "memory when it quits."
        )
        grid_desc.setStyleSheet("font-size: 11px; color: #7777aa;")
        grid_desc.setWordWrap(True)
        grid_lay.addWidget(grid_desc)

        self.grid_list = QListWidget()
        self.grid_list.setMinimumHeight(90)
        self.grid_list.currentRowChanged.connect(self._on_grid_selection)
        self.grid_list.itemDoubleClicked.connect(lambda _item: self._apply_selected_grid_fix())
        grid_lay.addWidget(self.grid_list, 1)

        grid_ctrl = QHBoxLayout()
        self.grid_apply_one_btn = QPushButton("Apply this one…")
        self.grid_apply_one_btn.setToolTip(
            "Write just the selected fix. Double-clicking a row does the same."
        )
        self.grid_apply_one_btn.clicked.connect(self._apply_selected_grid_fix)
        grid_ctrl.addWidget(self.grid_apply_one_btn)
        self.grid_apply_btn = QPushButton("Apply all…")
        self.grid_apply_btn.clicked.connect(self._apply_grid_fixes)
        grid_ctrl.addWidget(self.grid_apply_btn)
        self.grid_drop_btn = QPushButton("Drop")
        self.grid_drop_btn.setToolTip("Forget the selected capture.")
        self.grid_drop_btn.clicked.connect(self._drop_grid_fix)
        grid_ctrl.addWidget(self.grid_drop_btn)
        grid_ctrl.addStretch()
        grid_lay.addLayout(grid_ctrl)

        self.grid_status = QLabel("")
        self.grid_status.setStyleSheet("font-size: 11px; color: #7777aa;")
        self.grid_status.setWordWrap(True)
        grid_lay.addWidget(self.grid_status)
        self._tab("Hotcues").addWidget(grid_box)
        self._grid_fix_box = grid_box
        self.refresh_grid_fixes()

    # ── Beat-grid fixes ─────────────────────────────────────────────────────

    def grid_fix_captured(self) -> None:
        """A fix was just captured — show it if this window is already open."""
        self.refresh_grid_fixes()

    def refresh_grid_fixes(self) -> None:
        """Re-read the queue captured during mixing and show it."""
        fixes = GridQueue(grid_queue_path()).entries()
        self.grid_list.clear()
        for f in fixes:
            name = f"{f.artist} — {f.title}" if f.artist or f.title else Path(f.path).name
            self.grid_list.addItem(
                f"{f.offset_ms:+.0f} ms   ({f.phase:+.3f} beat @ {f.bpm:.1f})   "
                f"{name}   ·   deck {f.deck}, {f.captured_at}"
            )
        self.grid_apply_btn.setEnabled(bool(fixes))
        self._on_grid_selection(self.grid_list.currentRow())
        if not fixes:
            self.grid_status.setText("Nothing captured.")

    def _on_grid_selection(self, row: int) -> None:
        picked = 0 <= row < self.grid_list.count()
        self.grid_apply_one_btn.setEnabled(picked)
        self.grid_drop_btn.setEnabled(picked)

    def _drop_grid_fix(self) -> None:
        row = self.grid_list.currentRow()
        fixes = GridQueue(grid_queue_path()).entries()
        if 0 <= row < len(fixes):
            GridQueue(grid_queue_path()).remove(fixes[row].path)
            self.refresh_grid_fixes()

    def _apply_selected_grid_fix(self) -> None:
        """Write just the highlighted capture — one track at a time, so a
        doubtful one can be tried on its own before the rest."""
        row = self.grid_list.currentRow()
        fixes = GridQueue(grid_queue_path()).entries()
        if 0 <= row < len(fixes):
            self._write_grid_fixes([fixes[row]])

    def _apply_grid_fixes(self) -> None:
        self._write_grid_fixes(GridQueue(grid_queue_path()).entries())

    def _write_grid_fixes(self, fixes: list) -> None:
        from ui.dialogs import confirm, info, warn

        queue = GridQueue(grid_queue_path())
        if not fixes:
            return
        nml = self._nml()
        if not nml or not Path(nml).is_file():
            warn(
                self,
                "No collection",
                "No collection.nml for the Traktor selected in Settings → Traktor.",
            )
            return
        what = (
            f"the beat grid of {Path(fixes[0].path).name}"
            if len(fixes) == 1
            else f"{len(fixes)} beat grids"
        )
        if not confirm(
            self,
            "Apply beat-grid fixes",
            f"Move {what} in\n{nml}\n\nA .bak backup is made first.\n\nContinue?",
            yes_text="Apply",
        ):
            return
        if not self._confirm_traktor_closed("Writing beat grids"):
            return
        try:
            written, skipped = apply_grid_fixes(Path(nml), fixes)
        except RuntimeError as exc:
            warn(self, "Cannot write the collection", str(exc))
            return
        finally:
            self._collection_written()
        # Only what was written leaves the queue: a track the collection does
        # not have yet stays for the next try.
        failed = {s.split(":", 1)[0] for s in skipped}
        for f in fixes:
            if Path(f.path).name not in failed:
                queue.remove(f.path)
        self.refresh_grid_fixes()
        self.grid_status.setText(
            f"Moved {written} beat grid(s)." + (f"  {len(skipped)} skipped." if skipped else "")
        )
        details = f"{written} beat grid(s) moved.\n\nReload the collection in Traktor."
        if skipped:
            details += "\n\nSkipped:\n" + "\n".join(skipped[:10])
        info(self, "Beat grids updated", details)
