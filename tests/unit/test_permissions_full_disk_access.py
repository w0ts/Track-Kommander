"""Full Disk Access: the grant that lets the D2 install write into Traktor.

macOS *names* App Management as the permission for modifying another app's
bundle, and that turned out to be the wrong thing to ask for. The write is
done by the helper running as root, which App Management does not reach: with
it granted, the toggle set and the app restarted, the root write was still
refused. Full Disk Access let the same build through. Asking for App
Management also raised a system prompt on every attempt, which is why none of
it is here any more.

There is no public API for this, so :mod:`core.permissions` reads the state
through the private TCC framework. These tests fake that layer — what matters
here is the policy on top of it, and one reading in particular:

**"couldn't tell" is granted.** Private API: a renamed symbol on some future
macOS must not lock a user out of a feature whose permission is perfectly
fine. The install still meets the real answer from the kernel.
"""

from __future__ import annotations

import pytest

from core import permissions


@pytest.fixture
def on_mac(monkeypatch):
    monkeypatch.setattr(permissions, "_IS_MAC", True)
    return monkeypatch


def preflight(monkeypatch, value):
    """Make the TCC preflight report *value* for every service."""
    monkeypatch.setattr(permissions, "_tcc_preflight", lambda name: value)


def test_granted_is_granted(on_mac) -> None:
    preflight(on_mac, permissions._TCC_GRANTED)
    assert permissions.full_disk_access_granted() is True


def test_denied_is_not_granted(on_mac) -> None:
    preflight(on_mac, 1)
    assert permissions.full_disk_access_granted() is False


def test_never_asked_is_not_granted(on_mac) -> None:
    preflight(on_mac, 2)
    assert permissions.full_disk_access_granted() is False


def test_unreadable_state_does_not_block_the_user(on_mac) -> None:
    """A broken probe must not be the thing that stops a D2 install."""
    preflight(on_mac, None)
    assert permissions.full_disk_access_granted() is True


def test_it_reads_the_all_files_service(on_mac) -> None:
    asked = []
    on_mac.setattr(
        permissions, "_tcc_preflight", lambda name: asked.append(name) or permissions._TCC_GRANTED
    )
    assert permissions.full_disk_access_granted() is True
    assert asked == [permissions._TCC_ALL_FILES]


def test_requesting_raises_no_prompt(on_mac) -> None:
    """macOS has none to raise: the user adds the app with + and relaunches.

    The request only reports where things stand, so the UI falls through to
    opening the pane.
    """
    preflight(on_mac, 2)
    assert permissions.request_full_disk_access() is False


def test_off_mac_there_is_nothing_to_grant(monkeypatch) -> None:
    monkeypatch.setattr(permissions, "_IS_MAC", False)
    monkeypatch.setattr(
        permissions, "_tcc_preflight", lambda name: pytest.fail("asked TCC off macOS")
    )
    assert permissions.full_disk_access_granted() is True
    assert permissions.request_full_disk_access() is True


def test_a_missing_framework_is_survivable(monkeypatch) -> None:
    """The TCC entry points are private; none of them may raise."""
    monkeypatch.setattr(permissions, "_IS_MAC", True)
    monkeypatch.setitem(permissions._tcc_keepalive, "lib", None)
    assert permissions._tcc_preflight(permissions._TCC_ALL_FILES) is None
    assert permissions.full_disk_access_granted() is True


def test_app_management_is_gone(monkeypatch) -> None:
    """It was never the grant that worked, and asking for it prompted.

    Named explicitly so a future "let's also check App Management" has to
    argue with a test rather than quietly reintroduce the prompt.
    """
    leftovers = [name for name in dir(permissions) if "app_management" in name.lower()]
    assert leftovers == []
