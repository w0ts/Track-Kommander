"""A collection.nml runs to tens of megabytes and is parsed on the GUI thread:
an unchanged file is parsed once, and any change to it is picked up."""

from __future__ import annotations

import os
import xml.etree.ElementTree as ET

import core.playlists as playlists

_NML = """<?xml version="1.0" encoding="UTF-8" standalone="no" ?>
<NML VERSION="19">
 <COLLECTION ENTRIES="0"></COLLECTION>
 <PLAYLISTS>
  <NODE TYPE="FOLDER" NAME="$ROOT">
   <SUBNODES COUNT="1">
    <NODE TYPE="PLAYLIST" NAME="{name}">
     <PLAYLIST ENTRIES="1" TYPE="LIST">
      <ENTRY><PRIMARYKEY TYPE="TRACK" KEY="Macintosh HD/:Music/:a.mp3"/></ENTRY>
     </PLAYLIST>
    </NODE>
   </SUBNODES>
  </NODE>
 </PLAYLISTS>
</NML>
"""


def _count_parses(monkeypatch) -> list[str]:
    parses: list[str] = []
    real = ET.parse

    def spy(source, *a, **k):
        parses.append(str(source))
        return real(source, *a, **k)

    monkeypatch.setattr(playlists.ET, "parse", spy)
    return parses


def test_an_unchanged_collection_is_parsed_once(tmp_path, monkeypatch) -> None:
    nml = tmp_path / "collection.nml"
    nml.write_text(_NML.format(name="Set A"))
    parses = _count_parses(monkeypatch)
    first = playlists.parse_playlists(nml)
    second = playlists.parse_playlists(nml)
    assert [p.name for p in first] == [p.name for p in second] == ["Set A"]
    assert len(parses) == 1


def test_a_changed_collection_is_parsed_again(tmp_path, monkeypatch) -> None:
    nml = tmp_path / "collection.nml"
    nml.write_text(_NML.format(name="Set A"))
    parses = _count_parses(monkeypatch)
    playlists.parse_playlists(nml)
    nml.write_text(_NML.format(name="Set Bee"))  # a different size
    st = nml.stat()
    os.utime(nml, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
    assert [p.name for p in playlists.parse_playlists(nml)] == ["Set Bee"]
    assert len(parses) == 2


def test_cached_by_file_keys_on_path_size_and_mtime(tmp_path) -> None:
    f = tmp_path / "x"
    f.write_text("a")
    calls: list[int] = []
    compute = lambda: calls.append(1) or len(calls)  # noqa: E731
    assert playlists.cached_by_file("t", f, compute) == 1
    assert playlists.cached_by_file("t", f, compute) == 1
    f.write_text("ab")
    assert playlists.cached_by_file("t", f, compute) == 2
