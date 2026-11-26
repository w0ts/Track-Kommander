"""Where Native Instruments keeps a user's Traktor libraries."""

from __future__ import annotations

from pathlib import Path

from core import ni_paths, traktor_install


def test_root_is_documents_native_instruments(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert ni_paths.ni_documents_root() == tmp_path / "Documents" / "Native Instruments"


def test_traktor_install_uses_the_shared_root(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert traktor_install.nml_root() == ni_paths.ni_documents_root()
