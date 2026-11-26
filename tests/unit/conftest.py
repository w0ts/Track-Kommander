"""Shared by every test in this directory.

Two things no test should have to remember:

* Qt draws nowhere. Set when this file is loaded, which is before any test
  module — and so before anything imports PySide6.
* Settings go to a folder of the test's own. ``QSettings`` is keyed by the
  application's name, which is process-wide — a test that did not set it
  inherited the store of whichever test ran before, or, run first, read and
  wrote the developer's real preferences.
"""

from __future__ import annotations

import importlib
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# On Windows the offscreen platform looks for fonts only in PySide6's own lib/fonts, which is
# empty (Qt ships none): every glyph then draws faint. TK inside Traktor uses the real platform
# and the system's fonts; the tests get those too (measured on WOTS, 2026-11-26).
if os.name == "nt":
    os.environ.setdefault(
        "QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
    )


@pytest.fixture(autouse=True)
def _own_settings(tmp_path_factory, request) -> None:
    """Point ``QSettings`` at a folder that belongs to this test alone.

    A test that sets its own path afterwards simply overrides this.
    """
    # Looked up here, not imported above: the platform has to be chosen first.
    qt = importlib.import_module("PySide6.QtCore")
    store = tmp_path_factory.mktemp("settings")
    qt.QSettings.setDefaultFormat(qt.QSettings.Format.IniFormat)
    qt.QSettings.setPath(qt.QSettings.Format.IniFormat, qt.QSettings.Scope.UserScope, str(store))
    qt.QCoreApplication.setOrganizationName("tk-tests")
    qt.QCoreApplication.setOrganizationDomain("tests.invalid")
    qt.QCoreApplication.setApplicationName(f"tk-test-{request.node.name}"[:80])


@pytest.fixture(autouse=True)
def _own_secrets(tmp_path_factory, monkeypatch) -> None:
    """Passwords and API keys (core.secrets) in a folder of the test's own:
    a test must never read or change the user's real ones."""
    secrets = importlib.import_module("core.secrets")
    monkeypatch.setattr(secrets, "FOLDER", tmp_path_factory.mktemp("secrets"))


@pytest.fixture(autouse=True)
def _own_spotify_key(tmp_path_factory, monkeypatch) -> None:
    """The Spotify app's client id (core.spotify_key) in a folder of the test's own."""
    key = importlib.import_module("core.spotify_key")
    folder = tmp_path_factory.mktemp("spotify-key")
    real_path = key.path
    monkeypatch.setattr(key, "path", lambda f=None: real_path(folder if f is None else f))


@pytest.fixture(autouse=True)
def _fresh_extensions():
    """Each test starts with no add-on registered (MainWindow loads it in the tests that build one)."""
    extensions = importlib.import_module("core.extensions")
    extensions.clear()
    yield
    extensions.clear()


@pytest.fixture(autouse=True)
def _no_installed_addon(monkeypatch) -> None:
    """Never an add-on named in this shell (core.addon_finder): only one a test sets up."""
    finder = importlib.import_module("core.addon_finder")
    monkeypatch.delenv(finder.DEV_ENV, raising=False)
