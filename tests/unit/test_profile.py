"""The beta profile keeps a test copy away from the installed app's things."""

from __future__ import annotations

import pytest

from core import profile
from core.constants import APP_NAME, WEBHOOK_PORT


def test_without_the_variable_nothing_changes(monkeypatch):
    monkeypatch.delenv(profile.PROFILE_ENV, raising=False)
    assert not profile.is_beta()
    assert profile.app_name() == APP_NAME
    assert profile.webhook_port() == WEBHOOK_PORT
    assert profile.collection_writes_allowed()
    assert profile.addons_allowed()


def test_beta_has_its_own_name_port_and_no_shared_things(monkeypatch):
    monkeypatch.setenv(profile.PROFILE_ENV, "Beta")
    assert profile.is_beta()
    assert profile.app_name() == "Track Kommander Beta"
    assert profile.webhook_port() != WEBHOOK_PORT
    assert not profile.collection_writes_allowed()
    assert not profile.addons_allowed()


def test_beta_never_writes_the_collection(monkeypatch, tmp_path):
    import xml.etree.ElementTree as ET

    from core.collection_write import write_collection

    nml = tmp_path / "collection.nml"
    nml.write_text("<NML/>")
    monkeypatch.setenv(profile.PROFILE_ENV, "beta")
    with pytest.raises(PermissionError):
        write_collection(ET.parse(str(nml)), nml)
    assert nml.read_text() == "<NML/>"
    assert list(tmp_path.iterdir()) == [nml]  # no backup made either


def test_beta_loads_no_addon(monkeypatch):
    from core import extensions

    monkeypatch.setenv(profile.PROFILE_ENV, "beta")
    monkeypatch.setattr(extensions, "_loaded", False)
    monkeypatch.setattr(extensions, "_registered", False)
    imported = []
    monkeypatch.setattr("importlib.import_module", lambda name: imported.append(name))
    assert extensions.load_addons() is False
    assert imported == []
