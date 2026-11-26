"""Writing the user's Traktor collection.

``collection.nml`` is the one file this application can damage that the user
cannot get back from us: their whole library, cues and playlists. It was
written over in place by four separate functions, each after copying it to
the same ``collection.nml.bak`` — so an interrupted write left half a library,
and the second tool to run overwrote the only copy of the original.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from core import collection_write
from core.collection_write import KEEP_BACKUPS, backups_of, write_collection

_BEFORE = (
    '<?xml version="1.0" encoding="UTF-8" standalone="no" ?>\n'
    '<NML VERSION="19"><COLLECTION ENTRIES="1">'
    '<ENTRY ARTIST="Motörhead" TITLE="Ace of Spades"/>'
    "</COLLECTION></NML>"
)


@pytest.fixture
def nml(tmp_path):
    path = tmp_path / "collection.nml"
    path.write_text(_BEFORE, encoding="utf-8")
    return path


@pytest.fixture
def clock(monkeypatch):
    """Backups are named after the time; tests set it."""
    now = {"stamp": "2026-09-29_10-00-00"}
    monkeypatch.setattr(collection_write, "_stamp", lambda: now["stamp"])
    return now


def _retitled(nml, title: str) -> ET.ElementTree:
    tree = ET.parse(nml)
    tree.getroot().find(".//ENTRY").set("TITLE", title)
    return tree


# ── What is written ─────────────────────────────────────────────────────────


def test_the_collection_holds_the_new_content(nml, clock) -> None:
    write_collection(_retitled(nml, "Overkill"), nml)

    entry = ET.parse(nml).getroot().find(".//ENTRY")
    assert entry.get("TITLE") == "Overkill"
    assert entry.get("ARTIST") == "Motörhead"


def test_the_bytes_are_what_a_direct_write_gives(nml, clock, tmp_path) -> None:
    """Traktor has read files written this way for as long as the tools have
    existed. Going through a sibling must not change a byte of them."""
    tree = _retitled(nml, "Overkill")
    direct = tmp_path / "direct.nml"
    tree.write(str(direct), encoding="unicode", xml_declaration=True)

    write_collection(tree, nml)

    # As macOS writes them, where those files were read: a direct write on
    # Windows ends its lines CR LF, which is the one thing not kept.
    assert nml.read_bytes() == direct.read_bytes().replace(b"\r\n", b"\n")


def test_lines_end_the_way_traktor_ends_them(nml, clock) -> None:
    """With a line feed alone, on Windows too: a collection Traktor 4.4.2
    wrote there had 9454 of them and not one CR LF. Left to the platform,
    ours came out CR LF throughout."""
    write_collection(_retitled(nml, "Overkill"), nml)

    written = nml.read_bytes()
    assert b"\n" in written
    assert b"\r" not in written


def test_nothing_partial_is_left_beside_it(nml, clock) -> None:
    write_collection(_retitled(nml, "Overkill"), nml)

    assert not list(nml.parent.glob("*.part"))


# ── The backup ──────────────────────────────────────────────────────────────


def test_the_backup_is_the_collection_as_it_was(nml, clock) -> None:
    backup = write_collection(_retitled(nml, "Overkill"), nml)

    assert backup.read_text(encoding="utf-8") == _BEFORE
    assert backup.name == "collection.nml.2026-09-29_10-00-00.bak"


def test_a_second_write_does_not_take_the_first_backup(nml, clock) -> None:
    """The original has to outlive the second tool to run."""
    first = write_collection(_retitled(nml, "Overkill"), nml)
    clock["stamp"] = "2026-09-29_10-05-00"
    second = write_collection(_retitled(nml, "Bomber"), nml)

    assert first != second
    assert first.read_text(encoding="utf-8") == _BEFORE
    assert "Overkill" in second.read_text(encoding="utf-8")


def test_two_writes_in_the_same_second_keep_both_backups(nml, clock) -> None:
    first = write_collection(_retitled(nml, "Overkill"), nml)
    second = write_collection(_retitled(nml, "Bomber"), nml)

    assert first != second
    assert first.read_text(encoding="utf-8") == _BEFORE


def test_only_the_newest_backups_are_kept(nml, clock) -> None:
    written = []
    for minute in range(KEEP_BACKUPS + 3):
        clock["stamp"] = f"2026-09-29_10-{minute:02d}-00"
        written.append(write_collection(_retitled(nml, f"T{minute}"), nml))

    assert backups_of(nml) == written[-KEEP_BACKUPS:]


def test_a_backup_made_the_old_way_is_left_alone(nml, clock) -> None:
    old = nml.with_name("collection.nml.bak")
    old.write_text("from an earlier version")
    for minute in range(KEEP_BACKUPS + 2):
        clock["stamp"] = f"2026-09-29_10-{minute:02d}-00"
        write_collection(_retitled(nml, f"T{minute}"), nml)

    assert old.read_text() == "from an earlier version"


# ── When it goes wrong ──────────────────────────────────────────────────────


def test_a_write_cut_short_leaves_the_collection_whole(nml, clock, monkeypatch) -> None:
    tree = _retitled(nml, "Overkill")

    def half(self, target, **_kw) -> None:
        # *target* is the sibling, already open for writing.
        target.write('<?xml version="1.0"?><NML><COLLECTION><ENTRY ARTI')
        target.flush()
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(ET.ElementTree, "write", half)
    with pytest.raises(OSError, match="No space left"):
        write_collection(tree, nml)

    assert nml.read_text(encoding="utf-8") == _BEFORE
    assert not list(nml.parent.glob("*.part"))


def test_no_backup_means_no_write(nml, clock, monkeypatch) -> None:
    """Without a copy of the original there is no way back, so the
    collection is not touched."""

    def refuse(_src, _dst):
        raise OSError(13, "Permission denied")

    monkeypatch.setattr(collection_write.shutil, "copy2", refuse)
    with pytest.raises(OSError, match="Permission denied"):
        write_collection(_retitled(nml, "Overkill"), nml)

    assert nml.read_text(encoding="utf-8") == _BEFORE
