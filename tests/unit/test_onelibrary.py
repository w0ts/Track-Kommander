"""Writing the OneLibrary database (``exportLibrary.db``).

What matters is not that we can read back what we wrote — it is that the file
is shaped exactly like one rekordbox wrote, because a player that finds a
column out of place refuses the whole stick without saying why. The schema is
pinned against a copy captured from a rekordbox 7 export; the rest checks that
an ``export.pdb`` comes through row for row.
"""

from __future__ import annotations

import re
import tempfile
from datetime import date
from pathlib import Path

import pytest

from core import onelibrary, pdb_seed

sqlcipher3 = pytest.importorskip("sqlcipher3", reason="sqlcipher3-wheels not installed")
pdb_edit = pytest.importorskip("rekordbox_pdb.edit", reason="rekordbox-pdb not installed")

CAPTURED = Path(__file__).parent / "data" / "onelibrary-schema.sql"


def _captured_columns() -> dict[str, list[str]]:
    """table -> column names, from the DDL rekordbox 7 wrote."""
    out: dict[str, list[str]] = {}
    for m in re.finditer(r"CREATE TABLE (\w+)\((.*?)\);", CAPTURED.read_text(), re.S):
        out[m.group(1)] = [c.strip().split()[0] for c in m.group(2).split(",") if c.strip()]
    return out


def _pdb(tmp: Path) -> Path:
    """A small export.pdb: three tracks, two by one artist, one playlist."""
    seed = pdb_seed.write(tmp / "seed.pdb")
    ed = pdb_edit.PdbEditor.from_file(str(seed))
    a = ed.add_track(
        title="Alpha",
        file_path="/Contents/A/alpha.mp3",
        filename="alpha.mp3",
        artist="Aphex",
        album="SAW",
        genre="Ambient",
        key="Gm",
        analyze_path="/PIONEER/USBANLZ/P000/00000001/ANLZ0000.DAT",
        tempo=12400,
        duration=300,
        bitrate=320,
        sample_rate=44100,
        file_size=12_000_000,
        rating=5,
        year=1992,
    )
    b = ed.add_track(
        title="Béta",
        file_path="/Contents/B/beta.wav",
        filename="beta.wav",
        artist="Orbital",
        tempo=13000,
        duration=200,
        analyze_path="/PIONEER/USBANLZ/P000/00000002/ANLZ0000.DAT",
    )
    ed.add_track(
        title="Gamma",
        file_path="/Contents/C/gamma.flac",
        filename="gamma.flac",
        artist="Aphex",
        album="SAW",
        tempo=0,
        duration=100,
    )
    pid = ed.create_playlist("Set 1")
    ed.add_to_playlist(pid, b)
    ed.add_to_playlist(pid, a)
    out = tmp / "export.pdb"
    ed.save(str(out))
    return out


@pytest.fixture(scope="module")
def written() -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="onelibrary-"))
    return onelibrary.write_from_pdb(
        _pdb(tmp), tmp / "PIONEER" / "rekordbox" / "exportLibrary.db", created=date(2026, 9, 24)
    )


@pytest.fixture(scope="module")
def db(written):
    conn = onelibrary.open_readonly(written)
    yield conn
    conn.close()


def _rows(db, table: str) -> list[dict]:
    return [dict(r) for r in db.execute(f'SELECT * FROM "{table}"')]


# ── Shape ───────────────────────────────────────────────────────────────────


def test_schema_is_the_one_rekordbox_writes(db) -> None:
    """Every table, every column, in rekordbox's order — typos included."""
    reference = _captured_columns()
    assert len(reference) == 22
    tables = {
        r[0]
        for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    }
    assert tables == set(reference)
    for table, expected in reference.items():
        got = [r[1] for r in db.execute(f'PRAGMA table_info("{table}")')]
        assert got == expected, table
    assert "isComplation" in reference["album"]
    assert "OutFileOffsetInBlock" in reference["cue"]


def test_indexes_match(db) -> None:
    expected = set(re.findall(r"CREATE INDEX (\w+)", CAPTURED.read_text()))
    got = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert got == expected


def test_the_passphrase_is_a_sqlcipher_passphrase() -> None:
    """64 lowercase alphanumerics with non-hex letters: a passphrase, never a
    raw hex key. Supplying it as hex is the classic way to get 'file is not
    a database'."""
    assert re.fullmatch(r"[a-z0-9]{64}", onelibrary.PASSPHRASE)
    assert re.search(r"[g-z]", onelibrary.PASSPHRASE)


def test_wrong_passphrase_cannot_open_it(written) -> None:
    with pytest.raises(sqlcipher3.DatabaseError):
        onelibrary.open_readonly(written, "b" * 64)


def test_no_journal_is_left_beside_the_database(written) -> None:
    """Rekordbox's own exports leave most rows in a -wal; a stale one beside
    a rebuilt database would be read as part of it."""
    assert not written.with_name(written.name + "-wal").exists()
    assert not written.with_name(written.name + "-shm").exists()
    assert not written.with_name(written.name + ".part").exists()


# ── Fixed rows ──────────────────────────────────────────────────────────────


def test_browse_configuration_is_present(db) -> None:
    assert len(_rows(db, "menuItem")) == len(onelibrary.MENU_ITEMS) == 27
    assert len(_rows(db, "category")) == len(onelibrary.CATEGORIES) == 22
    assert len(_rows(db, "sort")) == len(onelibrary.SORT_OPTIONS) == 17
    assert [r["name"] for r in _rows(db, "color")] == [
        "Pink",
        "Red",
        "Orange",
        "Yellow",
        "Green",
        "Aqua",
        "Blue",
        "Purple",
    ]
    assert len(_rows(db, "myTag")) == 4


def test_menu_names_keep_the_localisation_markers(db) -> None:
    """U+FFFA … U+FFFB tells the player to show its own translation."""
    for r in _rows(db, "menuItem"):
        assert r["name"].startswith("￺") and r["name"].endswith("￻"), r


def test_property_row(db) -> None:
    (prop,) = _rows(db, "property")
    assert prop == {
        "deviceName": "",
        "dbVersion": "1000",
        "numberOfContents": 3,
        "createdDate": "2026-09-24",
        "backGroundColorType": 0,
        "myTagMasterDBID": 0,
    }


# ── Content ─────────────────────────────────────────────────────────────────


def test_every_track_comes_through(db) -> None:
    rows = {r["title"]: r for r in _rows(db, "content")}
    assert set(rows) == {"Alpha", "Béta", "Gamma"}
    alpha = rows["Alpha"]
    assert alpha["bpmx100"] == 12400
    assert alpha["length"] == 300
    assert alpha["rating"] == 5
    assert alpha["releaseYear"] == 1992
    assert alpha["path"] == "/Contents/A/alpha.mp3"
    assert alpha["fileName"] == "alpha.mp3"
    assert alpha["fileSize"] == 12_000_000
    assert alpha["bitrate"] == 320
    assert alpha["samplingRate"] == 44100
    assert alpha["analysisDataFilePath"] == "/PIONEER/USBANLZ/P000/00000001/ANLZ0000.DAT"
    assert alpha["analysedBits"] == onelibrary.ANALYSED_BITS
    assert alpha["contentLink"] == onelibrary.CONTENT_LINK


def test_search_names_are_folded(db) -> None:
    rows = {r["title"]: r for r in _rows(db, "content")}
    assert rows["Béta"]["titleForSearch"] == "BETA"
    assert {r["nameForSearch"] for r in _rows(db, "artist")} == {"APHEX", "ORBITAL"}


def test_file_type_follows_the_extension(db) -> None:
    rows = {r["title"]: r for r in _rows(db, "content")}
    assert rows["Alpha"]["fileType"] == 1  # mp3
    assert rows["Béta"]["fileType"] == 11  # wav
    assert rows["Gamma"]["fileType"] == 5  # flac


def test_a_track_without_analysis_says_so(db) -> None:
    gamma = {r["title"]: r for r in _rows(db, "content")}["Gamma"]
    assert gamma["analysisDataFilePath"] is None
    assert gamma["analysedBits"] is None


def test_empty_values_are_null_not_zero(db) -> None:
    """A 0 in a lookup column would point at a row that does not exist."""
    gamma = {r["title"]: r for r in _rows(db, "content")}["Gamma"]
    assert gamma["genre_id"] is None
    assert gamma["key_id"] is None
    assert gamma["releaseYear"] is None
    assert gamma["djComment"] is None


def test_lookups_hold_only_what_is_referenced(db) -> None:
    """Two tracks by one artist share a row; nothing unreferenced is listed."""
    assert sorted(r["name"] for r in _rows(db, "artist")) == ["Aphex", "Orbital"]
    assert [r["name"] for r in _rows(db, "album")] == ["SAW"]
    assert [r["name"] for r in _rows(db, "genre")] == ["Ambient"]
    assert [r["name"] for r in _rows(db, "key")] == ["Gm"]
    assert _rows(db, "label") == []


def test_content_points_at_the_lookup_rows(db) -> None:
    rows = {r["title"]: r for r in _rows(db, "content")}
    artists = {r["artist_id"]: r["name"] for r in _rows(db, "artist")}
    albums = {r["album_id"]: r for r in _rows(db, "album")}
    assert artists[rows["Alpha"]["artist_id_artist"]] == "Aphex"
    assert artists[rows["Béta"]["artist_id_artist"]] == "Orbital"
    assert rows["Alpha"]["album_id"] == rows["Gamma"]["album_id"]
    assert albums[rows["Alpha"]["album_id"]]["name"] == "SAW"


# ── Playlists ───────────────────────────────────────────────────────────────


def test_playlist_and_its_order(db) -> None:
    (pl,) = _rows(db, "playlist")
    assert pl["name"] == "Set 1"
    assert pl["attribute"] == onelibrary.LEAF
    members = sorted(_rows(db, "playlist_content"), key=lambda r: r["sequenceNo"])
    titles = {r["content_id"]: r["title"] for r in _rows(db, "content")}
    assert [titles[m["content_id"]] for m in members] == ["Béta", "Alpha"]
    assert [m["sequenceNo"] for m in members] == [1, 2], "1-based, in playlist order"
    assert {m["playlist_id"] for m in members} == {pl["playlist_id"]}


# ── Cues live in ANLZ, not here ─────────────────────────────────────────────


def test_cue_table_is_left_empty(db) -> None:
    """Rekordbox writes cues to the ANLZ files only; a player reads them from
    there. Rows here would be a second, possibly disagreeing, copy."""
    assert _rows(db, "cue") == []


# ── Rewriting ───────────────────────────────────────────────────────────────


def test_rewriting_replaces_rather_than_appends(tmp_path) -> None:
    pdb = _pdb(tmp_path)
    out = tmp_path / "exportLibrary.db"
    onelibrary.write_from_pdb(pdb, out)
    onelibrary.write_from_pdb(pdb, out)
    conn = onelibrary.open_readonly(out)
    try:
        assert conn.execute("SELECT count(*) FROM content").fetchone()[0] == 3
        assert conn.execute("SELECT count(*) FROM property").fetchone()[0] == 1
    finally:
        conn.close()


def test_a_stale_journal_is_removed_first(tmp_path) -> None:
    pdb = _pdb(tmp_path)
    out = tmp_path / "exportLibrary.db"
    stale = out.with_name(out.name + "-wal")
    stale.write_bytes(b"leftover")
    onelibrary.write_from_pdb(pdb, out)
    assert not stale.exists()


def test_sqlite_never_runs_on_the_key(tmp_path, monkeypatch) -> None:
    """Built on the computer and copied over in one go: SQLite on a USB stick
    pays a journal create, sync and delete per commit, which made the export
    crawl."""
    pdb = _pdb(tmp_path)
    key_dir = tmp_path / "key" / "PIONEER" / "rekordbox"
    opened: list[Path] = []
    real_connect = onelibrary._connect

    def spy(path, *args, **kwargs):
        opened.append(Path(path))
        return real_connect(path, *args, **kwargs)

    monkeypatch.setattr(onelibrary, "_connect", spy)
    out = onelibrary.write_from_pdb(pdb, key_dir / "exportLibrary.db")
    assert opened and all(key_dir not in p.parents for p in opened)
    assert out.exists()
    assert sorted(p.name for p in key_dir.iterdir()) == ["exportLibrary.db"]
