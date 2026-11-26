"""Settings ▸ About — where to get new builds and the docs, the app's licence, what it is built on, and where the
source of its LGPL libraries is.

The LGPL libraries' obligations are met by the files under ``licenses/``;
this page is how a user finds them. Data comes from :mod:`core.licensing`.
"""

from __future__ import annotations

import logging
import threading

from PySide6.QtCore import QObject, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core import licensing
from core.log_bundle import app_version
from core.reveal import reveal_in_file_manager
from ui.style import T

log = logging.getLogger(__name__)

APP_LICENCE = "GNU General Public License v3.0"
APP_COPYRIGHT = "Copyright © 2026 w0ts"
#: The public, binary-only repo: releases, and the README as documentation.
PUBLIC_REPO_URL = "https://github.com/w0ts/Track-Kommander"

_NOTE_CSS = f"color: {T.TEXT_DIM}; font-size: 11px;"


def about_groups() -> tuple[QGroupBox, ...]:
    """The About tab's groups, top to bottom."""
    return (_website_group(), _app_group(), _components_group(), _source_group())


def _note(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setWordWrap(True)
    lbl.setStyleSheet(_NOTE_CSS)
    return lbl


def _open_file(name: str) -> None:
    """Open a file from ``licenses/`` in the default viewer."""
    path = licensing.licence_file(name)
    if path is None:
        log.warning("about: %s is not in this build", name)
        return
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))


def _file_button(label: str, name: str) -> QPushButton:
    btn = QPushButton(label)
    btn.setEnabled(licensing.licence_file(name) is not None)
    btn.clicked.connect(lambda: _open_file(name))
    return btn


def _button_row(*buttons: QWidget) -> QHBoxLayout:
    row = QHBoxLayout()
    row.setSpacing(8)
    for b in buttons:
        row.addWidget(b)
    row.addStretch()
    return row


def _link_button(label: str, url: str) -> QPushButton:
    btn = QPushButton(label)
    btn.setToolTip(url)
    btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(url)))
    return btn


class _CheckRelay(QObject):
    """Carries a Check for updates answer from its thread to the GUI thread."""

    done = Signal(object, bool)  # (newer Release or None, GitHub answered)


def _update_button(status: QLabel) -> QPushButton:
    btn = QPushButton("Check for updates")
    btn.setToolTip("Ask GitHub whether a newer Track Kommander is out")
    btn.clicked.connect(lambda: _check_now(btn, status))
    return btn


def _check_now(btn: QPushButton, status: QLabel) -> None:
    """Ask GitHub on a daemon thread (the reason is in
    ``HealthMixin._run_update_check``); a newer release goes to the same
    Update… flow as the banner's."""
    from core import update_check

    btn.setEnabled(False)
    btn.setText("Checking…")
    # Owned by the button, on the GUI thread: kept alive by the thread alone,
    # the thread freed it while its answer was being delivered — a SIGSEGV
    # in 0.0.8 (2026-10-10).
    relay = _CheckRelay(btn)
    current = app_version()

    def answered(release, ok: bool) -> None:
        if not ok:
            btn.setEnabled(True)
            btn.setText("Check for updates")
            status.setText("Could not reach GitHub — try again later.")
        elif release is None:
            btn.setText("Up to date")
            status.setText(f"{current} is the latest version.")
        else:
            btn.setEnabled(True)
            btn.setText("Check for updates")
            status.setText(f"Track Kommander {release.version} is out.")
            win = btn.window()
            offer = getattr(win, "offer_update", None)
            if offer is not None:
                offer(release)
            else:
                QDesktopServices.openUrl(QUrl(release.url))
        status.show()

    relay.done.connect(answered)

    def ask() -> None:
        try:
            data = update_check.fetch_latest_release()
        except Exception as exc:
            log.info("check for updates: no answer (%s)", exc)
            answer(None, False)
            return
        answer(update_check.check_for_update(current, fetch=lambda: data), True)

    def answer(release, ok: bool) -> None:
        try:
            relay.done.emit(release, ok)
        except RuntimeError:  # Settings closed while GitHub was asked
            log.info("check for updates: answered after Settings closed")

    threading.Thread(target=ask, name="update-check-now", daemon=True).start()


def _website_group() -> QGroupBox:
    grp = QGroupBox("UPDATES AND DOCUMENTATION")
    lay = QVBoxLayout(grp)
    lay.setSpacing(8)
    link = QLabel(
        f"You are running version {app_version()}. New builds and the "
        f"documentation are at "
        f'<a href="{PUBLIC_REPO_URL}" style="color: {T.BLUE};">'
        f"{PUBLIC_REPO_URL.removeprefix('https://')}</a>."
    )
    link.setWordWrap(True)
    link.setOpenExternalLinks(True)
    lay.addWidget(link)
    status = _note("")
    status.hide()
    lay.addLayout(
        _button_row(
            _update_button(status),
            _link_button("Documentation", f"{PUBLIC_REPO_URL}#readme"),
        )
    )
    lay.addWidget(status)
    return grp


def _app_group() -> QGroupBox:
    grp = QGroupBox("TRACK KOMMANDER")
    lay = QVBoxLayout(grp)
    lay.setSpacing(8)
    lay.addWidget(
        QLabel(
            f"{APP_COPYRIGHT}. Free software under the {APP_LICENCE}: share and change it under its terms."
        )
    )
    lay.addWidget(
        _note(
            "Not affiliated with, endorsed by, or sponsored by Native Instruments. "
            "“Traktor”, “Traktor Pro” and “Traktor Kontrol” are trademarks of "
            "Native Instruments GmbH, used only to describe what this app works with."
        )
    )
    lay.addLayout(
        _button_row(
            _file_button("View licence", "LICENSE.txt"),
            _file_button("Third-party audit", "THIRD_PARTY.md"),
        )
    )
    return grp


def _components_group() -> QGroupBox:
    grp = QGroupBox("OPEN-SOURCE COMPONENTS")
    lay = QVBoxLayout(grp)
    lay.setSpacing(8)
    lay.addWidget(
        _note(
            "Track Kommander is built on the software below, each under its own "
            "licence. Double-click a row to open its project page."
        )
    )
    lay.addWidget(_components_table(licensing.components()))

    folder_btn = QPushButton("Show licence files")
    folder_btn.clicked.connect(_reveal_licences)
    lay.addLayout(
        _button_row(
            _file_button("Full licence texts", "THIRD_PARTY_NOTICES.txt"),
            folder_btn,
        )
    )
    return grp


def _components_table(rows: tuple[licensing.Component, ...]) -> QTableWidget:
    table = QTableWidget(len(rows), 3)
    table.setHorizontalHeaderLabels(["Component", "Version", "Licence"])
    table.verticalHeader().setVisible(False)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    header = table.horizontalHeader()
    header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
    header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
    header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
    for r, comp in enumerate(rows):
        for c, text in enumerate((comp.name, comp.version, comp.licence)):
            item = QTableWidgetItem(text)
            # Long names and licences elide; the tooltip always has them whole.
            item.setToolTip("\n".join(filter(None, (text, comp.url))))
            if comp.url:
                item.setData(Qt.ItemDataRole.UserRole, comp.url)
            table.setItem(r, c, item)
    table.setMinimumHeight(320)
    table.itemDoubleClicked.connect(_open_project)
    return table


def _open_project(item: QTableWidgetItem) -> None:
    url = item.data(Qt.ItemDataRole.UserRole)
    if url:
        QDesktopServices.openUrl(QUrl(url))


def _reveal_licences() -> None:
    notices = licensing.licences_dir() / "THIRD_PARTY_NOTICES.txt"
    reveal_in_file_manager(str(notices))


def _source_group() -> QGroupBox:
    grp = QGroupBox("SOURCE CODE")
    lay = QVBoxLayout(grp)
    lay.setSpacing(8)
    tarball = licensing.ffmpeg_source()
    lay.addWidget(
        _note(
            "Qt, FFmpeg, libsndfile and soxr are used under the GNU LGPL. They ship as "
            "separate libraries, and you may replace them with your own builds.\n\n"
            f"The complete source of the bundled FFmpeg — unmodified FFmpeg "
            f"{licensing.FFMPEG_VERSION} and the build script that records how it was "
            "configured — is included with the app, in licenses/source/."
        )
    )
    src_btn = QPushButton("Show FFmpeg source")
    src_btn.setEnabled(tarball is not None)
    if tarball is not None:
        src_btn.clicked.connect(lambda: reveal_in_file_manager(str(tarball)))
    lay.addLayout(_button_row(src_btn))
    return grp
