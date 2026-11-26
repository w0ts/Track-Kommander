"""History replay window: how would each matching setup have ranked the tracks
you actually played next?

Opened from the stats page. Replays Traktor's play history (core.history_replay)
under your current axes, then with each axis added or taken out, and shows
where the next-played track landed. A sanity check, not a verdict — the note
at the top says why.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QDate, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QDateEdit,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from core.history_replay import Result
from ui.dialogs import install_escape_to_close
from ui.style import T

_NOTE = (
    "For each pair of tracks in your Traktor history played one after the "
    "other, the library is ranked against the first: where did the second "
    "land? <b>R@10</b> is how often it was in the top 10, <b>MRR</b> the mean "
    "of 1 / its rank. Every setup is scored on the same transitions.<br><br>"
    "<i>Read it as a sanity check, not a verdict.</i> Your history was made by "
    "browsing — with these suggestions on screen, or your own crates — so it "
    "rewards rankings that look like how you chose, not necessarily better "
    "transitions. A setup that makes things clearly worse is worth noticing; "
    "a small gain is not proof."
)
_WORSE = "#e05252"
_COLUMNS = ("Setup", "R@10", "Δ", "R@50", "Δ", "MRR", "Δ", "Median rank")


class ReplayDialog(QDialog):
    def __init__(self, axes_provider: Callable[[], list], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("History replay")
        self.resize(760, 640)
        self._axes_provider = axes_provider
        self._worker = None
        self._results: list[Result] = []

        lay = QVBoxLayout(self)
        note = QLabel(_NOTE)
        note.setWordWrap(True)
        note.setTextFormat(Qt.TextFormat.RichText)
        note.setStyleSheet(f"color: {T.TEXT_DIM}; font-size: 12px;")
        lay.addWidget(note)

        row = QHBoxLayout()
        self._run_btn = QPushButton("Replay")
        self._run_btn.clicked.connect(self._start)
        row.addWidget(self._run_btn)
        self._stop_btn = QPushButton("Stop")
        self._stop_btn.setEnabled(False)
        self._stop_btn.clicked.connect(self._stop)
        row.addWidget(self._stop_btn)
        self._before_cb = QCheckBox("Only sets before")
        row.addWidget(self._before_cb)
        self._before = QDateEdit(QDate.currentDate())
        self._before.setCalendarPopup(True)
        self._before.setDisplayFormat("yyyy-MM-dd")
        self._before.setEnabled(False)
        self._before_cb.toggled.connect(self._before.setEnabled)
        row.addWidget(self._before)
        row.addStretch()
        lay.addLayout(row)

        self._progress = QProgressBar()
        self._progress.setValue(0)
        lay.addWidget(self._progress)
        self._status = QLabel("Replays your current axes, then each axis added or taken out.")
        self._status.setStyleSheet(f"color: {T.TEXT_DIM}; font-size: 11px;")
        lay.addWidget(self._status)

        self._table = QTableWidget(0, len(_COLUMNS))
        self._table.setHorizontalHeaderLabels(_COLUMNS)
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for c in range(1, len(_COLUMNS)):
            header.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        lay.addWidget(self._table, stretch=1)
        install_escape_to_close(self)

    # ── running ──────────────────────────────────────────────────────────────

    def _start(self) -> None:
        from workers.replay_worker import ReplayWorker

        if self._worker is not None:
            return
        current = [(a.key, a.strength) for a in self._axes_provider() if a.strength > 0]
        before = self._before.date().toPython() if self._before_cb.isChecked() else None
        self._results = []
        self._table.setRowCount(0)
        self._worker = ReplayWorker(current, before)
        self._worker.started_replay.connect(self._on_started)
        self._worker.result.connect(self._on_result)
        self._worker.finished.connect(self._on_finished)
        self._worker.error.connect(lambda msg: self._status.setText(msg))
        self._run_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)
        self._status.setText("Reading the history and the library…")
        self._worker.start()

    def _stop(self) -> None:
        if self._worker is not None:
            self._worker.stop()
            self._stop_btn.setEnabled(False)
            self._status.setText("Stopping after the setup in hand…")

    def _on_started(self, n_transitions: int, n_configs: int) -> None:
        self._progress.setMaximum(n_configs)
        self._progress.setValue(0)
        self._status.setText(f"{n_transitions} transitions, {n_configs} setups…")

    def _on_result(self, result: Result) -> None:
        self._results.append(result)
        self._progress.setValue(len(self._results))
        self._render()

    def _on_finished(self, why_not: str) -> None:
        from ui.worker_shutdown import keep_until_finished

        worker, self._worker = self._worker, None
        keep_until_finished(worker)
        self._run_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)
        if why_not:
            self._status.setText(why_not)
        elif self._results:
            n = self._results[0].n
            self._status.setText(
                f"{n} transitions. Green: better than your setup; red: worse. Sorted by MRR."
            )

    def done(self, result: int) -> None:
        """Closing while it runs: the replay stops, the window goes."""
        from ui.worker_shutdown import keep_until_finished

        if self._worker is not None:
            self._worker.stop()
            keep_until_finished(self._worker)
        super().done(result)

    # ── the table ────────────────────────────────────────────────────────────

    def _render(self) -> None:
        if not self._results:
            return
        base = self._results[0]
        rows = [base, *sorted(self._results[1:], key=lambda r: -r.mrr)]
        self._table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            median = f"{r.median_rank:.0f}" if r.median_rank is not None else "—"
            cells = [
                (r.name, None),
                (f"{r.recall(10):.1%}", None),
                *_delta(r.recall(10) - base.recall(10), pct=True, show=i > 0),
                (f"{r.recall(50):.1%}", None),
                *_delta(r.recall(50) - base.recall(50), pct=True, show=i > 0),
                (f"{r.mrr:.3f}", None),
                *_delta(r.mrr - base.mrr, pct=False, show=i > 0),
                (median, None),
            ]
            for c, (text, colour) in enumerate(cells):
                item = QTableWidgetItem(text)
                if c > 0:
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                    )
                if colour is not None:
                    item.setForeground(QColor(colour))
                if i == 0:
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                self._table.setItem(i, c, item)


def _delta(diff: float, *, pct: bool, show: bool) -> list[tuple[str, str | None]]:
    """The difference to the current setup, coloured; blank on its own row."""
    if not show:
        return [("", None)]
    if abs(diff) < (0.0005 if not pct else 0.00005):
        return [("=", T.TEXT_DIM)]
    text = f"{diff:+.1%}" if pct else f"{diff:+.3f}"
    return [(text, T.GREEN if diff > 0 else _WORSE)]
