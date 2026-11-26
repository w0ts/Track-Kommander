"""The banner's Update button: ask, download with progress, install, reopen.

Where installing in place cannot be done — run from source, no installer
attached to the release, a build not Developer ID signed — or fails, the
release page opens instead, which is all the button did before.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QProgressBar, QWidget

from core.update_check import Release

log = logging.getLogger(__name__)


class _DownloadRelay(QObject):
    """Carries the download's progress and outcome to the GUI thread."""

    progress = Signal(int, int)  # (bytes so far, total or 0)
    finished = Signal(str, str)  # (installer path or "", error or "")


def open_page(url: str) -> None:
    QDesktopServices.openUrl(QUrl(url))


def run_update(parent: QWidget, release: Release, quit_app: Callable[[], None]) -> None:
    """Offer to install *release*; on yes, download it and hand over."""
    from core import self_update
    from ui import dialogs
    from ui.inline_dialog import host_for

    host = host_for(parent)
    if release.installer is None or not self_update.supported() or host is None:
        open_page(release.url)
        return
    if not dialogs.confirm(
        parent,
        "Update Track Kommander",
        f"Install Track Kommander {release.version} now?\n\n"
        "It is downloaded first; then Track Kommander quits, the new "
        "version is installed, and it opens again.",
        yes_text="Install",
        no_text="Not now",
    ):
        return
    installer, error = _download(host, release)
    if installer is None:
        if error:
            _failed(parent, release, error)
        return
    try:
        self_update.hand_over(installer)
    except self_update.UpdateError as exc:
        _failed(parent, release, str(exc))
        return
    quit_app()


def _download(host: QWidget, release: Release) -> tuple[Path | None, str]:
    """Download in a daemon thread under an inline progress card.

    Returns (installer path, "") or (None, why); why is "" when the user
    cancelled. A daemon thread, not a QThread, for the reason given in
    ``HealthMixin._run_update_check``.
    """
    from core import self_update
    from ui.inline_dialog import InlineDialog

    asset = release.installer
    assert asset is not None
    card = InlineDialog(
        host,
        "Downloading the update",
        f"Track Kommander {release.version} · {asset.name}",
        buttons=[("cancel", "Cancel")],
        escape="cancel",
    )
    bar = QProgressBar()
    bar.setRange(0, 0)
    card.add_widget(bar)

    stop = threading.Event()
    outcome: dict[str, str] = {}
    # Owned by the window, on the GUI thread: if only the thread held it, a
    # cancelled download would free it on that thread (the Check for updates
    # crash, about_page._check_now).
    relay = _DownloadRelay(host)

    def on_progress(done: int, total: int) -> None:
        if total:
            bar.setRange(0, 1000)
            bar.setValue(int(done * 1000 / total))

    def on_finished(path: str, error: str) -> None:
        outcome.update(path=path, error=error)
        card.finish("done")

    relay.progress.connect(on_progress)
    relay.finished.connect(on_finished)

    def fetch() -> None:
        try:
            work = self_update.fresh_work_dir()
            path = self_update.download(asset, work, progress=report, cancelled=stop.is_set)
            finish(str(path), "")
        except self_update.Cancelled:
            finish("", "")
        except self_update.UpdateError as exc:
            finish("", str(exc))
        except Exception as exc:  # the thread must always answer
            log.exception("update: download crashed")
            finish("", f"unexpected error: {exc}")

    def report(done: int, total: int) -> None:
        if not stop.is_set():
            relay.progress.emit(done, total)

    def finish(path: str, error: str) -> None:
        try:
            relay.finished.emit(path, error)
        except RuntimeError:  # the window closed during the download
            log.info("update: download ended after the window closed")

    threading.Thread(target=fetch, name="update-download", daemon=True).start()
    if card.run() != "done":
        stop.set()  # Cancel, or the window went away: the thread ends itself
        log.info("update: download cancelled")
        return None, ""
    if outcome.get("path"):
        return Path(outcome["path"]), ""
    return None, outcome.get("error", "")


def _failed(parent: QWidget, release: Release, why: str) -> None:
    from ui import dialogs

    log.warning("update: not installed in place: %s", why)
    if dialogs.confirm(
        parent,
        "Update not installed",
        f"Track Kommander {release.version} could not be installed automatically:\n\n"
        f"{why}\n\nOpen the release page to download it yourself?",
        yes_text="Open page",
        no_text="Close",
    ):
        open_page(release.url)
