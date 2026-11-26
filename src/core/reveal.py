"""Show a file in the desktop's file manager, selected."""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

log = logging.getLogger(__name__)


def _open_url(url: str) -> bool:
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QDesktopServices

    return bool(QDesktopServices.openUrl(QUrl(url)))


def reveal_in_file_manager(path: str) -> bool:
    """Open Finder / Explorer with *path* selected; elsewhere, its folder.

    Returns False when the file is not there, or the launch failed.
    """
    p = Path(path)
    if not p.exists():
        log.info("reveal: %s does not exist", p)
        return False
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(p)])
        elif sys.platform == "win32":
            # explorer wants the switch and the path as one argument, with no
            # space after the comma; a path with spaces needs no quoting here.
            subprocess.Popen(["explorer", f"/select,{p}"])
        else:
            return _open_url(p.parent.as_uri())
    except OSError:
        log.warning("reveal: could not open a file manager for %s", p, exc_info=True)
        return False
    return True
