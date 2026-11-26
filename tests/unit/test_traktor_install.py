"""Tests for Traktor installation discovery and selection.

Several subsystems used to derive "which Traktor" independently, each taking
the first match from its own candidate list — the launcher, the running-process
check, the CSI folder to patch, and the collection.nml to read. On a machine
with more than one Traktor they could disagree, and that matters most for the
thing about to patch files inside the install.

The library folder is the subtle one: Traktor keeps one per version and
upgrades leave the old ones behind, so "most recently modified" can select a
library Traktor no longer opens.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.traktor_install import (
    TraktorInstall,
    choose,
    discover,
    major_of,
    resolve_nml,
    version_of,
)


def _install(major: str, version: str | None, app: str) -> TraktorInstall:
    return TraktorInstall(app_path=Path(app), major=major, version=version)


# ── Labels and ordering ─────────────────────────────────────────────────────


def test_label_includes_version_when_known() -> None:
    assert _install("4", "4.5.1", "/a").label == "Traktor Pro 4 (4.5.1)"


def test_label_without_version() -> None:
    assert _install("3", None, "/a").label == "Traktor Pro 3"


def test_discovery_sorts_newest_first(tmp_path) -> None:
    apps = []
    for name in ("Traktor Pro 3.app", "Traktor Pro 4.app"):
        p = tmp_path / name
        p.mkdir()
        apps.append(p)
    found = discover(candidates=apps, nml_search_root=tmp_path / "nope")
    assert [i.major for i in found] == ["4", "3"]


def test_discovery_skips_missing_and_duplicate_paths(tmp_path) -> None:
    real = tmp_path / "Traktor Pro 4.app"
    real.mkdir()
    ghost = tmp_path / "Traktor Pro 9.app"
    found = discover(candidates=[real, real, ghost], nml_search_root=tmp_path / "nope")
    assert len(found) == 1
    assert found[0].app_path == real


# ── Version parsing ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/Applications/Traktor Pro 4.app", "4"),
        ("/Applications/Native Instruments/Traktor Pro 3/Traktor Pro 3.app", "3"),
        ("/Applications/Something Else.app", ""),
    ],
)
def test_major_of(path: str, expected: str) -> None:
    assert major_of(Path(path)) == expected


def test_version_of_strips_build_suffix(tmp_path) -> None:
    # Traktor's Info.plist carries e.g. "4.5.1 21" — the build number is noise.
    app = tmp_path / "Traktor Pro 4.app"
    (app / "Contents").mkdir(parents=True)
    (app / "Contents" / "Info.plist").write_bytes(
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b'<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        b'"http://www.apple.com/DTDs/PropertyList-1.0.dtd">'
        b'<plist version="1.0"><dict>'
        b"<key>CFBundleShortVersionString</key><string>4.5.1 21</string>"
        b"</dict></plist>"
    )
    assert version_of(app) == "4.5.1"


def test_version_of_missing_plist_is_none(tmp_path) -> None:
    assert version_of(tmp_path / "nothing.app") is None


# ── collection.nml resolution ───────────────────────────────────────────────


@pytest.fixture
def nml_root(tmp_path):
    """Three per-version libraries, oldest version touched most recently."""
    root = tmp_path / "Native Instruments"
    for ver in ("4.4.2", "4.5.0", "4.5.1", "3.11.0"):
        d = root / f"Traktor {ver}"
        d.mkdir(parents=True)
        (d / "collection.nml").write_text("<NML/>")
    # Make the *stale* library the most recently modified one.
    stale = root / "Traktor 4.4.2" / "collection.nml"
    import os

    os.utime(stale, (10**9, 10**9 + 10**6))
    return root


def test_exact_version_wins(nml_root) -> None:
    got = resolve_nml("4.5.0", "4", nml_root)
    assert got == nml_root / "Traktor 4.5.0" / "collection.nml"


def test_falls_back_to_newest_of_the_same_major(nml_root) -> None:
    # 4.9.9 isn't installed; the newest 4.x library is the right guess, and
    # notably NOT the 3.x one nor the most-recently-touched 4.4.2.
    got = resolve_nml("4.9.9", "4", nml_root)
    assert got == nml_root / "Traktor 4.5.1" / "collection.nml"


def test_falls_back_to_newest_overall_without_a_major(nml_root) -> None:
    got = resolve_nml(None, "", nml_root)
    assert got == nml_root / "Traktor 4.5.1" / "collection.nml"


def test_major_3_picks_the_3x_library(nml_root) -> None:
    got = resolve_nml(None, "3", nml_root)
    assert got == nml_root / "Traktor 3.11.0" / "collection.nml"


def test_missing_root_is_none(tmp_path) -> None:
    assert resolve_nml("4.5.1", "4", tmp_path / "absent") is None


def test_folder_without_a_collection_is_ignored(tmp_path) -> None:
    root = tmp_path / "NI"
    (root / "Traktor 4.5.1").mkdir(parents=True)  # no collection.nml inside
    good = root / "Traktor 4.4.0"
    good.mkdir()
    (good / "collection.nml").write_text("<NML/>")
    assert resolve_nml(None, "4", root) == good / "collection.nml"


# ── Selection ───────────────────────────────────────────────────────────────

INSTALLS = (
    _install("4", "4.5.1", "/Applications/Traktor Pro 4.app"),
    _install("3", "3.11.0", "/Applications/Traktor Pro 3.app"),
)


def test_no_installs_selects_nothing() -> None:
    assert choose(()) is None


def test_defaults_to_newest() -> None:
    assert choose(INSTALLS).version == "4.5.1"


def test_stored_choice_wins_over_newest() -> None:
    got = choose(INSTALLS, stored_path="/Applications/Traktor Pro 3.app")
    assert got.version == "3.11.0"


def test_stale_stored_choice_is_ignored() -> None:
    got = choose(INSTALLS, stored_path="/Applications/Traktor Pro 2.app")
    assert got.version == "4.5.1", "an uninstalled choice must not win"


def test_running_install_wins_when_nothing_is_stored() -> None:
    got = choose(INSTALLS, running_path=Path("/Applications/Traktor Pro 3.app"))
    assert got.version == "3.11.0"


def test_stored_choice_beats_the_running_one() -> None:
    got = choose(
        INSTALLS,
        stored_path="/Applications/Traktor Pro 4.app",
        running_path=Path("/Applications/Traktor Pro 3.app"),
    )
    assert got.version == "4.5.1", "an explicit choice is not overridden"
