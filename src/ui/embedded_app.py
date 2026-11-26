"""Bootstrap for the embedded Track Kommander (Mac-only, injected Traktor).

A lean sibling of :func:`ui.app.main` that shows :class:`EmbeddedWindow`
instead of the full ``MainWindow``. The shipped app's bootstrap in
``ui.app`` is deliberately untouched. See
``traktor-native/notes/embedded-ui-plan.md``.

Identity is kept the same as the external app (``APP_NAME``) so the embedded
variant resolves to the same per-user data dir / library when it starts
reusing ``core/`` for matching.
"""

from __future__ import annotations

import logging
import sys

from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication

from core.constants import APP_NAME

log = logging.getLogger(__name__)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
        datefmt="%H:%M:%S",
    )

    # Identity before any QSettings/QStandardPaths use, exactly as ui.app does.
    QCoreApplication.setApplicationName(APP_NAME)
    QCoreApplication.setOrganizationDomain("trackkommander.app")

    app = QApplication(sys.argv)
    try:
        from ui.style import STYLE

        app.setStyleSheet(STYLE)
    except Exception:
        log.debug("embedded: stylesheet not applied", exc_info=True)

    from ui.embedded_window import EmbeddedWindow

    win = EmbeddedWindow()
    win.show()
    win.start()  # bind the loopback webhook only once the window is up
    log.info("embedded: window up")

    sys.exit(app.exec())
