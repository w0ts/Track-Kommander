"""Settings ▸ About renders every component and can reach every licence file."""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6.QtWidgets")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QPushButton, QTableWidget

from core import licensing
from ui import about_page
from ui.about_page import PUBLIC_REPO_URL, about_groups


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture(scope="module")
def groups(qapp):
    return about_groups()


def test_the_table_lists_every_component(groups) -> None:
    tables = [t for g in groups for t in g.findChildren(QTableWidget)]
    assert len(tables) == 1
    table = tables[0]
    listed = [table.item(r, 0).text() for r in range(table.rowCount())]
    assert listed == [c.name for c in licensing.components()]
    assert "av" in listed and "mutagen" not in listed


def test_running_from_source_every_button_has_a_file_to_open(groups) -> None:
    """From a checkout, each file resolves (root LICENSE, vendor/source …),
    so a disabled button here means a path the build would also miss."""
    buttons = {b.text(): b.isEnabled() for g in groups for b in g.findChildren(QPushButton)}
    assert buttons == {
        "Check for updates": True,
        "Documentation": True,
        "View licence": True,
        "Third-party audit": True,
        "Full licence texts": True,
        "Show licence files": True,
        "Show FFmpeg source": True,
    }


def test_the_public_repo_comes_first(groups) -> None:
    """Testers look here for where to get a new build and the docs."""
    assert groups[0].title() == "UPDATES AND DOCUMENTATION"
    assert PUBLIC_REPO_URL == "https://github.com/w0ts/Track-Kommander"


def test_each_link_opens_its_page_on_the_public_repo(groups, monkeypatch) -> None:
    opened: list[str] = []
    monkeypatch.setattr(
        about_page.QDesktopServices, "openUrl", lambda url: opened.append(url.toString())
    )
    for b in groups[0].findChildren(QPushButton):
        if b.text() == "Documentation":
            b.click()
    assert opened == [f"{PUBLIC_REPO_URL}#readme"]


# Check for updates: says so when this is the latest, offers the update when
# it is not — instead of a Download button that always showed.


class _SyncThread:
    """Runs the check on the calling thread, so its answer arrives at once."""

    def __init__(self, target, **_kw) -> None:
        self._target = target

    def start(self) -> None:
        self._target()


def _check(qapp, monkeypatch, fetch):
    from PySide6.QtWidgets import QLabel

    from core import update_check

    monkeypatch.setattr(about_page.threading, "Thread", _SyncThread)
    monkeypatch.setattr(about_page, "app_version", lambda: "0.0.8")
    monkeypatch.setattr(update_check, "fetch_latest_release", fetch)
    status = QLabel()
    btn = about_page._update_button(status)
    btn.click()
    return btn, status


def _tag(tag: str):
    url = f"{PUBLIC_REPO_URL}/releases/tag/{tag}"
    return lambda: {"tag_name": tag, "html_url": url}


def test_on_the_latest_version_it_says_so(qapp, monkeypatch) -> None:
    btn, status = _check(qapp, monkeypatch, _tag("v0.0.8"))
    assert btn.text() == "Up to date" and not btn.isEnabled()
    assert "0.0.8 is the latest" in status.text()


def test_without_an_answer_it_can_be_tried_again(qapp, monkeypatch) -> None:
    def offline():
        raise OSError("offline")

    btn, status = _check(qapp, monkeypatch, offline)
    assert btn.isEnabled() and "Could not reach" in status.text()


def test_a_newer_version_without_a_main_window_opens_its_page(qapp, monkeypatch) -> None:
    opened: list[str] = []
    monkeypatch.setattr(
        about_page.QDesktopServices, "openUrl", lambda url: opened.append(url.toString())
    )
    _btn, status = _check(qapp, monkeypatch, _tag("v0.0.9"))
    assert "0.0.9 is out" in status.text()
    assert opened == [f"{PUBLIC_REPO_URL}/releases/tag/v0.0.9"]


# On a real thread: 0.0.8 crashed (SIGSEGV) seconds after Check for updates,
# 2026-10-10. The answer's relay was kept alive by the thread alone, so the
# thread freed it while the GUI thread delivered its answer, through TK's
# app-wide event filter. Run apart: a crash would take pytest down with it.
_REAL_THREADS = """
import sys, time
sys.path.insert(0, {src!r})
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QLabel, QMainWindow
from core import update_check
from ui import about_page

class Window(QMainWindow):
    def eventFilter(self, obj, event):
        return False

app = QApplication([])
window = Window()
app.installEventFilter(window)
about_page.app_version = lambda: "0.0.8"
def latest():
    time.sleep(0.02)
    return {{"tag_name": "v0.0.8", "html_url": "https://example.invalid"}}
update_check.fetch_latest_release = latest
buttons = []
def click():
    status = QLabel(window)
    btn = about_page._update_button(status)
    btn.setParent(window)
    buttons.append(btn)
    btn.click()
for i in range(150):
    QTimer.singleShot(i * 10, click)
QTimer.singleShot(150 * 10 + 1500, app.quit)
app.exec()
print(sum(b.text() == "Up to date" for b in buttons))
"""


def test_checking_on_a_real_thread_does_not_crash() -> None:
    import subprocess
    import sys
    from pathlib import Path

    src = Path(about_page.__file__).resolve().parents[1]
    done = subprocess.run(
        [sys.executable, "-c", _REAL_THREADS.format(src=str(src))],
        capture_output=True,
        text=True,
        timeout=60,
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
    )
    assert done.returncode == 0, done.stderr[-2000:]
    assert done.stdout.strip() == "150"
