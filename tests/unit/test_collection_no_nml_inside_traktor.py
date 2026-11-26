"""Inside Traktor, TK never reads collection.nml (User, 2026-11-24).

core.collection_source and core.playlists read Traktor's memory there; when that read fails
they return nothing rather than quietly parsing the XML. Outside Traktor the NML stays the
source.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import core.collection_source as cs
import core.playlists as pl

INSIDE = "/repo/traktor-native/re/embed_tk.py"


def _no_parse(*_a, **_k):
    raise AssertionError("collection.nml parsed inside Traktor")


@pytest.fixture
def inside(monkeypatch):
    monkeypatch.setenv("TK_EMBED", INSIDE)
    for name in ("parse_traktor_nml", "parse_traktor_nml_metadata", "nml_cues"):
        monkeypatch.setattr(cs, name, _no_parse)
    monkeypatch.setattr(pl, "_parse_playlists", _no_parse)
    monkeypatch.setattr(cs, "_native", None)
    monkeypatch.setattr(cs, "_native_cues", None)
    monkeypatch.setattr(pl, "_native", None)


def test_inside_traktor_without_memory_nothing_is_read_from_the_nml(inside, tmp_path) -> None:
    nml = tmp_path / "collection.nml"
    nml.write_text("<NML/>")
    assert cs.collection_paths(nml) == []
    assert cs.collection_metadata(nml) == {}
    assert cs.collection_cues(nml) == []
    assert pl.parse_playlists(nml) == []


def test_inside_traktor_a_failing_memory_read_does_not_fall_back(
    inside, monkeypatch, tmp_path
) -> None:
    def broken():
        raise RuntimeError("libtk said no")

    monkeypatch.setattr(cs, "_native", broken)
    monkeypatch.setattr(cs, "_native_cues", broken)
    monkeypatch.setattr(pl, "_native", broken)
    nml = tmp_path / "collection.nml"
    assert cs.collection_paths(nml) == []
    assert cs.collection_cues(nml) == []
    assert pl.parse_playlists(nml) == []


def test_outside_traktor_the_nml_is_still_the_source(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("TK_EMBED", raising=False)
    monkeypatch.setattr(cs, "_native", None)
    seen: list[Path] = []
    monkeypatch.setattr(cs, "parse_traktor_nml", lambda p: seen.append(p) or ["/a.mp3"])
    nml = tmp_path / "collection.nml"
    assert cs.collection_paths(nml) == ["/a.mp3"]
    assert seen == [nml]
