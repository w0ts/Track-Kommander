"""The collection has one source: the selected Traktor install.

Settings used to carry a free-form "Collection:" path next to the version
picker, so the library could be made to disagree with the Traktor everything
else launched, watched and patched. The picker now decides it alone, and these
tests pin that: no second place to set it, and no stored override that could
quietly win over the picker.
"""

from __future__ import annotations

import os
import types
from pathlib import Path

import pytest

pytest.importorskip("PySide6.QtWidgets")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QSettings
from PySide6.QtWidgets import QApplication, QGroupBox, QLineEdit

import ui.library as lib_mod
from ui.settings import SettingsTab


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def store(tmp_path):
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path))
    QCoreApplication.setApplicationName("tk-collection-source-test")
    QCoreApplication.setOrganizationDomain("collection-source.invalid")
    return QSettings()


def test_library_follows_the_install_not_a_stored_path(store, monkeypatch):
    """A leftover ``nml_path`` must not override the selected install."""

    picked = Path("/picked/Traktor 4.5.1/collection.nml")
    monkeypatch.setattr(lib_mod, "find_traktor_nml", lambda: picked)
    tab = lib_mod.LibraryTab.__new__(lib_mod.LibraryTab)
    tab._settings = store
    store.setValue("nml_path", "/stale/Traktor 3.11/collection.nml")

    # Spelled as the platform spells a path: backslashes on Windows.
    assert tab._nml() == str(picked)


def test_library_reports_no_collection_when_none_resolves(store, monkeypatch):
    monkeypatch.setattr(lib_mod, "find_traktor_nml", lambda: None)
    tab = lib_mod.LibraryTab.__new__(lib_mod.LibraryTab)
    tab._settings = store

    assert tab._nml() == ""


def test_settings_has_no_collection_path_editor(qapp, store):
    tab = SettingsTab()
    try:
        assert not hasattr(tab, "nml_edit")
        traktor = next(g for g in tab.findChildren(QGroupBox) if g.title() == "TRAKTOR")
        assert not traktor.findChildren(QLineEdit)
    finally:
        tab.deleteLater()


def test_restore_drops_a_stale_collection_override(qapp, store):
    """Upgrading from the version that had the field must not leave it armed."""

    store.setValue("nml_path", "/stale/Traktor 3.11/collection.nml")
    store.sync()
    tab = SettingsTab()
    try:
        assert QSettings().value("nml_path", "", type=str) == ""
    finally:
        tab.deleteLater()


def test_settings_path_comes_from_the_selected_install(qapp, store, tmp_path):
    """``Traktor Settings.tsi`` sits beside the selected install's library."""

    tab = SettingsTab()
    try:
        lib = tmp_path / "Traktor 4.5.1"
        lib.mkdir()
        tsi = lib / "Traktor Settings.tsi"
        tsi.write_text("")
        tab._current_install = lambda: types.SimpleNamespace(nml_path=lib / "collection.nml")

        assert tab._traktor_settings_path() == tsi
    finally:
        tab.deleteLater()
