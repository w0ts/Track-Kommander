"""What Settings requires before it touches Traktor's bundle.

Two wrong turns are recorded here, because both cost real time.

Install used to offer to quit Traktor and start it back up — a lot of
machinery around a mapping that is only read at startup, so "restart Traktor"
afterwards says the same thing.

Then the gate demanded **App Management**, the grant macOS *names* for
modifying another app's bundle. Measured, that is not the grant that decides
it: with App Management on, the toggle set and the app restarted, the
elevated write was still refused; adding **Full Disk Access** let the same
build through. The write happens in the helper running as root, and App
Management does not reach it.

So the gate requires Full Disk Access and nothing else. App Management is
asked for only where macOS actually refuses — which is also the only place
the prompt earns itself, since the request is what creates the pane entry the
user is then told to switch on. Demanding it up front additionally made it
impossible to test whether it is needed at all: the request raises a prompt.

The method is called on a stand-in ``self``: it uses ``self`` only for the
dialogs, which the tests supply.
"""

from __future__ import annotations

import os
import sys

import pytest

pytest.importorskip("PySide6.QtWidgets")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QPushButton

from core import permissions
from ui.settings import SettingsTab


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


class Stub:
    """Enough of a SettingsTab for the gate: the two dialogs."""

    def __init__(self) -> None:
        self.explained_fda = 0

    def _explain_full_disk_access(self) -> None:
        self.explained_fda += 1


@pytest.fixture
def on_mac(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    return monkeypatch


def gate(stub: Stub) -> bool:
    return SettingsTab._ensure_can_modify_traktor(stub)


def test_off_mac_the_gate_is_open(monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(
        permissions,
        "full_disk_access_granted",
        lambda: pytest.fail("checked a macOS grant on Windows"),
    )
    stub = Stub()
    assert gate(stub) is True
    assert stub.explained_fda == 0


def test_full_disk_access_lets_the_job_run(qapp, on_mac) -> None:
    on_mac.setattr(permissions, "full_disk_access_granted", lambda: True)
    stub = Stub()
    assert gate(stub) is True
    assert stub.explained_fda == 0


def test_without_full_disk_access_the_job_stops_and_explains(qapp, on_mac) -> None:
    on_mac.setattr(permissions, "full_disk_access_granted", lambda: False)
    stub = Stub()
    assert gate(stub) is False
    assert stub.explained_fda == 1


def test_nothing_asks_for_app_management(qapp, on_mac) -> None:
    """It prompted on every attempt and never unblocked anything.

    The prompt is the whole reason it is gone: it appeared before the job
    ran, so the install could not even be tried without granting it.
    """
    on_mac.setattr(permissions, "full_disk_access_granted", lambda: True)
    assert not [n for n in dir(permissions) if "app_management" in n.lower()]

    stub = Stub()
    assert gate(stub) is True


class Buttons:
    """A SettingsTab stand-in that owns the two D2 buttons."""

    def __init__(self) -> None:
        self.d2_patch_btn = QPushButton("Install")
        self.d2_restore_btn = QPushButton("Restore")

    _d2_busy = SettingsTab._d2_busy


def test_a_running_job_holds_both_d2_buttons_down(qapp) -> None:
    """One press must mean one job.

    The elevated helper and the result dialog both turn the event loop, and
    that is when clicks made in the meantime are delivered. Qt discards a
    click on a disabled widget, so the hold is what prevents a second job
    with its own password prompt.
    """
    tab = Buttons()
    with tab._d2_busy():
        assert not tab.d2_patch_btn.isEnabled()
        assert not tab.d2_restore_btn.isEnabled()
    assert tab.d2_patch_btn.isEnabled()
    assert tab.d2_restore_btn.isEnabled()


def test_the_buttons_come_back_when_a_job_raises(qapp) -> None:
    tab = Buttons()
    with pytest.raises(RuntimeError), tab._d2_busy():
        raise RuntimeError("helper blew up")
    assert tab.d2_patch_btn.isEnabled()
    assert tab.d2_restore_btn.isEnabled()
