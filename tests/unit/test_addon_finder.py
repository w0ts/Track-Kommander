"""The add-on TK runs with: only the folder named in TK_ADDON_DIR, never searched for (User,
2026-11-26: the Plus edition is a full version, its add-on inside the same install), and only one
of TK's own version (User, 2026-11-25)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core import addon_finder


def _addon(root: Path, version: str = "0.1.0", name: str = "Extra") -> Path:
    folder = root / addon_finder.FOLDER
    folder.mkdir(parents=True)
    manifest = {"name": name, "version": version, "page": "https://example.invalid/extra"}
    (folder / addon_finder.MANIFEST).write_text(json.dumps(manifest))
    return folder


@pytest.fixture
def named(tmp_path, monkeypatch):
    """Names an add-on folder the way libtk does."""

    def name(version: str = "0.1.0") -> Path:
        folder = _addon(tmp_path, version)
        monkeypatch.setenv(addon_finder.DEV_ENV, str(folder))
        return folder

    return name


def test_the_named_folder_is_the_add_on(named) -> None:
    folder = named()
    found = addon_finder.find()
    assert found is not None
    assert (found.folder, found.name, found.version) == (folder, "Extra", "0.1.0")


def test_nothing_named_is_none() -> None:
    assert addon_finder.find() is None


def test_a_named_folder_that_is_not_there_is_none(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(addon_finder.DEV_ENV, str(tmp_path / "gone"))
    assert addon_finder.find() is None


def test_a_folder_without_a_readable_manifest_is_none(named) -> None:
    folder = named()
    (folder / addon_finder.MANIFEST).write_text("not json")
    assert addon_finder.find() is None


@pytest.mark.parametrize(
    ("addon", "tk", "ok"),
    [("0.1.0", "0.1.0", True), ("0.1.0", "0.1.1", False), ("dev", "0.1.1", True)],
)
def test_only_the_same_version_loads(named, addon, tk, ok) -> None:
    named(addon)
    found = addon_finder.find()
    assert found is not None and addon_finder.matches(found, tk) is ok


def test_a_mismatch_says_both_versions_and_where_to_get_the_right_one(named) -> None:
    named("0.1.0")
    found = addon_finder.find()
    assert found is not None
    notice = addon_finder.mismatch_notice(found, "0.2.0")
    assert "Extra is 0.1.0" in notice.text and "Track Kommander is 0.2.0" in notice.text
    assert notice.action == addon_finder.PAGE_ACTION


@pytest.fixture
def fresh_module():
    """The stand-in tk_addon this test imports goes again after it: later tests get the real one."""
    import sys

    before = sys.modules.pop("tk_addon", None)
    yield
    sys.modules.pop("tk_addon", None)
    if before is not None:
        sys.modules["tk_addon"] = before


def _with_module(folder: Path) -> Path:
    (folder / "tk_addon.py").write_text(
        "from core import extensions\n"
        "def register():\n"
        "    extensions.add_artist_source(lambda: ['From The Folder'])\n"
    )
    return folder


def test_tk_loads_the_named_add_on_of_its_own_version(named, monkeypatch, fresh_module) -> None:
    import sys

    from core import extensions

    monkeypatch.setattr("core.log_bundle.app_version", lambda: "0.1.0")
    folder = _with_module(named("0.1.0"))
    monkeypatch.setattr(sys, "path", list(sys.path))
    assert extensions.load_addons()
    assert str(folder) == sys.path[0]
    assert [list(source()) for source in extensions.artist_sources()] == [["From The Folder"]]
    assert extensions.mismatch_notices("0.1.0") == ()


def test_another_version_is_not_loaded_and_the_banner_says_so(
    named, monkeypatch, fresh_module
) -> None:
    from core import extensions

    monkeypatch.setattr("core.log_bundle.app_version", lambda: "0.2.0")
    _with_module(named("0.1.0"))
    assert not extensions.load_addons()
    assert extensions.artist_sources() == ()
    (notice,) = extensions.mismatch_notices("0.2.0")
    assert "0.1.0" in notice.text and extensions.mismatched_page()
