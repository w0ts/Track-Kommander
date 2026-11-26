"""Discovery and selection of the Traktor installation to work with.

Several parts of the app independently derived their own idea of "Traktor":
the executable to launch, the process to detect as running, the ``CSI/D2``
folder to patch, and the ``collection.nml`` to read. Each walked its own
candidate list and took the *first* existing entry, so on a machine with more
than one Traktor they could disagree — and the symptoms of that (a scan that
misses tracks, a bridge patched into the wrong app) look like unrelated bugs.

This module resolves one :class:`TraktorInstall` and everything else hangs off
it.

The ``collection.nml`` choice matters more than it looks. Traktor keeps a
per-version library folder::

    ~/Documents/Native Instruments/Traktor 4.4.2/collection.nml
    ~/Documents/Native Instruments/Traktor 4.5.0/collection.nml
    ~/Documents/Native Instruments/Traktor 4.5.1/collection.nml

Old folders are left behind by upgrades, so picking "the most recently
modified" (what the original ``find_traktor_nml`` did) can land on a stale
library — the app would then analyse a collection Traktor isn't using. Matching
the running app's version is exact; mtime is only the fallback.
"""

from __future__ import annotations

import logging
import os
import plistlib
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from core.ni_paths import ni_documents_root

log = logging.getLogger(__name__)

# QSettings key holding the user's explicit choice (an app path).
SETTINGS_KEY = "traktor_install_path"

# Major versions we know about, newest first.
_KNOWN_MAJORS = ("4", "3")


@dataclass(frozen=True)
class TraktorInstall:
    """One installed Traktor, with every path the app needs derived from it."""

    app_path: Path
    """The ``.app`` bundle (macOS) or ``.exe`` (Windows)."""

    major: str
    """Major version as a string — "4", "3"."""

    version: str | None = None
    """Full version ("4.5.1") when it could be read, else None."""

    csi_dir: Path | None = None
    """Traktor's ``qml/CSI`` directory — where the D2 bridge is installed."""

    nml_path: Path | None = None
    """This install's ``collection.nml``, if one was found."""

    @property
    def label(self) -> str:
        """Human-readable name for the Settings picker."""
        name = f"Traktor Pro {self.major}"
        if self.version:
            name += f" ({self.version})"
        return name

    @property
    def sort_key(self) -> tuple:
        """Newest first. Unknown versions sort below known ones."""
        return (_version_tuple(self.version), int(self.major or 0))


def _version_tuple(version: str | None) -> tuple[int, ...]:
    if not version:
        return ()
    return tuple(int(p) for p in re.findall(r"\d+", version)[:4])


# ── Filesystem probing ──────────────────────────────────────────────────────


def _mac_app_candidates() -> list[Path]:
    roots = [Path("/Applications/Native Instruments"), Path("/Applications")]
    found: list[Path] = []
    for root in roots:
        if not root.is_dir():
            continue
        try:
            entries = sorted(root.iterdir())
        except OSError:
            continue
        for entry in entries:
            # "/Applications/Traktor Pro 4.app" or
            # "/Applications/Native Instruments/Traktor Pro 4/Traktor Pro 4.app"
            if entry.suffix == ".app" and entry.stem.startswith("Traktor Pro"):
                found.append(entry)
            elif entry.is_dir() and entry.name.startswith("Traktor Pro"):
                nested = entry / f"{entry.name}.app"
                if nested.exists():
                    found.append(nested)
    return found


def _win_app_candidates() -> list[Path]:
    roots: list[Path] = []
    for env in ("ProgramW6432", "ProgramFiles"):
        v = os.environ.get(env)
        if v and Path(v) not in roots:
            roots.append(Path(v))
    if not roots:
        roots.append(Path(r"C:\Program Files"))
    found: list[Path] = []
    for root in roots:
        ni = root / "Native Instruments"
        if not ni.is_dir():
            continue
        try:
            entries = sorted(ni.iterdir())
        except OSError:
            continue
        for entry in entries:
            if entry.is_dir() and entry.name.startswith("Traktor Pro"):
                found.extend(sorted(entry.glob("Traktor*.exe")))
    return found


def app_candidates() -> list[Path]:
    """Every plausible Traktor executable/bundle on this machine."""
    if sys.platform == "darwin":
        return _mac_app_candidates()
    if sys.platform == "win32":
        return _win_app_candidates()
    return []


def major_of(app_path: Path) -> str:
    """Major version parsed from the install's name ("Traktor Pro 4" → "4")."""
    for part in (app_path.stem, *(p.name for p in app_path.parents)):
        m = re.search(r"Traktor Pro (\d+)", part)
        if m:
            return m.group(1)
    return ""


def version_of(app_path: Path) -> str | None:
    """Full version string of an installed Traktor, or None if unreadable.

    macOS reads ``CFBundleShortVersionString`` from the bundle's Info.plist
    (which can carry a build suffix — "4.5.1 21" — so only the leading
    dotted-numeric part is kept). Windows has no equally cheap equivalent, so
    it falls back to the major version from the folder name.
    """
    plist = app_path / "Contents" / "Info.plist"
    if not plist.is_file():
        return None
    try:
        with plist.open("rb") as fh:
            data = plistlib.load(fh)
    except (OSError, ValueError):
        log.debug("could not read %s", plist, exc_info=True)
        return None
    raw = str(data.get("CFBundleShortVersionString") or "").strip()
    m = re.match(r"\d+(?:\.\d+)*", raw)
    return m.group(0) if m else (raw or None)


def csi_dir_for(app_path: Path) -> Path | None:
    """Traktor's ``qml/CSI`` directory for an install, if it exists."""
    if sys.platform == "darwin":
        candidate = app_path / "Contents" / "Resources" / "qml" / "CSI"
    else:
        # ...\Traktor Pro N\Traktor.exe → ...\Traktor Pro N\Resources64\qml\CSI
        candidate = app_path.parent / "Resources64" / "qml" / "CSI"
    return candidate if candidate.is_dir() else None


def nml_root() -> Path:
    """The folder holding Traktor's per-version library directories."""
    return ni_documents_root()


def resolve_nml(version: str | None, major: str, root: Path | None = None) -> Path | None:
    """Pick the ``collection.nml`` belonging to a given Traktor version.

    Preference order, most specific first:

    1. exact version folder — ``Traktor 4.5.1``
    2. newest folder sharing the major version — ``Traktor 4.*``
    3. newest folder of any version

    Upgrades leave the old folders in place, so "most recently modified" alone
    can point at a library Traktor no longer opens.
    """
    root = root if root is not None else nml_root()
    if not root.is_dir():
        return None

    versioned: list[tuple[tuple[int, ...], Path]] = []
    try:
        entries = sorted(root.iterdir())
    except OSError:
        return None
    for entry in entries:
        m = re.fullmatch(r"Traktor\s+(\d[\d.]*)", entry.name)
        nml = entry / "collection.nml"
        if not m or not nml.is_file():
            continue
        parsed = _version_tuple(m.group(1))
        if version and m.group(1) == version:
            return nml
        versioned.append((parsed, nml))

    if not versioned:
        return None
    if major:
        same_major = [v for v in versioned if v[0] and str(v[0][0]) == major]
        if same_major:
            return max(same_major)[1]
    return max(versioned)[1]


# ── Discovery ───────────────────────────────────────────────────────────────


def discover(
    candidates: list[Path] | None = None, nml_search_root: Path | None = None
) -> tuple[TraktorInstall, ...]:
    """Every Traktor installation found on this machine, newest first.

    Unlike the old first-match helpers this returns *all* of them, so the user
    can be asked which one to use when it matters.
    """
    paths = candidates if candidates is not None else app_candidates()
    installs: list[TraktorInstall] = []
    seen: set[Path] = set()
    for path in paths:
        if path in seen or not path.exists():
            continue
        seen.add(path)
        version = version_of(path)
        major = major_of(path) or (
            str(_version_tuple(version)[0]) if _version_tuple(version) else ""
        )
        installs.append(
            TraktorInstall(
                app_path=path,
                major=major,
                version=version,
                csi_dir=csi_dir_for(path),
                nml_path=resolve_nml(version, major, nml_search_root),
            )
        )
    return tuple(sorted(installs, key=lambda i: i.sort_key, reverse=True))


def choose(
    installs: tuple[TraktorInstall, ...] | list[TraktorInstall],
    *,
    stored_path: str | None = None,
    running_path: Path | None = None,
) -> TraktorInstall | None:
    """Decide which install to use. Pure — no filesystem access.

    1. the user's stored choice, when it is still present;
    2. otherwise the one that is currently running;
    3. otherwise the newest.

    A stored path that no longer exists is ignored rather than honoured, so an
    uninstalled Traktor can't leave the app pointing at nothing.
    """
    if not installs:
        return None
    if stored_path:
        wanted = Path(stored_path)
        for inst in installs:
            if inst.app_path == wanted:
                return inst
        log.info("stored Traktor install %s is gone — re-selecting", stored_path)
    if running_path is not None:
        for inst in installs:
            if inst.app_path == running_path:
                return inst
    return installs[0]


def selected(nml_search_root: Path | None = None) -> TraktorInstall | None:
    """The install to work with, honouring the user's stored choice."""
    from PySide6.QtCore import QSettings

    stored = QSettings().value(SETTINGS_KEY, "", type=str)
    return choose(
        discover(nml_search_root=nml_search_root),
        stored_path=stored if isinstance(stored, str) else None,
    )


def select(install: TraktorInstall) -> None:
    """Persist *install* as the user's choice."""
    from PySide6.QtCore import QSettings

    s = QSettings()
    s.setValue(SETTINGS_KEY, str(install.app_path))
    s.sync()
    log.info("Traktor install selected: %s", install.app_path)
