"""The two log actions, shared by the macOS Debug menu and Settings.

On Windows the menu bar is hidden (it would render as stray buttons in a
frameless window), so Settings → General carries the same two buttons.
"""

from __future__ import annotations

import logging

from PySide6.QtWidgets import QWidget

log = logging.getLogger(__name__)


def show_log_folder(parent: QWidget | None) -> None:
    """Open the folder holding log.log, with the file selected."""
    from core.reveal import reveal_in_file_manager
    from core.runtime import user_log_dir
    from ui.dialogs import warn

    path = user_log_dir() / "log.log"
    if not reveal_in_file_manager(str(path if path.exists() else user_log_dir())):
        warn(parent, "Log folder", f"The log is in:\n{user_log_dir()}")


def share_log(parent: QWidget | None) -> None:
    """Zip the logs with a diagnostics report onto the Desktop and show it."""
    from core.log_bundle import default_destination, write_bundle
    from core.reveal import reveal_in_file_manager
    from core.runtime import user_log_dir
    from ui.dialogs import info, warn

    try:
        bundle = write_bundle(user_log_dir(), default_destination())
    except OSError as exc:
        log.warning("could not write the log bundle", exc_info=True)
        warn(parent, "Share log", f"Could not write the log bundle:\n{exc}")
        return
    reveal_in_file_manager(str(bundle))
    info(
        parent,
        "Share log",
        f"Saved {bundle.name} to {bundle.parent}.\n\n"
        "Send that file to whoever is helping you. It holds the app's logs "
        "and a short report (version, system, analysis state) — no "
        "settings, no music.\n\nFor more detail, turn on Settings → General "
        "→ Verbose logging, repeat what went wrong, then share again.",
    )
