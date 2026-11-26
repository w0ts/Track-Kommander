"""Constants and helpers shared by the Settings tab and its pages.

They sit apart from ``ui.settings`` so each page module can use them
without importing the tab that imports the page.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

#: One logger for the tab and every page module. It keeps the name the log
#: has always carried for Settings, so a shared ``log.log`` and any filter on
#: ``ui.settings`` read the same after the split.
log = logging.getLogger("ui.settings")

D2_EXPECTED_VERSION = "8"

#: QSettings key: arm Sync on every deck as soon as the bridge connects.
SYNC_ALL_ON_CONNECT_KEY = "sync_all_on_connect"

#: How the D2 dialogs warn about the elevation prompt that is coming. The
#: two platforms ask differently, and promising a Windows user a macOS
#: password dialog is the kind of small wrongness that makes people cancel.
ELEVATION_NOTE = (
    "macOS will ask for your password."
    if sys.platform == "darwin"
    else "Windows will ask you to confirm as administrator."
)


def _build_csi_candidates() -> list[Path]:
    """Return candidate Traktor QML/CSI directories for the current OS.

    * Windows: ``<ProgramFiles>\\Native Instruments\\Traktor Pro N\\Resources64\\qml\\CSI``
    * macOS:   the QML inside the Traktor ``.app`` bundle.
    The list is filtered to the existing path later (``_resolve_qml_path``), so
    returning entries that don't exist on a given box is harmless.
    """
    if sys.platform == "win32":
        import os

        roots: list[Path] = []
        for env in ("ProgramW6432", "ProgramFiles"):
            v = os.environ.get(env)
            if v and Path(v) not in roots:
                roots.append(Path(v))
        if not roots:
            roots.append(Path(r"C:\Program Files"))
        return [
            root / "Native Instruments" / ver / "Resources64" / "qml" / "CSI"
            for root in roots
            for ver in ("Traktor Pro 4", "Traktor Pro 3")
        ]
    if sys.platform == "darwin":
        return [
            Path(
                "/Applications/Native Instruments/Traktor Pro 4/Traktor Pro 4.app"
                "/Contents/Resources/qml/CSI"
            ),
            Path(
                "/Applications/Native Instruments/Traktor Pro 3/Traktor Pro 3.app"
                "/Contents/Resources/qml/CSI"
            ),
            Path("/Applications/Traktor Pro 4.app/Contents/Resources/qml/CSI"),
        ]
    return []


def _stat_key(path) -> tuple:
    """``(path, mtime_ns, size)`` for *path*, or ``(path, None, None)`` when
    it cannot be stat'ed — a file appearing or vanishing changes the key too.
    """
    try:
        st = Path(path).stat()
    except OSError:
        return (str(path), None, None)
    return (str(path), st.st_mtime_ns, st.st_size)


def bridge_stats_text(received: int, last_age_s: float | None) -> str:
    """Human-readable summary of D2 bridge traffic.

    "Connected" only means a ping arrived at some point; this says whether
    messages are still flowing and how stale the last one is, which is the
    thing to look at when the bridge goes quiet.
    """
    if received <= 0:
        return "No data received from Traktor yet"
    if last_age_s is None:
        age = "—"
    elif last_age_s < 2:
        age = "just now"
    elif last_age_s < 90:
        age = f"{last_age_s:.0f}s ago"
    else:
        age = f"{last_age_s / 60:.0f}m ago"
    noun = "message" if received == 1 else "messages"
    return f"{received:,} {noun} from Traktor · last {age}"
