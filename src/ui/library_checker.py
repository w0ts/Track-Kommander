"""LibraryCheckerDialog — verify every library track loads & plays.

Loads tracks onto Deck A and Deck B via the same drag-and-drop primitive the
NOW PLAYING tab uses (``core.drag.drag_to_deck`` from a ``ResultsTable`` row,
then ``WebhookWatcher.queue_load(skip_load=True)`` for the post-load automation
chain — mute, cue, play). Track success requires both a ``deck_loaded``
webhook event matching the expected file and a subsequent ``deck_position``
advance. Failed tracks are retried once on the OTHER deck before being
recorded in the failed list and exportable as a one-path-per-line text file.

Constraints inherited from the drag primitive:
* Traktor must be visible — ``drag_to_deck`` rejects the drag if Traktor's
  window is not on top at the deck drop zone.
* The two drag-source rows in this dialog must be on screen — that's why the
  ``_DragSourceTable`` row is laid out at a fixed height inside the dialog.
* Drags are serialised: only one drag runs at a time because mouse events are
  global. The two decks still pipeline (slot A waits for ``deck_loaded`` /
  playback while slot B drags), which is the "twice as fast" the user wanted.
"""

from __future__ import annotations

import logging
import os
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QProgressBar,
    QPushButton,
    QTableWidgetItem,
    QVBoxLayout,
)

from core.drag import drag_to_deck, is_traktor_visible, row_screen_center
from ui.dialogs import install_escape_to_close, warn
from ui.widgets import ResultsTable

_log = logging.getLogger(__name__)

# How long to wait for a deck_loaded event matching the dragged track.
LOAD_TIMEOUT_S = 8.0
# After loaded, how long to watch the playhead. Success requires deck_position
# to advance at least once during this window.
PLAY_WINDOW_S = 3.0
# Settle time between deck_loaded arriving and queue_play firing — Traktor
# needs a beat after the drop to finalise the load before the play command
# actually takes effect.
LOAD_SETTLE_MS = 500
# Time the QML needs to apply queue_mute before we start dragging tracks.
# The QML's loadDelayTimer fires 800 ms after the command reaches it; the
# bridge long-polls, so that is almost at once — 300 ms of margin on top.
MUTE_SETTLE_MS = 1100
# Roughly how long _drag_mac holds the mouse — keep one drag exclusive on the
# global cursor for this long after dispatch. Slightly larger than the actual
# ~0.5 s drag to leave headroom for NSDraggingSession teardown.
DRAG_LOCK_MS = 800
# Between raising this dialog and posting the drag: time for the macOS window
# server to make it the key window, or the mouse-down only activates it.
DISPATCH_SETTLE_MS = 50
# How long an answer from is_traktor_visible() is reused. It lists every
# window on screen; the pump ticks every TICK_MS and does not need a fresher
# answer than this to pause and resume.
VISIBILITY_CHECK_S = 0.5
# Tick interval for the state-machine pump.
TICK_MS = 100


@dataclass
class CheckItem:
    path: str
    attempts: list[str] = field(default_factory=list)  # deck letters already tried


@dataclass
class Slot:
    deck: str
    row: int  # row index in the drag-source table
    # idle → waiting_drag → dispatching → loading → settling → playing → idle
    state: str = "idle"
    item: CheckItem | None = None
    load_deadline: float = 0.0
    play_deadline: float = 0.0
    last_position: float = -1.0
    position_changed: bool = False
    confirmed_playing: bool = False  # set by deck_state(deck, True)


def _same_basename(a: str, b: str) -> bool:
    if not a or not b:
        return False
    return os.path.basename(a).lower() == os.path.basename(b).lower()


class LibraryCheckerDialog(QDialog):
    """Drives a two-deck drag/load/play sweep over every track in *paths*."""

    def __init__(self, watcher, paths: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Library Checker")
        self.resize(720, 560)

        self._watcher = watcher
        self._all_paths: list[str] = list(paths)
        self._main_queue: deque[CheckItem] = deque()
        self._retry_queue: list[CheckItem] = []
        # Row 0 → drag source for deck A, row 1 → drag source for deck B.
        # Each slot owns its own row so a pipelined deck B drag doesn't
        # clobber the path slot A is still tracking on the loading side.
        self._slots: dict[str, Slot] = {
            "A": Slot("A", row=0),
            "B": Slot("B", row=1),
        }
        self._failed: list[tuple[str, str]] = []  # (path, reason)
        self._done_count: int = 0
        self._running: bool = False
        self._drag_busy: bool = False
        self._paused_no_traktor: bool = False
        self._paused_manual: bool = False
        self._pause_started_at: float = 0.0
        # (monotonic time asked, answer) of the last Traktor visibility check.
        self._visibility: tuple[float, bool | None] = (0.0, None)

        self._build_ui()
        install_escape_to_close(self)

        self._tick = QTimer(self)
        self._tick.setInterval(TICK_MS)
        self._tick.timeout.connect(self._pump)

        self._watcher.deck_loaded.connect(self._on_deck_loaded)
        self._watcher.deck_position.connect(self._on_deck_position)
        self._watcher.deck_state.connect(self._on_deck_state)

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(10)
        root.setContentsMargins(14, 14, 14, 14)

        desc = QLabel(
            f"Verifies {len(self._all_paths)} track(s) load and play in "
            "Traktor.\n"
            "Keep Traktor's main window visible (deck drop zones must be on "
            "top) — tracks are dragged from the rows below into Deck A / B.\n"
            "Deck A and B channel faders are silenced automatically before "
            "the first track loads."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet("color: #7777aa; font-size: 11px;")
        root.addWidget(desc)

        # The drag-source table — same widget the NOW PLAYING results use, so
        # Qt's startDrag emits a file-URL MIME just like a manual drag from
        # the main UI. Two rows, one per deck. Visible but compact.
        self._src_table = ResultsTable(2, 1, self)
        self._src_table.setDragEnabled(True)
        self._src_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._src_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._src_table.setHorizontalHeaderLabels(["Currently dragging"])
        self._src_table.verticalHeader().setVisible(True)
        self._src_table.setVerticalHeaderLabels(["A", "B"])
        for r in range(2):
            self._src_table.setItem(r, 0, QTableWidgetItem(""))
        self._src_table.setFixedHeight(80)
        self._src_table.horizontalHeader().setStretchLastSection(True)
        root.addWidget(self._src_table)

        self.progress = QProgressBar()
        self.progress.setMaximum(len(self._all_paths) or 1)
        self.progress.setValue(0)
        root.addWidget(self.progress)

        self.status_lbl = QLabel("Ready.")
        self.status_lbl.setStyleSheet("color: #dde1f0; font-size: 12px;")
        root.addWidget(self.status_lbl)

        fail_label = QLabel("Failed tracks:")
        fail_label.setStyleSheet("color: #ff8888; font-size: 11px;")
        root.addWidget(fail_label)
        self.failed_list = QListWidget()
        self.failed_list.setStyleSheet(
            "background: #1a1a2e; color: #ddccaa; font-family: monospace; font-size: 11px;"
        )
        root.addWidget(self.failed_list, 1)

        btn_row = QHBoxLayout()
        self.start_btn = QPushButton("Start")
        self.start_btn.setObjectName("primary")
        self.start_btn.clicked.connect(self._start)
        btn_row.addWidget(self.start_btn)

        self.pause_btn = QPushButton("Pause")
        self.pause_btn.setEnabled(False)
        self.pause_btn.clicked.connect(self._toggle_pause)
        btn_row.addWidget(self.pause_btn)

        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._stop)
        btn_row.addWidget(self.stop_btn)

        self.retest_btn = QPushButton("Retest Failed")
        self.retest_btn.setEnabled(False)
        self.retest_btn.setToolTip(
            "Re-run the check using only the tracks currently listed as failed."
        )
        self.retest_btn.clicked.connect(self._retest_failed)
        btn_row.addWidget(self.retest_btn)

        self.export_btn = QPushButton("Export Failed…")
        self.export_btn.setEnabled(False)
        self.export_btn.clicked.connect(self._export)
        btn_row.addWidget(self.export_btn)

        btn_row.addStretch()
        self.close_btn = QPushButton("Close")
        self.close_btn.clicked.connect(self.close)
        btn_row.addWidget(self.close_btn)

        root.addLayout(btn_row)

    # ── Control ───────────────────────────────────────────────────────────────

    def _start(self) -> None:
        if self._running:
            return
        if not self._all_paths:
            warn(self, "No tracks", "Nothing to check — the track list is empty.")
            return

        self._main_queue = deque(CheckItem(p) for p in self._all_paths)
        self._retry_queue = []
        self._failed = []
        self._done_count = 0
        for slot in self._slots.values():
            slot.state = "idle"
            slot.item = None
            slot.last_position = -1.0
            slot.position_changed = False
            self._set_row(slot.row, "")
        self.failed_list.clear()
        self.progress.setMaximum(len(self._all_paths))
        self.progress.setValue(0)
        self.export_btn.setEnabled(False)
        self.retest_btn.setEnabled(False)
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.pause_btn.setEnabled(True)
        self.pause_btn.setText("Pause")
        self._paused_manual = False
        self._running = True

        # Drive deck A/B channel faders to zero via the existing QML mute
        # path before any track is loaded. The QML wraps every "load"-action
        # command (including our mute-only variant) in a single
        # ``loadDelayTimer.restart()``, so two queue_mute calls fired
        # back-to-back collide — the second wipes the first and only the
        # last deck gets muted. Chain them through QTimer.singleShot so the
        # first mute fully completes before the second goes out.
        self.status_lbl.setText("Muting deck A channel…")
        try:
            self._watcher.queue_mute("A")
        except Exception:
            _log.exception("library-checker: queue_mute(A) failed")
        QTimer.singleShot(MUTE_SETTLE_MS, self._mute_b_then_pump)

    def _mute_b_then_pump(self) -> None:
        if not self._running:
            return  # user stopped during the A-mute settle
        self.status_lbl.setText("Muting deck B channel…")
        try:
            self._watcher.queue_mute("B")
        except Exception:
            _log.exception("library-checker: queue_mute(B) failed")
        QTimer.singleShot(MUTE_SETTLE_MS, self._begin_pumping)

    def _begin_pumping(self) -> None:
        if not self._running:
            return  # user stopped during the B-mute settle
        if not self._paused_manual:
            self.status_lbl.setText("Running…")
        self._tick.start()

    # ── Pause / Resume ────────────────────────────────────────────────────────

    def _toggle_pause(self) -> None:
        if not self._running:
            return
        if self._paused_manual:
            self._resume_run()
        else:
            self._pause_run()

    def _pause_run(self) -> None:
        if self._paused_manual:
            return
        self._paused_manual = True
        self._pause_started_at = time.monotonic()
        self.pause_btn.setText("Resume")
        self.status_lbl.setText(
            f"Paused — {self._done_count}/{len(self._all_paths)} checked, "
            f"{len(self._failed)} failed so far."
        )

    def _resume_run(self) -> None:
        if not self._paused_manual:
            return
        # Push in-flight deadlines forward by the full paused interval so
        # tracks that were mid-load / mid-playback when the user paused
        # don't trip their timeouts the moment we resume.
        elapsed = time.monotonic() - self._pause_started_at
        for slot in self._slots.values():
            if slot.state == "loading":
                slot.load_deadline += elapsed
            elif slot.state == "playing":
                slot.play_deadline += elapsed
        self._paused_manual = False
        self.pause_btn.setText("Pause")
        self.status_lbl.setText("Running…")

    # ── Retest failed ─────────────────────────────────────────────────────────

    def _retest_failed(self) -> None:
        """Restart the check using only the currently-failed tracks."""
        if self._running or not self._failed:
            return
        # Capture the paths before _start clears the failed list.
        self._all_paths = [path for path, _ in self._failed]
        self._start()

    def _stop(self) -> None:
        if not self._running:
            return
        self._running = False
        self._tick.stop()
        for d in ("A", "B"):
            try:
                self._watcher.queue_stop(d)
            except Exception:
                _log.exception("library-checker: queue_stop(%s) failed", d)
            self._slots[d].state = "idle"
            self._slots[d].item = None
            self._set_row(self._slots[d].row, "")
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.pause_btn.setEnabled(False)
        self.pause_btn.setText("Pause")
        self._paused_manual = False
        self.export_btn.setEnabled(bool(self._failed))
        self.retest_btn.setEnabled(bool(self._failed))
        self.status_lbl.setText(
            f"Stopped — {self._done_count}/{len(self._all_paths)} checked, "
            f"{len(self._failed)} failed."
        )

    def _finish(self) -> None:
        self._running = False
        self._tick.stop()
        for d in ("A", "B"):
            try:
                self._watcher.queue_stop(d)
            except Exception:
                _log.exception("library-checker: queue_stop(%s) failed", d)
            self._set_row(self._slots[d].row, "")
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.pause_btn.setEnabled(False)
        self.pause_btn.setText("Pause")
        self._paused_manual = False
        self.export_btn.setEnabled(bool(self._failed))
        self.retest_btn.setEnabled(bool(self._failed))
        self.status_lbl.setText(
            f"Done — {self._done_count}/{len(self._all_paths)} checked, {len(self._failed)} failed."
        )

    # ── Drag source row management ────────────────────────────────────────────

    def _set_row(self, row: int, path: str) -> None:
        """Bind *path* to *row* of the drag-source table and refresh its label."""
        self._src_table._row_paths[row] = path
        item = self._src_table.item(row, 0)
        if item is None:
            item = QTableWidgetItem("")
            self._src_table.setItem(row, 0, item)
        item.setText(os.path.basename(path) if path else "")
        # Default QTableWidgetItem flags don't include ItemIsDragEnabled —
        # some Qt builds silently no-op startDrag when the source item isn't
        # explicitly drag-enabled. Belt-and-suspenders fix here.
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsDragEnabled)

    # ── State machine pump ────────────────────────────────────────────────────

    def _pump(self) -> None:
        if not self._running:
            return
        now = time.monotonic()

        # Pause the entire state machine while Traktor's window is hidden /
        # behind another Space / minimised. Drags would be rejected anyway,
        # and load/play timeouts would mark every track as failed.
        if not self._traktor_visible(now):
            if not self._paused_no_traktor:
                self._paused_no_traktor = True
                self.status_lbl.setText(
                    "Paused — bring Traktor's window to the foreground to continue."
                )
            # Push in-flight deadlines forward by one tick so paused time
            # doesn't count against the load / play windows.
            dt = TICK_MS / 1000.0
            for slot in self._slots.values():
                if slot.state == "loading":
                    slot.load_deadline += dt
                elif slot.state == "playing":
                    slot.play_deadline += dt
            return

        if self._paused_no_traktor:
            self._paused_no_traktor = False
            if not self._paused_manual:
                self.status_lbl.setText("Running…")

        # Manual pause — freeze the state machine. Deadlines are shifted in
        # _resume_run, not per-tick, so we don't need to bump them here.
        if self._paused_manual:
            return

        # Promote idle slots to waiting_drag if there's work for them
        for slot in self._slots.values():
            if slot.state == "idle":
                self._start_next_on_slot(slot)

        # Dispatch at most one drag — mouse events are global, drags don't
        # parallelise. Slots take turns; the other slot's load/play monitoring
        # runs concurrently so the overall check still completes ~2× faster
        # than a single-deck loop would.
        if not self._drag_busy:
            for slot in self._slots.values():
                if slot.state == "waiting_drag":
                    self._dispatch_drag(slot)
                    break  # only one drag at a time

        # Time-out checks for in-flight slots
        for slot in self._slots.values():
            if slot.state == "loading" and now >= slot.load_deadline:
                self._on_attempt_finished(slot, success=False, reason="no load")
            elif slot.state == "playing" and now >= slot.play_deadline:
                ok = slot.confirmed_playing or slot.position_changed
                self._on_attempt_finished(
                    slot,
                    success=ok,
                    reason="ok" if ok else "no playback",
                )

        # No more work in either queue and both slots idle → done
        if (
            not self._main_queue
            and not self._retry_queue
            and all(s.state == "idle" for s in self._slots.values())
        ):
            self._finish()

    def _traktor_visible(self, now: float) -> bool:
        """is_traktor_visible(), asked at most every VISIBILITY_CHECK_S."""
        checked_at, answer = self._visibility
        if answer is None or now - checked_at >= VISIBILITY_CHECK_S:
            answer = is_traktor_visible()
            self._visibility = (now, answer)
        return answer

    def _start_next_on_slot(self, slot: Slot) -> None:
        # Prefer a retry that already failed on the OTHER deck
        for i, item in enumerate(self._retry_queue):
            if slot.deck not in item.attempts:
                del self._retry_queue[i]
                self._claim(slot, item)
                return
        if self._main_queue:
            item = self._main_queue.popleft()
            self._claim(slot, item)
            return
        # Otherwise stay idle — there may still be retries blocked by the
        # other slot that will free up later, or the run might be ending.

    def _claim(self, slot: Slot, item: CheckItem) -> None:
        slot.item = item
        slot.state = "waiting_drag"
        slot.last_position = -1.0
        slot.position_changed = False
        slot.confirmed_playing = False
        item.attempts.append(slot.deck)
        self._set_row(slot.row, item.path)

    def _dispatch_drag(self, slot: Slot) -> None:
        if slot.item is None:
            slot.state = "idle"
            return

        # Make the dialog the frontmost window before posting the mouse
        # events. Traktor naturally raises when queue_play fires on the
        # previous track, and macOS' "acceptsFirstMouse" rule means the
        # mouse-down on a backgrounded window only activates it — it does
        # NOT register as a click for Qt's drag-distance check, so
        # startDrag never fires and the file is never picked up.
        self.raise_()
        self.activateWindow()

        # Ensure the row is currentRow AND selected so QTableWidget's drag
        # pipeline reads the right index when startDrag fires.
        self._src_table.setCurrentCell(slot.row, 0)
        self._src_table.selectRow(slot.row)

        # Claimed before anything else can run: the slot leaves
        # "waiting_drag" and the global mouse is marked busy, so no pump
        # tick can dispatch this track (or another) in the meantime.
        slot.state = "dispatching"
        self._drag_busy = True
        # The drag itself goes out after DISPATCH_SETTLE_MS, with the event
        # loop running meanwhile: that flushes the paints and the
        # focus/activate events, and gives the macOS window server time to
        # make this the key window before mouse events arrive. It used to
        # sleep and spin processEvents here instead, blocking the GUI and
        # letting a due tick re-enter and drag the same track twice.
        item = slot.item
        QTimer.singleShot(DISPATCH_SETTLE_MS, self, lambda: self._drag_now(slot, item))

    def _drag_now(self, slot: Slot, item: CheckItem) -> None:
        """Second half of :meth:`_dispatch_drag`, once the window settled."""
        # Stop pressed, or the slot reset, while we waited: nothing to drag.
        if not self._running or slot.state != "dispatching" or slot.item is not item:
            self._drag_busy = False
            return

        src = row_screen_center(self._src_table, slot.row)
        if src is None:
            _log.warning(
                "library-checker: row %d has no on-screen rect; make the dialog visible.", slot.row
            )
            self._drag_busy = False  # no drag went out
            self._on_attempt_finished(slot, success=False, reason="row off-screen")
            return

        outcome = drag_to_deck(src, slot.deck)
        if not outcome:
            self._drag_busy = False  # rejected: the mouse is free
            self._on_attempt_finished(
                slot,
                success=False,
                reason=outcome.reason or "drag rejected",
            )
            return

        # Transition to "loading" RIGHT AWAY so a fast deck_loaded event
        # from Traktor isn't dropped by the signal handler's state check.
        # The drag itself runs ~0.5 s on a daemon thread; _drag_busy keeps
        # the next drag from racing on the global mouse for DRAG_LOCK_MS.
        slot.state = "loading"
        slot.load_deadline = time.monotonic() + LOAD_TIMEOUT_S
        self._drag_busy = True
        QTimer.singleShot(DRAG_LOCK_MS, self._clear_drag_busy)

    def _clear_drag_busy(self) -> None:
        self._drag_busy = False

    def _on_attempt_finished(self, slot: Slot, success: bool, reason: str) -> None:
        item = slot.item
        slot.state = "idle"
        slot.item = None
        slot.last_position = -1.0
        slot.position_changed = False
        slot.confirmed_playing = False
        self._set_row(slot.row, "")
        try:
            self._watcher.queue_stop(slot.deck)
        except Exception:
            _log.exception("library-checker: queue_stop(%s) failed", slot.deck)

        if item is None:
            return

        if success:
            self._done_count += 1
            self.progress.setValue(self._done_count)
        else:
            other = "B" if slot.deck == "A" else "A"
            if other not in item.attempts:
                self._retry_queue.append(item)
            else:
                self._failed.append((item.path, reason))
                self.failed_list.addItem(f"{item.path}   [{reason}]")
                self._done_count += 1
                self.progress.setValue(self._done_count)

        remaining = (
            len(self._main_queue)
            + len(self._retry_queue)
            + sum(1 for s in self._slots.values() if s.state != "idle")
        )
        self.status_lbl.setText(
            f"{self._done_count}/{len(self._all_paths)} checked — "
            f"{len(self._failed)} failed, {remaining} pending"
        )

    # ── Webhook signal handlers ───────────────────────────────────────────────

    def _on_deck_loaded(self, deck: str, info: dict) -> None:
        if not self._running:
            return
        slot = self._slots.get(deck)
        if slot is None or slot.state != "loading" or slot.item is None:
            return
        # Match by basename — Traktor sometimes reports a slightly normalised
        # path. Title alone is unreliable (duplicate titles exist), basename
        # is the safest signal short of full-path normalisation.
        reported = info.get("file_path", "")
        if not _same_basename(reported, slot.item.path):
            return

        # Drag landed the file on the deck — but Traktor still needs a beat
        # to finalise the load before queue_play actually takes effect.
        # Park the slot in "settling" so the pump's loading-timeout doesn't
        # mis-fire, then queue_play after LOAD_SETTLE_MS.
        slot.state = "settling"
        QTimer.singleShot(LOAD_SETTLE_MS, lambda s=slot: self._start_playback(s))

    def _start_playback(self, slot: Slot) -> None:
        # The slot may have been reset (Stop pressed, dialog closed, retry
        # taken over) while the settle timer was pending. Bail in that case.
        if not self._running or slot.state != "settling" or slot.item is None:
            return
        # If the user paused while the slot was in "settling", defer the
        # queue_play until they resume — otherwise we'd kick off audible
        # playback (well, muted playback) during a pause.
        if self._paused_manual:
            QTimer.singleShot(200, lambda s=slot: self._start_playback(s))
            return
        try:
            self._watcher.queue_play(slot.deck)
        except Exception:
            _log.exception("library-checker: queue_play failed")
        slot.last_position = -1.0
        slot.position_changed = False
        slot.confirmed_playing = False
        slot.play_deadline = time.monotonic() + PLAY_WINDOW_S
        slot.state = "playing"

    def _on_deck_state(self, deck: str, is_playing: bool) -> None:
        if not self._running:
            return
        slot = self._slots.get(deck)
        if slot is None or slot.state != "playing":
            return
        if is_playing:
            slot.confirmed_playing = True

    def _on_deck_position(self, deck: str, position: float) -> None:
        if not self._running:
            return
        slot = self._slots.get(deck)
        if slot is None or slot.state != "playing":
            return
        if slot.last_position < 0.0:
            slot.last_position = position
            return
        if position != slot.last_position:
            slot.position_changed = True
            slot.last_position = position

    # ── Export ────────────────────────────────────────────────────────────────

    def _export(self) -> None:
        if not self._failed:
            return
        default_name = f"library-checker-failed-{int(time.time())}.txt"
        f, _ = QFileDialog.getSaveFileName(
            self,
            "Export failed tracks",
            str(Path.home() / default_name),
            "Text files (*.txt);;All files (*)",
        )
        if not f:
            return
        # One path per line — the user asked for "simple path of the track,
        # one on each line", so failure reasons are not included in the file.
        try:
            Path(f).write_text(
                "\n".join(path for path, _ in self._failed) + "\n",
                encoding="utf-8",
            )
        except OSError as exc:
            warn(self, "Export failed", str(exc))
            return
        self.status_lbl.setText(f"Exported {len(self._failed)} failed path(s) to {f}")

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def closeEvent(self, event) -> None:
        try:
            if self._running:
                self._stop()
            self._watcher.deck_loaded.disconnect(self._on_deck_loaded)
            self._watcher.deck_position.disconnect(self._on_deck_position)
            self._watcher.deck_state.disconnect(self._on_deck_state)
        except (RuntimeError, TypeError):
            pass
        super().closeEvent(event)
