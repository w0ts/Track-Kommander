"""Filling empty Traktor fields from Discogs matches, in a copy of the test
collection: preview first, then exactly what was previewed."""

from __future__ import annotations

import os
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from core import collection_write
from core.discogs_fields import DEFAULT_MAPPING, DiscogsRecord
from core.discogs_write import apply_fill, plan_fill

DATA = Path(__file__).parent / "data" / "collection.nml"
# Spelt as the collection reader gives them (os.path.normpath: "\\" on Windows).
FULL_HOUSE = os.path.normpath("/Users/dj/Music/Tracks/Test Artist - Full House.mp3")
MIRROR = os.path.normpath("/Users/dj/Music/Tracks/Test Artist - Mirror First.mp3")

RECORD = DiscogsRecord(
    release_id=1,
    genres=("Electronic",),
    styles=("Deep House",),
    label="Discogs Label",
    catno="DL 001",
    released="2019-04-12",
    year=2019,
)


@pytest.fixture
def nml(tmp_path, monkeypatch):
    path = tmp_path / "collection.nml"
    shutil.copy(DATA, path)
    monkeypatch.setattr(collection_write, "_stamp", lambda: "2026-10-03_12-00-00")
    return path


def _info(nml: Path, path: str) -> ET.Element:
    from core.traktor import _nml_build_path

    for entry in ET.parse(nml).getroot().iter("ENTRY"):
        loc = entry.find("LOCATION")
        if loc is not None and _nml_build_path(loc) == path:
            info = entry.find("INFO")
            assert info is not None
            return info
    raise AssertionError(f"{path} not in the collection")


def test_the_plan_names_only_empty_mapped_fields(nml) -> None:
    plan = plan_fill(nml, {FULL_HOUSE: RECORD, MIRROR: RECORD}, DEFAULT_MAPPING)
    by_path = {t.path: {c.field.attr: (c.old, c.new) for c in t.changes} for t in plan}
    # Full House has genre, label and a date: only the catalog number is empty.
    assert by_path[FULL_HOUSE] == {"CATALOG_NO": ("", "DL 001")}
    # Mirror First has a genre but no label, catalog number or date.
    assert by_path[MIRROR] == {
        "LABEL": ("", "Discogs Label"),
        "CATALOG_NO": ("", "DL 001"),
        "RELEASE_DATE": ("", "2019/4/12"),
    }


def test_tracks_without_a_match_are_not_in_the_plan(nml) -> None:
    assert plan_fill(nml, {}, DEFAULT_MAPPING) == []


def test_applying_writes_the_plan_and_keeps_a_backup(nml) -> None:
    plan = plan_fill(nml, {MIRROR: RECORD}, DEFAULT_MAPPING)
    assert apply_fill(nml, plan) == 1
    info = _info(nml, MIRROR)
    assert info.get("LABEL") == "Discogs Label"
    assert info.get("RELEASE_DATE") == "2019/4/12"
    assert info.get("GENRE") == "House"  # was there, untouched
    assert collection_write.backups_of(nml)


def test_an_edit_made_after_the_preview_is_not_overwritten(nml) -> None:
    plan = plan_fill(nml, {MIRROR: RECORD}, DEFAULT_MAPPING)
    text = nml.read_text(encoding="utf-8").replace(
        'GENRE="House" KEY="9d"', 'GENRE="House" LABEL="Typed In Traktor" KEY="9d"'
    )
    nml.write_text(text, encoding="utf-8")
    apply_fill(nml, plan)
    info = _info(nml, MIRROR)
    assert info.get("LABEL") == "Typed In Traktor"
    assert info.get("CATALOG_NO") == "DL 001"  # still empty at write time, so filled


def test_nothing_to_write_leaves_the_file_alone(nml) -> None:
    before = nml.read_bytes()
    assert apply_fill(nml, []) == 0
    assert nml.read_bytes() == before
    assert collection_write.backups_of(nml) == []


def test_a_missing_info_is_created_where_traktor_keeps_it(nml) -> None:
    text = nml.read_text(encoding="utf-8")
    start = text.index("<INFO", text.index('FILE="Test Artist - Mirror First.mp3"'))
    end = text.index("/>", start) + 2
    nml.write_text(text[:start] + text[end:], encoding="utf-8")
    plan = plan_fill(nml, {MIRROR: RECORD}, DEFAULT_MAPPING)
    apply_fill(nml, plan)
    for entry in ET.parse(nml).getroot().iter("ENTRY"):
        if entry.get("TITLE") == "Mirror First":
            tags = [child.tag for child in entry]
            assert tags.index("INFO") == tags.index("MODIFICATION_INFO") + 1
            info = entry.find("INFO")
            assert info is not None and info.get("GENRE") == "Deep House"
