"""Checking the public repo for a newer release.

Testers were running old builds without knowing a fix had shipped. The check
asks GitHub for the public repo's latest release once at startup and says so
when it is newer than the running build — and stays quiet when it cannot tell.
"""

from __future__ import annotations

import logging

import pytest

from core import update_check as uc


@pytest.mark.parametrize(
    ("latest", "current", "newer"),
    [
        ("v0.0.7", "0.0.6", True),
        ("v0.1.0", "0.0.9", True),
        ("v0.0.10", "0.0.9", True),  # numeric, not string, comparison
        ("v0.0.6", "0.0.6", False),
        ("v0.0.5", "0.0.6", False),
        ("v1.0", "1.0.0", False),  # missing parts count as zero
    ],
)
def test_versions_compare_numerically(latest, current, newer) -> None:
    assert uc.is_newer(latest, current) is newer


@pytest.mark.parametrize("current", ["unknown", "", "dev"])
def test_an_unknown_running_version_is_never_older(current) -> None:
    """A build that cannot say its version must not nag on every launch."""
    assert uc.is_newer("v9.9.9", current) is False


_PAGES = f"https://github.com/{uc.PUBLIC_REPO}/releases"


def _release(tag: str, url: str | None = None) -> dict:
    return {"tag_name": tag, "html_url": f"{_PAGES}/tag/{tag}" if url is None else url}


def test_a_newer_release_is_reported_with_its_page() -> None:
    found = uc.check_for_update("0.0.6", fetch=lambda: _release("v0.0.7"))
    assert found == uc.Release(version="0.0.7", url=f"{_PAGES}/tag/v0.0.7")


# The page is what the Download button opens. It comes out of a network
# answer, so it is only used if it is where the releases actually are;
# anything else and the button opens the releases page we know.


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example/releases/tag/v0.0.7",
        "http://github.com/w0ts/Track-Kommander/releases/tag/v0.0.7",  # not https
        "https://github.com/someone-else/Track-Kommander/releases/tag/v0.0.7",
        "https://github.com.evil.example/w0ts/Track-Kommander/releases",
        "https://github.com@evil.example/w0ts/Track-Kommander/releases",
        "https://github.com/w0ts/Track-Kommander-evil/releases/tag/v0.0.7",
        "file:///Applications/Something.app",
        "javascript:alert(1)",
        "",
        "not a url",
    ],
)
def test_a_page_that_is_not_ours_is_not_opened(url: str) -> None:
    found = uc.check_for_update("0.0.6", fetch=lambda: _release("v0.0.7", url))
    assert found == uc.Release(version="0.0.7", url=uc.RELEASES_PAGE)


def test_the_page_we_know_is_the_public_releases() -> None:
    assert uc.RELEASES_PAGE == _PAGES


def test_the_same_release_is_not_reported() -> None:
    assert uc.check_for_update("0.0.7", fetch=lambda: _release("v0.0.7")) is None


def test_a_failed_request_is_silent(caplog) -> None:
    caplog.set_level(logging.INFO, logger="core.update_check")

    def _boom() -> dict:
        raise OSError("offline")

    assert uc.check_for_update("0.0.6", fetch=_boom) is None
    assert "offline" in caplog.text


def test_a_malformed_answer_is_silent() -> None:
    assert uc.check_for_update("0.0.6", fetch=lambda: {"message": "Not Found"}) is None


def test_it_asks_the_public_repo_for_its_latest_release() -> None:
    assert uc.LATEST_RELEASE_API == (
        "https://api.github.com/repos/w0ts/Track-Kommander/releases/latest"
    )


def test_the_check_is_on_by_default() -> None:
    assert uc.SETTING_DEFAULT is True


# The installer: offered for installing in place only when it comes from the
# public repo's downloads and GitHub's digest can check it.

_SHA = "ab" * 32
_DL = f"{uc.DOWNLOADS}v0.0.9/"


def _asset(name: str, url: str | None = None, digest: str | None = f"sha256:{_SHA}") -> dict:
    return {"name": name, "browser_download_url": url or _DL + name, "digest": digest}


_ASSETS = [
    _asset("Track.Kommander.dmg"),
    _asset("Track.Kommander.dmg.sha256"),
    _asset("TrackKommanderSetup-2026-10-06_09-30-29.msi"),
    _asset("TrackKommanderSetup-2026-10-06_09-30-29.msi.sha256"),
]


@pytest.mark.parametrize(
    ("platform", "name"),
    [
        ("darwin", "Track.Kommander.dmg"),
        ("win32", "TrackKommanderSetup-2026-10-06_09-30-29.msi"),
    ],
)
def test_the_platforms_installer_is_picked(platform: str, name: str) -> None:
    assert uc.pick_installer(_ASSETS, platform) == uc.Asset(name, _DL + name, _SHA)


def test_no_installer_on_linux() -> None:
    assert uc.pick_installer(_ASSETS, "linux") is None


@pytest.mark.parametrize(
    "asset",
    [
        _asset("Track.Kommander.dmg", url="https://evil.example/Track.Kommander.dmg"),
        _asset("Track.Kommander.dmg", digest=None),
        _asset("Track.Kommander.dmg", digest="md5:abc"),
        _asset("Track.Kommander.dmg", digest="sha256:short"),
    ],
)
def test_an_installer_that_cannot_be_trusted_is_not_offered(asset: dict) -> None:
    assert uc.pick_installer([asset], "darwin") is None


@pytest.mark.parametrize("assets", [None, "x", [None, 3]])
def test_a_malformed_asset_list_offers_nothing(assets) -> None:
    assert uc.pick_installer(assets, "darwin") is None


def test_the_release_carries_its_installer(monkeypatch) -> None:
    monkeypatch.setattr(uc.sys, "platform", "darwin")
    data = {**_release("v0.0.9"), "assets": _ASSETS}
    found = uc.check_for_update("0.0.8", fetch=lambda: data)
    assert found is not None
    assert found.installer == uc.Asset("Track.Kommander.dmg", _DL + "Track.Kommander.dmg", _SHA)
