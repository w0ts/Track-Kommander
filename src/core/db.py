"""SQLite database helpers."""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

import numpy as np

_log = logging.getLogger(__name__)


def _db_path() -> Path:
    """OS-standard per-user data location.

    Resolves to ``~/Library/Application Support/Track Kommander/`` on
    macOS, ``~/.local/share/Track Kommander/`` on Linux, ``%APPDATA%/
    Track Kommander/`` on Windows. Same path in dev and frozen — the
    repo no longer accumulates database files in the working tree.
    """
    from core.runtime import user_data_dir

    return user_data_dir() / "traktor_kommander.db"


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_db_path()), timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


#: Columns added to ``tracks`` after the original CREATE TABLE, as
#: ``"name TYPE"`` definitions. init_db adds whichever are missing.
_ADDED_COLUMNS: tuple[str, ...] = (
    "artist TEXT",
    "album TEXT",
    "title TEXT",
    "genre TEXT",
    "rating INTEGER",
    # Extra Traktor-collection metadata (display-only, not matched on)
    "release_date TEXT",
    "import_date TEXT",
    "label TEXT",
    "bitrate INTEGER",
    "play_count INTEGER",
    # Signal features (librosa)
    "danceability REAL",
    "dynamic_complexity REAL",
    "hfc REAL",
    "low_energy REAL",
    "mid_energy REAL",
    "loudness REAL",
    "bassiness REAL",
    "low_dynamics REAL",
    "mid_dynamics REAL",
    "high_dynamics REAL",
    "spectral_centroid REAL",
    # ML features (CLAP)
    "is_vocal REAL",
    "mood_party REAL",
    "mood_aggressive REAL",
    "mood_happy REAL",
    "mood_relaxed REAL",
    "arousal REAL",
    "valence REAL",
    "is_electronic REAL",
    "genres TEXT",
    "genres_vector BLOB",
    "melodic REAL",
    # One bit per feature: analysed, at that feature's current
    # version. Replaces the global analysis_version — see
    # core.feature_bits.
    "computed INTEGER",
    # Bar-aligned shape: how the track is built and what the bass does
    "loop_1bar REAL",
    "loop_4bar REAL",
    "sameness REAL",
    "evolution REAL",
    "sections_per_min REAL",
    "bass_notes_per_bar REAL",
    "bass_range REAL",
    "bass_jump REAL",
    "bass_rest REAL",
    "bass_is_major INTEGER",
    "bounce REAL",
    "onset_density REAL",
    # Retired: the global version the computed mask replaced. Still
    # added so older databases keep their shape; nothing writes it.
    "analysis_version INTEGER",
)

#: Columns earlier versions had and this one no longer uses. Dropped when
#: present (needs SQLite 3.35+ for DROP COLUMN).
_RETIRED_COLUMNS: tuple[str, ...] = (
    "raw_energy",
    "energy",
    "is_vocal_clap",
    "mood_party_clap",
    "mood_aggressive_clap",
    "mood_happy_clap",
    "mood_relaxed_clap",
    "arousal_clap",
    "valence_clap",
    "is_electronic_clap",
    "genres_clap",
    "genres_vector_clap",
)


def _track_columns(c: sqlite3.Connection) -> frozenset[str]:
    return frozenset(r[1] for r in c.execute("PRAGMA table_info(tracks)"))


def _alter_tracks(c: sqlite3.Connection, clause: str) -> None:
    """Run one ``ALTER TABLE tracks`` and fail loudly if it does not work.

    Only ALTERs the schema actually needs reach here, so any error is a real
    one (locked database, disk I/O, read-only volume, an index on a retired
    column). Carrying on would leave the app on a half-migrated schema, so
    log which statement failed on which file, then let the error through.
    """
    try:
        c.execute(f"ALTER TABLE tracks {clause}")
    except sqlite3.Error:
        _log.exception(
            "schema migration failed: ALTER TABLE tracks %s (database %s)", clause, _db_path()
        )
        raise


def _add_missing_columns(c: sqlite3.Connection) -> None:
    present = _track_columns(c)
    for col_def in _ADDED_COLUMNS:
        if col_def.split()[0] not in present:
            _alter_tracks(c, f"ADD COLUMN {col_def}")


def _drop_retired_columns(c: sqlite3.Connection) -> None:
    present = _track_columns(c)
    for col in _RETIRED_COLUMNS:
        if col in present:
            _alter_tracks(c, f"DROP COLUMN {col}")


def _version(c: sqlite3.Connection) -> int:
    return int(c.execute("PRAGMA user_version").fetchone()[0])


def written_by_a_newer_build() -> bool:
    """Was this library last shaped by a newer Track Kommander than this?

    It is still read and written — an older build has always coped with
    columns it does not know — but it is not migrated, and nothing in it is
    dropped.
    """
    with get_conn() as c:
        return _version(c) > SCHEMA_VERSION


def _migrate(c: sqlite3.Connection) -> None:
    """Run the steps this library has not had, in order, recording each.

    Each step and the record of it are committed together, so a step that
    fails is run again at the next start rather than skipped.
    """
    at = _version(c)
    if at > SCHEMA_VERSION:
        _log.warning(
            "this library was written by a newer Track Kommander (schema %d, "
            "this build knows %d) — using it as it is, changing nothing in "
            "its shape",
            at,
            SCHEMA_VERSION,
        )
        return
    for number, step in enumerate(MIGRATIONS[at:], start=at + 1):
        try:
            step(c)
            # PRAGMA takes no parameters; the number is ours, not input.
            c.execute(f"PRAGMA user_version = {int(number)}")
            c.commit()
        except sqlite3.Error:
            c.rollback()
            _log.exception("schema step %d failed (database %s)", number, _db_path())
            raise
        _log.info("library schema brought to version %d", number)


def _step_1_the_shape_before_versions(c: sqlite3.Connection) -> None:
    """Everything that used to run at every start and only ever needed to
    run once: the masks for a library analysed before they existed, the
    repair of masks that claimed more than was stored, and the columns
    retired so far."""
    seed_computed_masks(c)
    repair_computed_masks(c)
    _drop_retired_columns(c)


#: What has been done to the shape of the library, once each, in order. A
#: step is never edited after it ships and never removed: a library that is
#: at version 1 has had exactly step 1, whatever the code says later. Adding
#: a column needs no step (see :func:`_add_missing_columns`); retiring one,
#: or rewriting what is stored, does.
MIGRATIONS: tuple[Callable[[sqlite3.Connection], None], ...] = (_step_1_the_shape_before_versions,)

#: The version this build brings a library to.
SCHEMA_VERSION = len(MIGRATIONS)


def init_db() -> None:
    with get_conn() as c:
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("""
            CREATE TABLE IF NOT EXISTS tracks (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                path        TEXT UNIQUE NOT NULL,
                filename    TEXT NOT NULL,
                bpm         REAL,
                key_num     INTEGER,
                is_major    INTEGER,
                camelot     TEXT,
                duration    REAL,
                analyzed_at TEXT DEFAULT (datetime('now'))
            )
        """)
        _add_missing_columns(c)
        c.commit()
        c.execute("CREATE INDEX IF NOT EXISTS idx_tracks_camelot ON tracks(camelot)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_tracks_key_num ON tracks(key_num)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_tracks_bpm ON tracks(bpm)")
        # Per-model embedding vectors live in a side table rather than as ever-more
        # BLOB columns on tracks: a new similarity model is then a registry entry,
        # not a schema change. Keyed by (path, model); the vector is float32 bytes.
        c.execute("""
            CREATE TABLE IF NOT EXISTS track_embeddings (
                path   TEXT    NOT NULL,
                model  TEXT    NOT NULL,
                vector BLOB    NOT NULL,
                dim    INTEGER NOT NULL,
                PRIMARY KEY (path, model)
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_emb_model ON track_embeddings(model)")
        # Computed waveforms for the Pioneer export. Its own table rather than
        # BLOB columns on tracks: these are tens of kilobytes each and nothing
        # but the export wants them, so they should not be dragged into every
        # SELECT the browser does. ``fingerprint`` is the file's
        # ``size:mtime_ns`` and ``version`` is anlz.WAVEFORM_VERSION — a row
        # that disagrees with either is stale. See core/waveform_cache.py.
        c.execute("""
            CREATE TABLE IF NOT EXISTS track_waveforms (
                path        TEXT    PRIMARY KEY,
                fingerprint TEXT    NOT NULL,
                version     INTEGER NOT NULL,
                n_preview   INTEGER NOT NULL,
                n_detail    INTEGER NOT NULL,
                blob        BLOB    NOT NULL,
                stored_at   TEXT    DEFAULT (datetime('now'))
            )
        """)
        # Discogs lookups (core/discogs_store.py). One row per track that has
        # been looked up, matched or not, so a lookup never asks Discogs twice
        # for a track it has an answer for. ``record`` and ``candidates`` are
        # JSON: the accepted release's fields, and the releases kept for review.
        c.execute("""
            CREATE TABLE IF NOT EXISTS track_discogs (
                path         TEXT    PRIMARY KEY,
                status       TEXT    NOT NULL,
                confidence   REAL,
                release_id   INTEGER,
                record       TEXT,
                candidates   TEXT,
                error        TEXT,
                looked_up_at REAL    NOT NULL
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_discogs_status ON track_discogs(status)")
        # What Spotify said about tracks that came from a Spotify playlist
        # (core/spotify_store.py, filled from the playlist jobs): the release
        # date and album for the matcher's fallback, and a clean artist/title
        # for the Discogs search. No label or genre: Spotify's public pages
        # have none.
        c.execute("""
            CREATE TABLE IF NOT EXISTS track_spotify (
                path         TEXT PRIMARY KEY,
                spotify_id   TEXT NOT NULL,
                artist       TEXT,
                title        TEXT,
                album        TEXT,
                release_date TEXT,
                track_number TEXT,
                duration_s   REAL,
                playlist     TEXT,
                synced_at    REAL NOT NULL
            )
        """)
        # Tracks the Spotify search has already been asked about, found or not,
        # so a miss is not searched again on every lookup run (up to ten
        # requests each). ``status``: ok, no_match or no_label.
        c.execute("""
            CREATE TABLE IF NOT EXISTS spotify_tried (
                path     TEXT PRIMARY KEY,
                status   TEXT NOT NULL,
                tried_at REAL NOT NULL
            )
        """)
        # What an unsure search found (JSON), so the review can show Spotify's
        # best guess and the user can say it is right.
        if "guess" not in {r[1] for r in c.execute("PRAGMA table_info(spotify_tried)")}:
            c.execute("ALTER TABLE spotify_tried ADD COLUMN guess TEXT")
        # Added after the table first shipped: what a Spotify search (not a
        # playlist) found — the label from the album's ℗ line, the ISRC, the
        # artists' genres (JSON), whether the match was sure, and where the
        # row came from ('playlist' or 'search').
        have = {r[1] for r in c.execute("PRAGMA table_info(track_spotify)")}
        for column, kind in (
            ("label", "TEXT"),
            ("isrc", "TEXT"),
            ("genres", "TEXT"),
            ("sure", "INTEGER"),
            ("source", "TEXT"),
        ):
            if column not in have:
                c.execute(f"ALTER TABLE track_spotify ADD COLUMN {column} {kind}")
        # Per-track and per-playlist comments the user writes in TK — a sidecar to
        # Traktor's own comment fields, never written back to the collection (goal
        # 5). ``playlist = ''`` is the track's general comment; a playlist name
        # scopes a comment to that playlist, so one track can read differently per
        # set. Keyed on the track's file path (the id TK already has).
        c.execute("""
            CREATE TABLE IF NOT EXISTS track_comments (
                path       TEXT NOT NULL,
                playlist   TEXT NOT NULL DEFAULT '',
                comment    TEXT NOT NULL,
                updated_at REAL NOT NULL,
                PRIMARY KEY (path, playlist)
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_comments_playlist ON track_comments(playlist)")
        c.commit()
        # Last, once every table is there: the steps work on the whole shape.
        _migrate(c)


def upsert_track(
    path: str,
    filename: str,
    bpm: float | None,
    key_num: int | None,
    is_major: bool | None,
    camelot: str | None,
    duration: float,
    artist: str = "",
    album: str = "",
    title: str = "",
    genre: str = "",
    rating: int = 0,
    release_date: str = "",
    import_date: str = "",
    label: str = "",
    bitrate: int | None = None,
    play_count: int | None = None,
    # Signal features (librosa)
    danceability: float | None = None,
    dynamic_complexity: float | None = None,
    hfc: float | None = None,
    low_energy: float | None = None,
    mid_energy: float | None = None,
    loudness: float | None = None,
    bassiness: float | None = None,
    low_dynamics: float | None = None,
    mid_dynamics: float | None = None,
    high_dynamics: float | None = None,
    spectral_centroid: float | None = None,
    # ML features (CLAP)
    is_vocal: float | None = None,
    mood_party: float | None = None,
    mood_aggressive: float | None = None,
    mood_happy: float | None = None,
    mood_relaxed: float | None = None,
    arousal: float | None = None,
    valence: float | None = None,
    is_electronic: float | None = None,
    genres: str | None = None,
    genres_vector: bytes | None = None,
    melodic: float | None = None,
    # Bar-aligned shape — structure and bassline
    loop_1bar: float | None = None,
    loop_4bar: float | None = None,
    sameness: float | None = None,
    evolution: float | None = None,
    sections_per_min: float | None = None,
    bass_notes_per_bar: float | None = None,
    bass_range: float | None = None,
    bass_jump: float | None = None,
    bass_rest: float | None = None,
    bass_is_major: int | None = None,
    bounce: float | None = None,
    onset_density: float | None = None,
    computed: int | None = None,
) -> None:
    with get_conn() as c:
        c.execute(
            """
            INSERT INTO tracks
                (path, filename, bpm, key_num, is_major, camelot, duration,
                 artist, album, title, genre, rating,
                 release_date, import_date, label, bitrate, play_count,
                 danceability, dynamic_complexity, hfc,
                 low_energy, mid_energy, loudness, bassiness,
                 low_dynamics, mid_dynamics, high_dynamics,
                 spectral_centroid,
                 is_vocal, mood_party, mood_aggressive, mood_happy, mood_relaxed,
                 arousal, valence, is_electronic, genres, genres_vector, melodic,
                 loop_1bar, loop_4bar, sameness, evolution, sections_per_min,
                 bass_notes_per_bar, bass_range, bass_jump, bass_rest,
                 bass_is_major, bounce, onset_density,
                 computed)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?,
                    ?, ?, ?,
                    ?, ?, ?, ?,
                    ?, ?, ?,
                    ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?,
                    ?)
            ON CONFLICT(path) DO UPDATE SET
                bpm=excluded.bpm, key_num=excluded.key_num,
                is_major=excluded.is_major, camelot=excluded.camelot,
                duration=excluded.duration, analyzed_at=datetime('now'),
                artist=excluded.artist, album=excluded.album,
                title=excluded.title, genre=excluded.genre,
                rating=excluded.rating,
                release_date=excluded.release_date, import_date=excluded.import_date,
                label=excluded.label, bitrate=excluded.bitrate,
                play_count=excluded.play_count,
                danceability=COALESCE(excluded.danceability, danceability),
                dynamic_complexity=COALESCE(excluded.dynamic_complexity, dynamic_complexity),
                hfc=COALESCE(excluded.hfc, hfc),
                low_energy=COALESCE(excluded.low_energy, low_energy),
                mid_energy=COALESCE(excluded.mid_energy, mid_energy),
                loudness=COALESCE(excluded.loudness, loudness),
                bassiness=COALESCE(excluded.bassiness, bassiness),
                low_dynamics=COALESCE(excluded.low_dynamics, low_dynamics),
                mid_dynamics=COALESCE(excluded.mid_dynamics, mid_dynamics),
                high_dynamics=COALESCE(excluded.high_dynamics, high_dynamics),
                spectral_centroid=COALESCE(excluded.spectral_centroid, spectral_centroid),
                is_vocal=COALESCE(excluded.is_vocal, is_vocal),
                mood_party=COALESCE(excluded.mood_party, mood_party),
                mood_aggressive=COALESCE(excluded.mood_aggressive, mood_aggressive),
                mood_happy=COALESCE(excluded.mood_happy, mood_happy),
                mood_relaxed=COALESCE(excluded.mood_relaxed, mood_relaxed),
                arousal=COALESCE(excluded.arousal, arousal),
                valence=COALESCE(excluded.valence, valence),
                is_electronic=COALESCE(excluded.is_electronic, is_electronic),
                genres=COALESCE(excluded.genres, genres),
                genres_vector=COALESCE(excluded.genres_vector, genres_vector),
                melodic=COALESCE(excluded.melodic, melodic),
                -- Features this run computed are added to what the track
                -- already had: a partial scan must not erase the rest.
                loop_1bar=COALESCE(excluded.loop_1bar, tracks.loop_1bar),
                loop_4bar=COALESCE(excluded.loop_4bar, tracks.loop_4bar),
                sameness=COALESCE(excluded.sameness, tracks.sameness),
                evolution=COALESCE(excluded.evolution, tracks.evolution),
                sections_per_min=COALESCE(excluded.sections_per_min,
                                          tracks.sections_per_min),
                bass_notes_per_bar=COALESCE(excluded.bass_notes_per_bar,
                                            tracks.bass_notes_per_bar),
                bass_range=COALESCE(excluded.bass_range, tracks.bass_range),
                bass_jump=COALESCE(excluded.bass_jump, tracks.bass_jump),
                bass_rest=COALESCE(excluded.bass_rest, tracks.bass_rest),
                bass_is_major=COALESCE(excluded.bass_is_major, tracks.bass_is_major),
                bounce=COALESCE(excluded.bounce, tracks.bounce),
                onset_density=COALESCE(excluded.onset_density, tracks.onset_density),
                -- Features this run computed are added to what the track
                -- already had: a partial scan must not erase the rest.
                computed=COALESCE(tracks.computed, 0) | COALESCE(excluded.computed, 0)
        """,
            (
                path,
                filename,
                bpm,
                key_num,
                int(is_major) if is_major is not None else None,
                camelot,
                duration,
                artist,
                album,
                title,
                genre,
                rating,
                release_date,
                import_date,
                label,
                bitrate,
                play_count,
                danceability,
                dynamic_complexity,
                hfc,
                low_energy,
                mid_energy,
                loudness,
                bassiness,
                low_dynamics,
                mid_dynamics,
                high_dynamics,
                spectral_centroid,
                is_vocal,
                mood_party,
                mood_aggressive,
                mood_happy,
                mood_relaxed,
                arousal,
                valence,
                is_electronic,
                genres,
                genres_vector,
                melodic,
                loop_1bar,
                loop_4bar,
                sameness,
                evolution,
                sections_per_min,
                bass_notes_per_bar,
                bass_range,
                bass_jump,
                bass_rest,
                bass_is_major,
                bounce,
                onset_density,
                computed,
            ),
        )
        c.commit()


def update_track_metadata(path: str, artist: str, album: str, title: str, genre: str) -> None:
    """Update only the metadata columns for an already-analysed track."""
    with get_conn() as c:
        c.execute(
            "UPDATE tracks SET artist=?, album=?, title=?, genre=? WHERE path=?",
            (artist, album, title, genre, path),
        )
        c.commit()


def batch_update_key(updates: list[tuple[int, bool, str, str]]) -> None:
    """Bulk-update key columns. Each tuple: (key_num, is_major, camelot, path)."""
    with get_conn() as c:
        c.executemany(
            "UPDATE tracks SET key_num=?, is_major=?, camelot=? WHERE path=?",
            [(kn, int(im), cal, p) for kn, im, cal, p in updates],
        )
        c.commit()


def batch_update_bpm(updates: list[tuple[float, str]]) -> None:
    """Bulk-update bpm column. Each tuple: (bpm, path)."""
    with get_conn() as c:
        c.executemany("UPDATE tracks SET bpm=? WHERE path=?", updates)
        c.commit()


def batch_update_metadata(updates: list[tuple]) -> None:
    """Bulk-update metadata in a single transaction. Each tuple:
    (artist, album, title, genre, rating, release_date, import_date, label,
     bitrate, play_count, path).
    """
    with get_conn() as c:
        c.executemany(
            "UPDATE tracks SET artist=?, album=?, title=?, genre=?, rating=?, "
            "release_date=?, import_date=?, label=?, bitrate=?, play_count=? "
            "WHERE path=?",
            updates,
        )
        c.commit()


def get_metadata_batch(paths: list[str]) -> dict[str, tuple]:
    """Return {path: (artist, album, title, genre, rating, release_date,
    import_date, label, bitrate, play_count)} for the given paths.

    Chunks the IN-list to stay under SQLite's bound-variable limit.
    """
    if not paths:
        return {}
    rows: list = []
    with get_conn() as c:
        for i in range(0, len(paths), 900):
            chunk = paths[i : i + 900]
            placeholders = ",".join("?" * len(chunk))
            rows += c.execute(
                "SELECT path, artist, album, title, genre, rating, release_date, "
                f"import_date, label, bitrate, play_count FROM tracks"
                f" WHERE path IN ({placeholders})",
                chunk,
            ).fetchall()
    return {
        r["path"]: (
            r["artist"] or "",
            r["album"] or "",
            r["title"] or "",
            r["genre"] or "",
            int(r["rating"] or 0),
            r["release_date"] or "",
            r["import_date"] or "",
            r["label"] or "",
            r["bitrate"],
            r["play_count"],
        )
        for r in rows
    }


def _wanted_for_count() -> int:
    """The features a scan would compute — what "up to date" is measured
    against now that there is no global version."""
    from core.feature_bits import enabled_mask
    from core.scan_plan import computable_features

    return enabled_mask([f.key for f in computable_features()])


def already_analyzed(path: str) -> bool:
    with get_conn() as c:
        return (
            c.execute(
                "SELECT 1 FROM tracks WHERE path=? AND genres IS NOT NULL", (path,)
            ).fetchone()
            is not None
        )


def get_all_analyzed_paths() -> set[str]:
    """Paths analysed for every feature currently switched on for computing."""
    from core.scan_plan import stale_paths

    stale = stale_paths(_wanted_for_count())
    with get_conn() as c:
        rows = c.execute("SELECT path FROM tracks").fetchall()
    return {r["path"] for r in rows} - stale


def seed_computed_masks(conn=None) -> int:
    """Fill in the computed mask for tracks analysed before it existed.

    A feature counts as computed when its column holds a value. That is the
    same rule the old scan used, so an existing library keeps its work
    instead of re-running CLAP on every track after an upgrade.

    Only rows with no mask at all are touched, so this is safe to run on
    every start and never undoes what a scan wrote.
    """
    from core.feature_bits import FEATURE_BITS
    from core.features import BY_KEY

    # column → bit, for the features that live in a column of their own.
    by_column: dict[str, int] = {}
    for key, bit in FEATURE_BITS.items():
        spec = BY_KEY.get(key)
        if spec is not None and spec.column:
            by_column[spec.column] = bit

    def _run(c) -> int:
        have = {row[1] for row in c.execute("PRAGMA table_info(tracks)")}
        usable = {col: bit for col, bit in by_column.items() if col in have}
        if not usable:
            return 0
        # One UPDATE: each column contributes its bit where it is not null.
        terms = " + ".join(
            f"(CASE WHEN {col} IS NOT NULL THEN {1 << bit} ELSE 0 END)"
            for col, bit in sorted(usable.items())
        )
        cur = c.execute(f"UPDATE tracks SET computed = ({terms}) WHERE computed IS NULL")
        c.commit()
        return cur.rowcount or 0

    if conn is not None:
        return _run(conn)
    with get_conn() as c:
        return _run(c)


def repair_computed_masks(conn=None) -> int:
    """Clear bits that claim a feature is computed while its column is empty.

    A feature can be produced by the analysis and still never reach the
    database if it is missing from :func:`upsert_track`'s signature — the
    mask records it, the column stays NULL, and nothing ever re-runs it
    because the mask says it is done. A test now guards that, and this
    repairs the libraries written before the test existed.

    Returns how many tracks were repaired. One statement for every feature
    together: this runs on every start, before the window exists, and a
    statement per feature was a pass over the whole table for each.
    """
    from core.feature_bits import FEATURE_BITS
    from core.features import BY_KEY

    def _run(c) -> int:
        have = {row[1] for row in c.execute("PRAGMA table_info(tracks)")}
        # For each feature, its bit where the column is empty. Or-ed
        # together, the bits this track has no business claiming.
        empty = " | ".join(
            f"(CASE WHEN {spec.column} IS NULL THEN {1 << bit} ELSE 0 END)"
            for key, bit in sorted(FEATURE_BITS.items())
            if (spec := BY_KEY.get(key)) is not None and spec.column and spec.column in have
        )
        if not empty:
            return 0
        cur = c.execute(
            f"UPDATE tracks SET computed = computed & ~({empty}) "
            f"WHERE computed IS NOT NULL AND computed & ({empty}) != 0"
        )
        c.commit()
        return cur.rowcount or 0

    if conn is not None:
        return _run(conn)
    with get_conn() as c:
        return _run(c)


def get_up_to_date_paths(
    required_columns: list[str], required_models: Iterable[str] = ()
) -> set[str]:
    """Paths at the current version that have every required column *and* every
    required embedding model.

    A track only counts as "up to date" when all features for the *currently
    enabled* compute groups are present. Enabling a new group — or a new
    embedding model — therefore makes previously-skipped tracks eligible for a
    backfill scan, without forcing a full re-analysis of everything.
    """
    # Column and model names come from the trusted registry, never user input;
    # still validate to keep the f-string injection-safe.
    safe = [c for c in required_columns if c.replace("_", "").isalnum()]
    clauses = [f"{c} IS NOT NULL" for c in safe]
    params: list = []
    for model in required_models:
        if not str(model).replace("_", "").isalnum():
            continue
        clauses.append("path IN (SELECT path FROM track_embeddings WHERE model = ?)")
        params.append(model)
    clause = " AND ".join(clauses) if clauses else "1=1"
    with get_conn() as c:
        rows = c.execute(
            f"SELECT path FROM tracks WHERE {clause}",
            params,
        ).fetchall()
    return {r["path"] for r in rows}


def list_all_tracks_for_browsing() -> list:
    """All analysed tracks sorted by rating desc, then most-recently-analysed first.

    Used as the fallback library view when no track is loaded in Traktor — gives
    the user something useful to scroll through (their favourites surface first).
    """
    with get_conn() as c:
        rows = c.execute(
            "SELECT * FROM tracks "
            "ORDER BY COALESCE(rating, 0) DESC, "
            "         analyzed_at DESC, "
            "         COALESCE(artist, '') ASC, "
            "         COALESCE(title, '') ASC"
        ).fetchall()
    return rows


def get_track_count() -> tuple[int, int]:
    """Return (total_tracks, up_to_date_count)."""
    with get_conn() as c:
        total = c.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]
        want = _wanted_for_count()
        current = (
            c.execute(
                "SELECT COUNT(*) FROM tracks WHERE computed & ? = ?", (want, want)
            ).fetchone()[0]
            if want
            else total
        )
    return total, current


@dataclass(frozen=True)
class AnalysisGaps:
    """How much of the library is analysed, and for what it is not."""

    total: int
    complete: int
    #: Compute group → tracks missing at least one of its wanted features.
    missing: dict[str, int]
    #: Of the tracks missing structure/bassline, those with no BPM — which a
    #: scan cannot fix: every one of those features needs the tempo.
    shape_without_bpm: int


def analysis_gaps() -> AnalysisGaps:
    """Per compute group, how many tracks still lack something."""
    from core.feature_bits import FEATURE_BITS
    from core.features import BY_KEY

    want = _wanted_for_count()
    group_masks: dict[str, int] = {}
    for key, bit in FEATURE_BITS.items():
        spec = BY_KEY.get(key)
        if spec is not None and want & (1 << bit):
            group_masks[spec.group] = group_masks.get(spec.group, 0) | (1 << bit)

    lacks = "(computed IS NULL OR computed & ? != ?)"
    with get_conn() as c:
        total = c.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]
        complete = (
            total
            - c.execute(f"SELECT COUNT(*) FROM tracks WHERE {lacks}", (want, want)).fetchone()[0]
            if want
            else total
        )
        missing = {}
        for group, mask in group_masks.items():
            n = c.execute(f"SELECT COUNT(*) FROM tracks WHERE {lacks}", (mask, mask)).fetchone()[0]
            if n:
                missing[group] = n
        shape_mask = group_masks.get("shape", 0)
        no_bpm = (
            c.execute(
                f"SELECT COUNT(*) FROM tracks WHERE {lacks} AND COALESCE(bpm, 0) <= 0",
                (shape_mask, shape_mask),
            ).fetchone()[0]
            if shape_mask
            else 0
        )
    return AnalysisGaps(total=total, complete=complete, missing=missing, shape_without_bpm=no_bpm)


# ── Per-model embedding vectors (track_embeddings side table) ───────────────


def upsert_embedding(path: str, model: str, vector: bytes, dim: int) -> None:
    """Store one model's vector for one track (replaces any existing row)."""
    with get_conn() as c:
        c.execute(
            "INSERT INTO track_embeddings (path, model, vector, dim) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(path, model) DO UPDATE SET "
            "vector=excluded.vector, dim=excluded.dim",
            (path, model, vector, dim),
        )
        c.commit()


def get_embeddings(model: str, paths: list[str]) -> dict[str, np.ndarray]:
    """Return ``{path: vector}`` for the given paths under one model.

    Chunks the IN-list to stay under SQLite's bound-variable limit.
    """
    if not paths:
        return {}
    out: dict[str, np.ndarray] = {}
    with get_conn() as c:
        for i in range(0, len(paths), 900):
            chunk = paths[i : i + 900]
            placeholders = ",".join("?" * len(chunk))
            rows = c.execute(
                f"SELECT path, vector FROM track_embeddings "
                f"WHERE model = ? AND path IN ({placeholders})",
                (model, *chunk),
            ).fetchall()
            for r in rows:
                v = np.frombuffer(r["vector"], dtype=np.float32)
                if v.size:
                    out[r["path"]] = v
    return out


def upsert_waveform(
    path: str, fingerprint: str, version: int, n_preview: int, n_detail: int, blob: bytes
) -> None:
    """Store one track's computed export waveform (replaces any existing row)."""
    with get_conn() as c:
        c.execute(
            "INSERT INTO track_waveforms "
            "(path, fingerprint, version, n_preview, n_detail, blob) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(path) DO UPDATE SET "
            "fingerprint=excluded.fingerprint, version=excluded.version, "
            "n_preview=excluded.n_preview, n_detail=excluded.n_detail, "
            "blob=excluded.blob, stored_at=datetime('now')",
            (path, fingerprint, version, n_preview, n_detail, blob),
        )
        c.commit()


def get_waveforms(
    paths: list[str],
    version: int,
) -> dict[str, tuple[str, int, int, bytes]]:
    """``{path: (fingerprint, n_preview, n_detail, blob)}`` for the given paths.

    Rows from a superseded analysis are filtered out in SQL rather than read
    and discarded — after a version bump that is every row in the table.
    Chunks the IN-list to stay under SQLite's bound-variable limit.
    """
    if not paths:
        return {}
    out: dict[str, tuple[str, int, int, bytes]] = {}
    with get_conn() as c:
        for i in range(0, len(paths), 900):
            chunk = paths[i : i + 900]
            placeholders = ",".join("?" * len(chunk))
            rows = c.execute(
                f"SELECT path, fingerprint, n_preview, n_detail, blob "
                f"FROM track_waveforms "
                f"WHERE version = ? AND path IN ({placeholders})",
                (version, *chunk),
            ).fetchall()
            for r in rows:
                out[r["path"]] = (r["fingerprint"], r["n_preview"], r["n_detail"], bytes(r["blob"]))
    return out


def _delete_embeddings(conn: sqlite3.Connection, paths: Iterable[str]) -> None:
    """Delete all embedding rows for the given paths on an open connection."""
    conn.executemany("DELETE FROM track_embeddings WHERE path = ?", [(p,) for p in paths])


def _delete_waveforms(conn: sqlite3.Connection, paths: Iterable[str]) -> None:
    """Delete cached export waveforms for the given paths, on an open
    connection. These are the largest rows the database holds, so a pruned
    track must take its waveform with it."""
    conn.executemany("DELETE FROM track_waveforms WHERE path = ?", [(p,) for p in paths])


def _delete_spotify(conn: sqlite3.Connection, paths: Iterable[str]) -> None:
    """Delete Spotify metadata for the given paths, on an open connection."""
    conn.executemany("DELETE FROM track_spotify WHERE path = ?", [(p,) for p in paths])
    conn.executemany("DELETE FROM spotify_tried WHERE path = ?", [(p,) for p in paths])


def _delete_discogs(conn: sqlite3.Connection, paths: Iterable[str]) -> None:
    """Delete Discogs lookups for the given paths, on an open connection."""
    conn.executemany("DELETE FROM track_discogs WHERE path = ?", [(p,) for p in paths])


def remove_track(path: str) -> None:
    with get_conn() as c:
        c.execute("DELETE FROM tracks WHERE path = ?", (path,))
        _delete_embeddings(c, [path])
        _delete_waveforms(c, [path])
        _delete_discogs(c, [path])
        _delete_spotify(c, [path])
        c.commit()


def remove_missing_tracks() -> int:
    """Delete DB entries whose files no longer exist. Returns count removed."""
    with get_conn() as c:
        rows = c.execute("SELECT path FROM tracks").fetchall()
    missing = [r["path"] for r in rows if not Path(r["path"]).exists()]
    if missing:
        with get_conn() as c:
            c.executemany("DELETE FROM tracks WHERE path = ?", [(p,) for p in missing])
            _delete_embeddings(c, missing)
            _delete_waveforms(c, missing)
            _delete_discogs(c, missing)
            _delete_spotify(c, missing)
            c.commit()
    return len(missing)


def remove_tracks_not_in(keep_paths: Iterable[str]) -> int:
    """Delete DB entries whose path is not in ``keep_paths`` (the scanned
    collection). Returns the count removed.

    This is how the DB is kept mirrored to Traktor's collection: rows orphaned
    by a folder rename or a removed track (whose audio may still sit on disk, so
    :func:`remove_missing_tracks` can't catch them) are pruned. Guarded against
    an empty ``keep_paths`` — a failed/empty NML parse must never wipe the DB.
    """
    keep = set(keep_paths)
    if not keep:
        return 0
    with get_conn() as c:
        rows = c.execute("SELECT path FROM tracks").fetchall()
        orphans = [r["path"] for r in rows if r["path"] not in keep]
        if orphans:
            c.executemany("DELETE FROM tracks WHERE path = ?", [(p,) for p in orphans])
            _delete_embeddings(c, orphans)
            _delete_waveforms(c, orphans)
            _delete_discogs(c, orphans)
            _delete_spotify(c, orphans)
            c.commit()
    return len(orphans)
