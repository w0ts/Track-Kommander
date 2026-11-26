"""UpdateCheckWorker — asks GitHub for a newer release off the UI thread.

One request, up to :data:`core.update_check._TIMEOUT_S` long; emits ``found``
only when a newer release exists, and nothing at all otherwise.
"""

from __future__ import annotations

from PySide6.QtCore import QThread, Signal

from core.update_check import check_for_update


class UpdateCheckWorker(QThread):
    found = Signal(str, str)  # (version, release page url)

    def __init__(self, current: str, parent=None) -> None:
        super().__init__(parent)
        self._current = current

    def run(self) -> None:
        release = check_for_update(self._current)
        if release is not None:
            self.found.emit(release.version, release.url)
