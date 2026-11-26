"""Is a newer Track Kommander out? Asks the public repo's latest release.

Qt-free and synchronous: the caller runs :func:`check_for_update` off the UI
thread. GitHub's ``/releases/latest`` already skips drafts and pre-releases,
so a release candidate never prompts anyone. Any failure — offline, rate
limited, a changed answer — is logged and reads as "no update": a check that
cannot tell must stay quiet rather than nag.
"""

from __future__ import annotations

import json
import logging
import re
import ssl
import sys
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass

log = logging.getLogger(__name__)

PUBLIC_REPO = "w0ts/Track-Kommander"
LATEST_RELEASE_API = f"https://api.github.com/repos/{PUBLIC_REPO}/releases/latest"

#: Where the releases are, for a person. Known in advance, so it can be
#: trusted where an address out of a network answer cannot.
RELEASES_PAGE = f"https://github.com/{PUBLIC_REPO}/releases"

#: QSettings key, and its default: on, so testers learn a fix has shipped.
SETTING_KEY = "check_for_updates"
SETTING_DEFAULT = True

_TIMEOUT_S = 10
_VERSION_RE = re.compile(r"^v?(\d+(?:\.\d+)*)")


#: Where a release's files are downloaded from. An installer whose address
#: is anywhere else is not offered for installing.
DOWNLOADS = f"https://github.com/{PUBLIC_REPO}/releases/download/"


@dataclass(frozen=True)
class Asset:
    """The installer attached to a release, for this platform."""

    name: str  # "Track.Kommander.dmg"
    url: str  # under DOWNLOADS
    sha256: str  # hex, from GitHub's own digest of the upload


@dataclass(frozen=True)
class Release:
    version: str  # "0.0.7" — the tag without its "v"
    url: str  # the release's page on GitHub
    installer: Asset | None = None  # None: only the page can be offered


def _parse(version: str) -> tuple[int, ...] | None:
    m = _VERSION_RE.match(version.strip())
    if not m:
        return None
    parts = [int(p) for p in m.group(1).split(".")]
    while parts and parts[-1] == 0:  # "1.0" == "1.0.0"
        parts.pop()
    return tuple(parts)


def is_newer(latest: str, current: str) -> bool:
    """True when *latest* is a later version than *current*. An unparseable
    *current* ("unknown") is never older, so it never prompts."""
    new, cur = _parse(latest), _parse(current)
    return new is not None and cur is not None and new > cur


def fetch_latest_release() -> dict:
    """GitHub's JSON for the public repo's latest release. Raises on failure."""
    try:
        import certifi

        ctx = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        ctx = ssl.create_default_context()
    req = urllib.request.Request(
        LATEST_RELEASE_API,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "TrackKommander-update-check",
        },
    )
    with urllib.request.urlopen(req, timeout=_TIMEOUT_S, context=ctx) as resp:
        return json.loads(resp.read().decode("utf-8"))


def check_for_update(
    current: str, fetch: Callable[[], dict] = fetch_latest_release
) -> Release | None:
    """The latest release when it is newer than *current*, else None."""
    try:
        data = fetch()
        tag, url = str(data["tag_name"]), str(data["html_url"])
    except Exception as exc:
        log.info("update check: no answer (%s)", exc)
        return None
    if not is_newer(tag, current):
        log.info("update check: %s is current (latest %s)", current, tag)
        return None
    log.info("update check: %s available (running %s)", tag, current)
    return Release(
        version=tag.removeprefix("v"),
        url=release_page(url),
        installer=pick_installer(data.get("assets"), sys.platform),
    )


def _wanted(name: str, platform: str) -> bool:
    """Is *name* this platform's installer of Track Kommander itself?
    The checksum files beside them and any other build are not."""
    low = name.lower()
    if platform == "darwin":
        return low.endswith(".dmg") and low.replace(" ", ".") == "track.kommander.dmg"
    if platform == "win32":
        return low.endswith(".msi") and low.startswith("trackkommandersetup")
    return False


def pick_installer(assets: object, platform: str) -> Asset | None:
    """The release's installer for *platform*, or None.

    None too when the answer is not what is expected: no digest to check the
    download against, or an address outside the public repo's downloads —
    the update then falls back to opening the release page.
    """
    if not isinstance(assets, list):
        return None
    for a in assets:
        if not isinstance(a, dict) or not _wanted(str(a.get("name", "")), platform):
            continue
        url, digest = str(a.get("browser_download_url", "")), str(a.get("digest") or "")
        algo, _, hexdigest = digest.partition(":")
        if not url.startswith(DOWNLOADS) or algo != "sha256" or len(hexdigest) != 64:
            log.warning(
                "update check: installer %r not usable (%s, %r)", a.get("name"), url, digest
            )
            return None
        return Asset(name=str(a["name"]), url=url, sha256=hexdigest.lower())
    return None


def release_page(url: str) -> str:
    """*url* if it is a page of our releases, else the releases page.

    It is what the Download button hands to the system to open, and it came
    out of a network answer. Anything that is not an https page under the
    public repo's releases is replaced with the one address known in
    advance, so the button still leads somewhere useful.
    """
    parts = urllib.parse.urlsplit(url)
    ours = urllib.parse.urlsplit(RELEASES_PAGE)
    if (
        parts.scheme == "https"
        and parts.netloc == ours.netloc
        and (parts.path == ours.path or parts.path.startswith(ours.path + "/"))
    ):
        return url
    log.warning("update check: %r is not a page of our releases — using %s", url, RELEASES_PAGE)
    return RELEASES_PAGE
