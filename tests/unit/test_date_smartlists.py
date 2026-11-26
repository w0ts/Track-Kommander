"""Date smartlists in Traktor's collection: one per calendar window.

Each kind (Imported, Released) has a dropdown: windows of 3, 6 or 12
months, or off. One smartlist per window, newest first, empty ones skipped.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import date

import pytest

from core.date_smartlists import (
    LEGACY_ROOT_FOLDER,
    ROOT_FOLDER,
    install,
    range_query,
    window_of,
    windows_for,
    write_to_collection,
)
from core.rekordbox import Track
from core.smartlist import evaluate


def _entry(imported: str, released: str = "") -> str:
    rel = f' RELEASE_DATE="{released}"' if released else ""
    return (
        f'<ENTRY TITLE="t"><LOCATION DIR="/:m/:" FILE="x.mp3" VOLUME="HD"/>'
        f'<INFO IMPORT_DATE="{imported}"{rel}/></ENTRY>'
    )


ENTRIES = "".join(
    [
        _entry("2026/9/20", "2026/1/1"),
        _entry("2026/8/2", "2025/1/1"),
        _entry("2026/2/14", "2025/6/30"),
        _entry("2025/11/5"),
    ]
)

COLLECTION = f"""<?xml version="1.0" encoding="UTF-8" standalone="no" ?>
<NML VERSION="20"><HEAD COMPANY="www.native-instruments.com" PROGRAM="Traktor Pro 4"></HEAD>
<COLLECTION ENTRIES="4">{ENTRIES}</COLLECTION>
<PLAYLISTS><NODE TYPE="FOLDER" NAME="$ROOT"><SUBNODES COUNT="2">
<NODE TYPE="FOLDER" NAME="Smart"><SUBNODES COUNT="0"></SUBNODES></NODE>
<NODE TYPE="PLAYLIST" NAME="403"><PLAYLIST ENTRIES="0" TYPE="LIST" UUID="abc"></PLAYLIST></NODE>
</SUBNODES></NODE></PLAYLISTS>
</NML>
"""


@pytest.fixture
def nml(tmp_path):
    p = tmp_path / "collection.nml"
    p.write_text(COLLECTION, encoding="utf-8")
    return p


@pytest.fixture
def tree(nml):
    return ET.parse(nml)


def _top(tree):
    return tree.getroot().find("PLAYLISTS/NODE/SUBNODES")


def _inner(tree):
    ours = next(n for n in _top(tree).findall("NODE") if n.get("NAME") == ROOT_FOLDER)
    return ours.find("SUBNODES")


def _lists(tree, kind):
    folder = next(n for n in _inner(tree).findall("NODE") if n.get("NAME") == kind)
    return [
        (
            n.get("NAME"),
            n.find("SMARTLIST/SEARCH_EXPRESSION").get("QUERY"),
            n.find("SMARTLIST").get("UUID"),
        )
        for n in folder.find("SUBNODES").findall("NODE")
    ]


# ── Windows ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "months, day, name, start, end",
    [
        (3, date(2026, 9, 20), "2026-07 to 09", date(2026, 7, 1), date(2026, 10, 1)),
        (3, date(2026, 1, 1), "2026-01 to 03", date(2026, 1, 1), date(2026, 4, 1)),
        (3, date(2025, 12, 31), "2025-10 to 12", date(2025, 10, 1), date(2026, 1, 1)),
        (6, date(2026, 8, 2), "2026-07 to 12", date(2026, 7, 1), date(2027, 1, 1)),
        (12, date(2026, 2, 14), "2026", date(2026, 1, 1), date(2027, 1, 1)),
    ],
)
def test_calendar_windows(months, day, name, start, end) -> None:
    w = window_of(day, months)
    assert (w.name, w.start, w.end) == (name, start, end)


def test_windows_are_newest_first_and_skip_empty_ones() -> None:
    days = [date(2026, 9, 20), date(2026, 2, 14), date(2025, 11, 5)]
    assert [w.name for w in windows_for(days, 3)] == [
        "2026-07 to 09",
        "2026-01 to 03",
        "2025-10 to 12",
    ]  # 2026 Apr–Jun empty


def test_names_sort_in_date_order() -> None:
    days = [date(2025, 11, 5), date(2026, 2, 14), date(2025, 1, 3), date(2026, 9, 20)]
    names = [w.name for w in windows_for(days, 3)]
    assert names == sorted(names, reverse=True)


def test_names_are_plain_ascii() -> None:
    """Traktor 4.5.1 showed an en dash as a trademark sign."""
    for months in (3, 6, 12):
        assert window_of(date(2026, 8, 1), months).name.isascii()


def test_queries_use_only_before_and_after() -> None:
    """Traktor dropped a >= date condition; it has is before / is after."""
    q = range_query("IMPORTDATE", window_of(date(2026, 1, 15), 3))
    assert ">=" not in q and "<=" not in q
    assert q == "$IMPORTDATE > 2025-12-31 & $IMPORTDATE < 2026-04-01"


def test_the_query_has_the_shape_traktor_saves() -> None:
    """Copied from a smartlist Traktor 4.5.1 wrote itself: & for and, dates
    unquoted. AND and quoted dates came back empty after Traktor saved."""
    q = range_query("IMPORTDATE", window_of(date(2026, 5, 1), 3))
    assert q == "$IMPORTDATE > 2026-03-31 & $IMPORTDATE < 2026-07-01"
    assert '"' not in q and " AND " not in q


def test_other_sizes_are_refused() -> None:
    with pytest.raises(ValueError):
        window_of(date(2026, 1, 1), 4)


# ── The collection ──────────────────────────────────────────────────────────


def test_one_smartlist_per_quarter_in_an_imported_folder(tree) -> None:
    count, _replaced = install(tree, {"Imported": 3})
    lists = _lists(tree, "Imported")
    assert [n for n, _q, _u in lists] == [
        "imported-2026-07 to 09",
        "imported-2026-01 to 03",
        "imported-2025-10 to 12",
    ]
    assert count == 3
    assert lists[0][1] == "$IMPORTDATE > 2026-06-30 & $IMPORTDATE < 2026-10-01"


def test_released_uses_release_dates_and_undated_tracks_are_skipped(tree) -> None:
    install(tree, {"Imported": None, "Released": 12})
    assert [n for n, _q, _u in _lists(tree, "Released")] == ["released-2026", "released-2025"]
    assert [n.get("NAME") for n in _inner(tree).findall("NODE")] == ["Released"]


def test_changing_the_size_rebuilds_the_folder(tree) -> None:
    install(tree, {"Imported": 3})
    _count, replaced = install(tree, {"Imported": 12})
    assert replaced
    assert [n for n, _q, _u in _lists(tree, "Imported")] == ["imported-2026", "imported-2025"]


def test_running_again_keeps_the_same_uuids(tree) -> None:
    install(tree, {"Imported": 3})
    first = _lists(tree, "Imported")
    install(tree, {"Imported": 3})
    assert _lists(tree, "Imported") == first


def test_list_names_take_a_custom_prefix(tree) -> None:
    install(tree, {"Imported": 12, "Released": 12}, {"Imported": "in ", "Released": ""})
    assert [n for n, _q, _u in _lists(tree, "Imported")] == ["in 2026", "in 2025"]
    assert [n for n, _q, _u in _lists(tree, "Released")] == ["2026", "2025"]


def test_a_missing_prefix_falls_back_to_the_default(tree) -> None:
    install(tree, {"Imported": 12, "Released": 12}, {"Imported": "in-"})
    assert [n for n, _q, _u in _lists(tree, "Released")] == ["released-2026", "released-2025"]


def test_changing_the_prefix_keeps_the_uuids(tree) -> None:
    """Traktor sees the same lists, renamed."""
    install(tree, {"Imported": 3})
    before = [u for _n, _q, u in _lists(tree, "Imported")]
    install(tree, {"Imported": 3}, {"Imported": "new-"})
    after = _lists(tree, "Imported")
    assert [u for _n, _q, u in after] == before
    assert after[0][0] == "new-2026-07 to 09"


def test_off_removes_the_kind(tree) -> None:
    install(tree, {"Imported": 3, "Released": 12})
    install(tree, {"Imported": 3, "Released": None})
    assert [n.get("NAME") for n in _inner(tree).findall("NODE")] == ["Imported"]


def test_the_users_own_playlists_and_other_folders_are_left_alone(tree) -> None:
    install(tree, {"Imported": 3})
    ET.SubElement(_inner(tree), "NODE", TYPE="FOLDER", NAME="Spotify")
    install(tree, {"Imported": 6})
    assert [n.get("NAME") for n in _top(tree).findall("NODE")] == ["Smart", "403", ROOT_FOLDER]
    assert [n.get("NAME") for n in _inner(tree).findall("NODE")] == ["Imported", "Spotify"]


def test_writing_backs_up_and_reparses(nml) -> None:
    result = write_to_collection(nml, {"Imported": 3})
    assert result.lists == 3
    assert result.backup.read_text(encoding="utf-8") == COLLECTION
    ET.parse(nml)


def test_a_window_query_selects_exactly_its_tracks() -> None:
    """So a window smartlist exports to a stick like any other."""
    tracks = [
        Track(path="/in", date_added="2026-09-30"),
        Track(path="/first", date_added="2026-07-01"),
        Track(path="/edge", date_added="2026-10-01"),
        Track(path="/before", date_added="2026-06-30"),
    ]
    q = range_query("IMPORTDATE", window_of(date(2026, 8, 1), 3))
    assert [t.path for t in evaluate(q, tracks)] == ["/in", "/first"]


def test_a_hand_edited_folder_without_subnodes_is_given_one(tree) -> None:
    install(tree, {"Imported": 3})
    ours = next(n for n in _top(tree).findall("NODE") if n.get("NAME") == ROOT_FOLDER)
    ours.remove(ours.find("SUBNODES"))
    count, replaced = install(tree, {"Imported": 3})
    assert (count, replaced) == (3, False)
    assert len(_lists(tree, "Imported")) == 3


def test_the_folder_is_named_just_kommander() -> None:
    assert ROOT_FOLDER == "Kommander"


def test_an_old_track_kommander_folder_is_renamed_in_place_with_its_lists(tree) -> None:
    install(tree, {"Imported": 3})
    before = _lists(tree, "Imported")
    ours = next(n for n in _top(tree).findall("NODE") if n.get("NAME") == ROOT_FOLDER)
    ours.set("NAME", LEGACY_ROOT_FOLDER)  # a collection written by an earlier version
    ET.SubElement(ours.find("SUBNODES"), "NODE", TYPE="PLAYLIST", NAME="Results")
    install(tree, {"Imported": 3})
    names = [n.get("NAME") for n in _top(tree).findall("NODE")]
    assert names == ["Smart", "403", ROOT_FOLDER]  # renamed, not a second folder
    assert _lists(tree, "Imported") == before  # same lists, same UUIDs
    assert "Results" in [n.get("NAME") for n in _inner(tree).findall("NODE")]
