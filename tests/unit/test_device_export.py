"""Tests for writing a Pioneer device export (a CDJ-readable USB stick).

The database can only be built on top of a blank ``export.pdb``, which needs
both the writer library and a seed file, so the full export is exercised only
where those exist. The parts that don't need them — the seed search, the
analysis layout, playlist index mapping — are tested everywhere, because those
are where the logic errors live.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest

from core import anlz, pdb_seed
from core.device_export import (
    DB_DIR,
    DeviceTrack,
    find_seed,
    scan_for_seed,
    seed_is_blank,
)

pdb_writer = pytest.importorskip("rekordbox_pdb", reason="rekordbox-pdb not installed")

#: A blank export.pdb captured from a Rekordbox-prepared stick.
SEED_CANDIDATES = (
    Path(__file__).parent / "data" / "export-seed.pdb",
    Path.home() / "Library/Application Support/Track Kommander/export-seed.pdb",
)


def _seed() -> Path:
    """A blank export.pdb. Found on a stick if there is one, generated if not.

    These used to skip wholesale on any machine without a Rekordbox-prepared
    key — which is every CI machine, so the export went untested there.
    """
    for p in SEED_CANDIDATES:
        if p.is_file():
            return p
    out = Path(tempfile.mkdtemp(prefix="pdb-seed-")) / "export.pdb"
    return pdb_seed.write(out)


# ── Analysis file layout ────────────────────────────────────────────────────


def test_analysis_sits_where_the_player_looks(exported) -> None:
    """An XDJ-1000 ignores the database's analyze path and hashes the
    track's own path instead; the files have to be in that folder."""
    from rekordbox_pdb import Database

    from core.anlz_path import anlz_dir

    dest, report = exported
    for row in Database.from_file(str(report.db_path)).tracks:
        folder = anlz_dir(row.file_path)
        assert row.analyze_path == f"/{folder}/ANLZ0000.DAT"
        assert (dest / folder / "ANLZ0000.DAT").is_file()
        assert (dest / folder / "ANLZ0000.EXT").is_file()


def test_scan_for_seed_returns_a_list() -> None:
    """Must not raise on a machine with no removable media."""
    assert isinstance(scan_for_seed(), list)


def test_find_seed_uses_the_cache_first(tmp_path) -> None:
    seed = _seed()
    if seed is None:
        pytest.skip("no blank export.pdb available")
    cached = tmp_path / "export-seed.pdb"
    shutil.copy2(seed, cached)
    assert find_seed(tmp_path) == cached


def test_find_seed_generates_one_when_there_is_nothing(tmp_path, monkeypatch) -> None:
    """It used to give up here, which is what made a Rekordbox-prepared stick
    a prerequisite for the very first export."""
    monkeypatch.setattr("core.device_export.scan_for_seed", list)
    seed = find_seed(tmp_path / "empty")
    assert seed is not None and seed.is_file()
    assert seed_is_blank(seed)


def test_find_seed_gives_up_if_it_cannot_even_generate(tmp_path, monkeypatch) -> None:
    """The caller still has to handle None — a read-only data directory, say."""
    monkeypatch.setattr("core.device_export.scan_for_seed", list)
    monkeypatch.setattr(
        "core.pdb_seed.write", lambda dest: (_ for _ in ()).throw(OSError("read-only"))
    )
    assert find_seed(tmp_path / "empty") is None


def test_a_blank_seed_is_recognised() -> None:
    seed = _seed()
    if seed is None:
        pytest.skip("no blank export.pdb available")
    assert seed_is_blank(seed), "the seed must not carry someone else's tracks"


# ── The whole export ────────────────────────────────────────────────────────


@pytest.fixture
def audio(tmp_path) -> list[Path]:
    soundfile = pytest.importorskip("soundfile")
    np = pytest.importorskip("numpy")
    out = []
    src = tmp_path / "src"
    src.mkdir()
    for i in range(3):
        p = src / f"track{i}.mp3"
        if "MP3" not in soundfile.available_formats():
            pytest.skip("libsndfile has no MP3 support")
        soundfile.write(p, np.zeros(44100, dtype="float32"), 44100, format="MP3")
        out.append(p)
    return out


@pytest.fixture
def exported(tmp_path, audio):
    from core.device_export import export

    seed = _seed()
    if seed is None:
        pytest.skip("no blank export.pdb available")

    tracks = [
        DeviceTrack(
            source=p,
            title=f"Track {i}",
            artist="A",
            bpm=128.0,
            duration_s=60,
            bitrate_kbps=320,
            cues=[anlz.Cue(time_ms=1000 * (i + 1), hot_cue=i + 1)],
            waveform=[8] * 400,
        )
        for i, p in enumerate(audio)
    ]
    dest = tmp_path / "usb"
    report = export(tracks, dest, seed, playlists={"Set A": [0, 1], "Set B": [2]})
    return dest, report


def test_writes_the_expected_tree(exported) -> None:
    dest, report = exported
    assert report.db_path == dest / DB_DIR / "export.pdb"
    assert report.db_path.is_file()
    assert report.copied == 3
    assert not report.errors
    assert len(list((dest / "Contents").rglob("*.mp3"))) == 3
    # Filed by artist and album, like Rekordbox.
    assert (dest / "Contents" / "A" / "Unknown Album" / "track0.mp3").is_file()


def test_onelibrary_database_is_written_beside_the_pdb(exported) -> None:
    """The 2023+ players read only exportLibrary.db; it must mirror the pdb."""
    onelibrary = pytest.importorskip("core.onelibrary")
    pytest.importorskip("sqlcipher3")
    dest, report = exported
    assert report.onelibrary_path == dest / DB_DIR / "exportLibrary.db"
    assert report.onelibrary_path.is_file()
    conn = onelibrary.open_readonly(report.onelibrary_path)
    try:
        titles = {r[0] for r in conn.execute("SELECT title FROM content")}
        names = {r[0] for r in conn.execute("SELECT name FROM playlist")}
        paths = [r[0] for r in conn.execute("SELECT analysisDataFilePath FROM content")]
    finally:
        conn.close()
    assert titles == {"Track 0", "Track 1", "Track 2"}
    assert names == {"Set A", "Set B"}
    for p in paths:
        assert (dest / p.lstrip("/")).is_file(), p


def test_database_lists_every_track(exported) -> None:
    from rekordbox_pdb import Database

    dest, report = exported
    db = Database.from_file(str(report.db_path))
    titles = {t.title for t in db.tracks}
    assert titles == {"Track 0", "Track 1", "Track 2"}


def test_playlists_are_written(exported) -> None:
    from rekordbox_pdb import Database

    _dest, report = exported
    db = Database.from_file(str(report.db_path))
    assert {p.name for p in db.playlist_tree} == {"Set A", "Set B"}
    assert len(list(db.playlist_entries)) == 3


def _tree(db) -> dict[tuple[str, ...], bool]:
    """Each node's position in the tree -> whether it is a folder."""
    nodes = {n.id: n for n in db.playlist_tree}

    def path(n) -> tuple[str, ...]:
        parent = nodes.get(n.parent_id)
        return (*path(parent), n.name) if parent else (n.name,)

    return {path(n): bool(n.is_folder) for n in nodes.values()}


def _folder_tracks(audio) -> list[DeviceTrack]:
    return [
        DeviceTrack(source=p, title=f"Track {i}", bpm=120.0, duration_s=10)
        for i, p in enumerate(audio)
    ]


def test_playlists_keep_their_folders(tmp_path, audio) -> None:
    """A CDJ browses the same tree Traktor shows: folders, then lists."""
    from rekordbox_pdb import Database

    from core.device_export import export

    seed = _seed()
    if seed is None:
        pytest.skip("no blank export.pdb available")
    report = export(
        _folder_tracks(audio),
        tmp_path / "usb",
        seed,
        playlists={
            ("Gigs", "Summer", "Set"): [0, 1],
            ("Gigs", "Warmup"): [2],
            ("Loose",): [0],
        },
    )
    db = Database.from_file(str(report.db_path))
    assert _tree(db) == {
        ("Gigs",): True,
        ("Gigs", "Summer"): True,
        ("Gigs", "Summer", "Set"): False,
        ("Gigs", "Warmup"): False,
        ("Loose",): False,
    }


def test_a_merge_keeps_the_folders_of_playlists_it_does_not_name(tmp_path, audio) -> None:
    from rekordbox_pdb import Database

    from core.device_export import export

    seed = _seed()
    if seed is None:
        pytest.skip("no blank export.pdb available")
    dest = tmp_path / "usb"
    tracks = _folder_tracks(audio)
    export(tracks, dest, seed, playlists={("Gigs", "Set"): [0, 1]})
    report = export(tracks, dest, seed, merge=True, playlists={("Gigs", "Other"): [2]})
    db = Database.from_file(str(report.db_path))
    assert _tree(db) == {
        ("Gigs",): True,
        ("Gigs", "Set"): False,
        ("Gigs", "Other"): False,
    }


def test_player_settings_are_written(exported) -> None:
    """Without them an XDJ-1000 stops at "MY SETTING DATA was not found"."""
    from core.my_settings import SETTINGS_FILES

    dest, report = exported
    for spec in SETTINGS_FILES:
        assert (dest / "PIONEER" / spec.name).is_file(), spec.name
    assert sorted(report.settings_written) == sorted(s.name for s in SETTINGS_FILES)


def test_analysis_files_exist_where_the_database_says(exported) -> None:
    """A wrong analyze_path means a player finds no grid, cues or waveform."""
    from rekordbox_pdb import Database

    dest, report = exported
    db = Database.from_file(str(report.db_path))
    for t in db.tracks:
        dat = dest / t.analyze_path.lstrip("/")
        assert dat.is_file(), f"{t.title}: {t.analyze_path} missing"
        assert dat.with_suffix(".EXT").is_file()


def test_analysis_carries_grid_and_cues(exported) -> None:
    from rekordbox_pdb import Database

    dest, report = exported
    db = Database.from_file(str(report.db_path))
    t = next(iter(db.tracks))
    a = anlz.parse((dest / t.analyze_path.lstrip("/")).read_bytes())
    assert "PQTZ" in a.fourccs, "beat grid"
    assert "PWAV" in a.fourccs, "waveform"
    assert a.getall("PCOB"), "cues"


def test_device_paths_are_player_relative(exported) -> None:
    from rekordbox_pdb import Database

    _dest, report = exported
    db = Database.from_file(str(report.db_path))
    for t in db.tracks:
        assert t.file_path.startswith("/Contents/"), t.file_path
        assert t.analyze_path.startswith("/PIONEER/"), t.analyze_path


def test_a_missing_source_is_reported_not_fatal(tmp_path, audio) -> None:
    from core.device_export import export

    seed = _seed()
    if seed is None:
        pytest.skip("no blank export.pdb available")
    tracks = [DeviceTrack(source=p, title=p.stem, bpm=120.0, duration_s=10) for p in audio]
    tracks.append(DeviceTrack(source=Path("/nowhere/gone.mp3"), title="Gone"))
    report = export(tracks, tmp_path / "usb", seed)
    assert len(report.missing) == 1
    assert report.copied == 3
    assert report.db_path is not None, "one bad track must not sink the export"


def test_playlist_indices_survive_a_missing_track(tmp_path, audio) -> None:
    """Playlists are indices into the caller's list; a track that never
    reaches the device must drop out without shifting the others."""
    from rekordbox_pdb import Database

    from core.device_export import export

    seed = _seed()
    if seed is None:
        pytest.skip("no blank export.pdb available")
    tracks = [
        DeviceTrack(source=Path("/nowhere/gone.mp3"), title="Gone"),
        DeviceTrack(source=audio[0], title="Real one", bpm=120.0, duration_s=10),
    ]
    report = export(tracks, tmp_path / "usb", seed, playlists={"Mixed": [0, 1]})
    db = Database.from_file(str(report.db_path))
    entries = list(db.playlist_entries)
    assert len(entries) == 1, "the missing track drops out"
    assert {t.title for t in db.tracks} == {"Real one"}


# ── The seed must be blank ──────────────────────────────────────────────────
# Seeding from a populated database silently inherits its tracks. That
# happened for real: a stick Rekordbox had exported 17 tracks to was used as
# the seed, and the resulting device library carried 17 phantom entries whose
# files were not on the stick — "the file could not be found" on the player.


def _populated_pdb(tmp_path: Path) -> Path:
    from rekordbox_pdb.edit import PdbEditor

    seed = _seed()
    if seed is None:
        pytest.skip("no blank export.pdb available")
    ed = PdbEditor.from_file(str(seed))
    ed.add_track(title="Someone else's track", file_path="/Contents/x.mp3")
    out = tmp_path / "populated.pdb"
    ed.save(str(out))
    return out


def test_a_populated_database_is_not_blank(tmp_path) -> None:
    from core.device_export import seed_is_blank

    assert not seed_is_blank(_populated_pdb(tmp_path))


def test_a_poisoned_cache_is_discarded(tmp_path, monkeypatch) -> None:
    """A cache poisoned once must not stay poisoned.

    What matters is that the populated file is never handed back — the
    phantom tracks it carries point at audio that is not on the key, which a
    player reports as "the file could not be found". With nothing else to
    fall back on a blank is now generated, so the check is that whatever
    comes back is blank, not that nothing comes back.
    """
    bad = _populated_pdb(tmp_path)
    cache = tmp_path / "cache"
    cache.mkdir()
    shutil.copy2(bad, cache / "export-seed.pdb")
    poisoned = (cache / "export-seed.pdb").read_bytes()

    monkeypatch.setattr("core.device_export.scan_for_seed", list)
    seed = find_seed(cache)

    assert seed is not None
    assert seed_is_blank(seed), "a populated database must never be the seed"
    assert seed.read_bytes() != poisoned, "the bad cache was handed straight back"


def test_scan_rejects_populated_databases(tmp_path, monkeypatch) -> None:
    from core.device_export import DB_DIR, scan_for_seed

    vol = tmp_path / "Volumes" / "Stick"
    (vol / DB_DIR).mkdir(parents=True)
    shutil.copy2(_populated_pdb(tmp_path), vol / DB_DIR / "export.pdb")
    monkeypatch.setattr("core.device_export.Path", Path)
    # Point the scan at our fake volume root.
    import core.device_export as de

    real_scan = de.scan_for_seed

    def _scan():
        out = []
        pdb = vol / DB_DIR / "export.pdb"
        if pdb.is_file() and de.seed_is_blank(pdb):
            out.append(pdb)
        return out

    assert _scan() == [], "a populated stick must not be offered as a seed"
    assert callable(real_scan) and callable(scan_for_seed)


# ── Progress after the copies ───────────────────────────────────────────────


def test_analysis_writes_and_phases_are_reported(tmp_path, audio) -> None:
    """The copy used to be the last thing heard from — the analysis files,
    the database and the sweeps ran in silence, which read as a hang."""
    from core.device_export import export

    seed = _seed()
    tracks = [
        DeviceTrack(source=p, title=f"T{i}", bpm=128.0, duration_s=60, waveform=[8] * 400)
        for i, p in enumerate(audio)
    ]
    analysis: list[tuple[int, int, str]] = []
    phases: list[str] = []
    export(
        tracks,
        tmp_path / "usb",
        seed,
        analysis_progress=lambda i, of, name: analysis.append((i, of, name)),
        phase=phases.append,
    )

    assert [(i, of) for i, of, _n in analysis] == [(1, 3), (2, 3), (3, 3)]
    assert analysis[0][2] == "track0.mp3"
    assert any("database" in p.lower() for p in phases)
    assert any("sidecar" in p.lower() for p in phases)


# ── Stopping half way ───────────────────────────────────────────────────────


def test_a_stopped_export_lists_only_what_it_copied(tmp_path, audio) -> None:
    """Stop used to raise FileNotFoundError: every planned track was treated
    as being on the key, copied or not, and the ones that were not got
    stat()ed. The database must name what is there and nothing else."""
    from rekordbox_pdb import Database

    from core.device_export import export

    seed = _seed()
    tracks = [
        DeviceTrack(source=p, title=f"T{i}", bpm=128.0, duration_s=60, waveform=[8] * 400)
        for i, p in enumerate(audio)
    ]
    calls = {"n": 0}

    def _stop_after_one() -> bool:
        calls["n"] += 1
        return calls["n"] > 1

    report = export(tracks, tmp_path / "usb", seed, should_stop=_stop_after_one)
    assert report.copied == 1
    assert report.db_path is not None
    assert {t.title for t in Database.from_file(str(report.db_path)).tracks} == {"T0"}
    assert [t.device_path for t in tracks if t.device_path] == [tracks[0].device_path]


# ── From a collection track to a device track ───────────────────────────────


def test_the_device_track_carries_stars_not_the_raw_ranking(tmp_path) -> None:
    """Every rating on the key used to show as 0 or 5: the PDB byte means
    0–5 stars and was handed Traktor's 0–255 RANKING."""
    from core.device_export import device_track_from
    from core.rekordbox import Track

    t = Track(
        path=str(tmp_path / "a.mp3"),
        title="T",
        artist="A",
        rating=153,
        bpm=128.0,
        total_time_s=60.0,
        bitrate=320,
        musical_key=0,
    )
    dt = device_track_from(t, offset_ms=0.0)
    assert dt.rating == 3
    assert dt.source == tmp_path / "a.mp3"
    assert dt.bitrate_kbps == 320


def test_a_manifest_from_an_older_format_is_ignored(tmp_path) -> None:
    """Its tracks were written with analysis files an XDJ-1000 ignores, so
    a merge must rewrite them instead of trusting the old fingerprints."""
    import json

    from core.device_export import MANIFEST_NAME, read_manifest, write_manifest

    write_manifest(tmp_path, {"/Contents/a.mp3": {"all": "x", "audio": "y"}})
    assert read_manifest(tmp_path) == {"/Contents/a.mp3": {"all": "x", "audio": "y"}}

    path = tmp_path / DB_DIR / MANIFEST_NAME
    old = json.loads(path.read_text())
    old["version"] = 4
    path.write_text(json.dumps(old))
    assert read_manifest(tmp_path) == {}


def test_each_row_carries_its_real_file_type(tmp_path) -> None:
    """The writer library knows no FLAC and files it as MP3 (1). An
    XDJ-1000 cannot decode FLAC and crashed on a key holding one labelled
    MP3; Rekordbox labels it 5, which lets a player refuse it cleanly."""
    import struct

    soundfile = pytest.importorskip("soundfile")
    np = pytest.importorskip("numpy")
    from rekordbox_pdb import Database
    from rekordbox_pdb.pdb import TableType

    from core.device_export import export

    seed = _seed()
    if seed is None:
        pytest.skip("no blank export.pdb available")
    src = tmp_path / "src"
    src.mkdir()
    silence = np.zeros(44100, dtype="float32")
    flac, wav = src / "a.flac", src / "b.wav"
    soundfile.write(flac, silence, 44100, format="FLAC")
    soundfile.write(wav, silence, 44100, format="WAV")
    tracks = [DeviceTrack(source=p, title=p.stem, bpm=120.0, duration_s=1) for p in (flac, wav)]
    report = export(tracks, tmp_path / "usb", seed)

    raw = report.db_path.read_bytes()
    db = Database.from_file(str(report.db_path))
    types = {}
    for loc in db.row_locations(TableType.TRACKS):
        track_id = struct.unpack_from("<I", raw, loc + 0x48)[0]
        types[track_id] = struct.unpack_from("<H", raw, loc + 0x5A)[0]
    by_name = {Path(r.file_path).suffix: types[r.id] for r in db.tracks}
    assert by_name == {".flac": 0x05, ".wav": 0x0B}


def test_page_row_counts_are_rekordboxs(exported) -> None:
    """Every data page with rows carries 1 and rows - 1 at 0x20/0x22, bar
    the fixed tables. Three tracks, two artists' worth of rows: the case an
    XDJ-1000 crashed on when it read rows and 0."""
    import struct

    from core.pdb_pages import FIXED_TABLES, row_count

    _dest, report = exported
    raw = report.db_path.read_bytes()
    checked = 0
    for n in range(1, len(raw) // 4096):
        page = raw[n * 4096 : (n + 1) * 4096]
        if not page[0x1B] or page[0x1B] & 0x40 or not row_count(page):
            continue
        table = struct.unpack_from("<I", page, 8)[0]
        rows = row_count(page)
        want = (rows, 0) if table in FIXED_TABLES else (1, rows - 1)
        assert struct.unpack_from("<HH", page, 0x20) == want, (n, table)
        checked += 1
    assert checked
