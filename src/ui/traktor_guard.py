"""Hold a collection write until Traktor is closed.

Traktor rewrites ``collection.nml`` when it quits, so anything written into
it while Traktor runs is silently undone. The old prompt offered "Continue
anyway?" — which only ever lost the user's work. Now a write waits:

* a small window says why, and watches Traktor — it carries on by itself the
  moment Traktor has closed, however it was closed;
* **Quit Traktor for me** asks Traktor to quit politely (it saves its own
  collection first; nothing is ever force-killed), and Traktor is started
  again once the write is done;
* **Cancel** drops the write.
"""

from __future__ import annotations

import logging
import time

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

log = logging.getLogger(__name__)

#: How often to look whether Traktor is still running, in ms.
POLL_MS = 500

#: After asking Traktor to quit, how long before saying it may be stuck.
QUIT_PATIENCE_S = 20.0


class TraktorClosedDialog(QDialog):
    """Waits for Traktor to close; accepted once it has."""

    def __init__(self, action: str, parent=None, *, running=None, ask_to_quit=None) -> None:
        super().__init__(parent)
        from core.traktor import _ask_traktor_to_quit, is_traktor_running

        self._running = running or is_traktor_running
        self._ask_to_quit = ask_to_quit or _ask_traktor_to_quit
        #: True once this dialog asked Traktor to quit: the caller then
        #: starts it again after writing.
        self.quit_by_us = False
        self._asked_at: float | None = None

        self.setWindowTitle("Traktor is running")
        lay = QVBoxLayout(self)
        text = QLabel(
            f"{action} needs Traktor closed.\n\n"
            "Traktor rewrites its collection when it quits, so a change made "
            "while it runs would be lost.\n\n"
            "Quit Traktor — this carries on by itself once it has closed."
        )
        text.setWordWrap(True)
        lay.addWidget(text)
        self.status = QLabel("Waiting for Traktor to close…")
        self.status.setStyleSheet("color: #7777aa;")
        self.status.setWordWrap(True)
        lay.addWidget(self.status)

        row = QHBoxLayout()
        row.addStretch()
        self.quit_btn = QPushButton("Quit Traktor for me")
        self.quit_btn.setToolTip(
            "Asks Traktor to quit (it saves its collection first), writes, "
            "then starts Traktor again."
        )
        self.quit_btn.clicked.connect(self._quit_for_me)
        row.addWidget(self.quit_btn)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)
        lay.addLayout(row)

        self._timer = QTimer(self)
        self._timer.setInterval(POLL_MS)
        self._timer.timeout.connect(self._check)
        self._timer.start()

    def _quit_for_me(self) -> None:
        self.quit_btn.setEnabled(False)
        if self._ask_to_quit():
            self.quit_by_us = True
            self._asked_at = time.monotonic()
            self.status.setText("Asking Traktor to quit…")
        else:
            self.quit_btn.setEnabled(True)
            self.status.setText("Could not ask Traktor to quit — quit it yourself.")

    def _check(self) -> None:
        if not self._running():
            self._timer.stop()
            self.accept()
            return
        if self._asked_at is not None and time.monotonic() - self._asked_at > QUIT_PATIENCE_S:
            self.status.setText(
                "Traktor is still open — it may be asking you something. Check its window."
            )


def _wait_inline(parent, action: str, running) -> tuple[bool, bool] | None:
    """The same wait, drawn inside the main window; None without one.

    A window of its own stayed hidden under the docked, always-on-top main
    window (see ui.inline_dialog).
    """
    from core.traktor import _ask_traktor_to_quit
    from ui.inline_dialog import InlineDialog, host_for

    host = host_for(parent)
    if host is None:
        return None
    dlg = InlineDialog(
        host,
        "Traktor is running",
        f"{action} needs Traktor closed.\n\n"
        "Traktor rewrites its collection when it quits, so a change made while "
        "it runs would be lost.\n\nQuit Traktor — this carries on by itself "
        "once it has closed.",
        buttons=[("cancel", "Cancel"), ("quit", "Quit Traktor for me")],
        escape="cancel",
    )
    status = QLabel("Waiting for Traktor to close…")
    status.setStyleSheet("color: #7777aa; background: transparent;")
    status.setWordWrap(True)
    dlg.add_widget(status)
    state = {"quit_by_us": False, "asked_at": None}
    quit_btn = dlg.buttons["quit"]
    quit_btn.clicked.disconnect()  # asking to quit is not an answer

    def _quit() -> None:
        quit_btn.setEnabled(False)
        if _ask_traktor_to_quit():
            state["quit_by_us"] = True
            state["asked_at"] = time.monotonic()
            status.setText("Asking Traktor to quit…")
        else:
            quit_btn.setEnabled(True)
            status.setText("Could not ask Traktor to quit — quit it yourself.")

    def _check() -> None:
        if not running():
            dlg.finish("closed")
        elif (
            state["asked_at"] is not None and time.monotonic() - state["asked_at"] > QUIT_PATIENCE_S
        ):
            status.setText(
                "Traktor is still open — it may be asking you something. Check its window."
            )

    quit_btn.clicked.connect(_quit)
    timer = QTimer(dlg)
    timer.setInterval(POLL_MS)
    timer.timeout.connect(_check)
    timer.start()
    closed = dlg.run() == "closed"
    log.info(
        "waited for Traktor to close for %r: %s%s",
        action,
        "closed" if closed else "cancelled",
        " (quit by us)" if state["quit_by_us"] else "",
    )
    return closed, closed and state["quit_by_us"]


def wait_for_traktor_closed(parent, action: str, *, running=None) -> tuple[bool, bool]:
    """Block the write until Traktor is closed.

    Returns ``(go_ahead, relaunch_after)``: *go_ahead* is False if the user
    cancelled; *relaunch_after* is True if Traktor was quit from here and
    should be started again once the write is done. *running* is the
    Traktor check to use — the caller's own, so both agree.
    """
    if running is None:
        from core.traktor import is_traktor_running as running
    if not running():
        return True, False
    inline = _wait_inline(parent, action, running)
    if inline is not None:
        return inline
    dlg = TraktorClosedDialog(action, parent, running=running)
    accepted = dlg.exec() == QDialog.DialogCode.Accepted
    log.info(
        "waited for Traktor to close for %r: %s%s",
        action,
        "closed" if accepted else "cancelled",
        " (quit by us)" if dlg.quit_by_us else "",
    )
    return accepted, accepted and dlg.quit_by_us
