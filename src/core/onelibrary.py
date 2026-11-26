"""Write the OneLibrary database — ``PIONEER/rekordbox/exportLibrary.db``.

OneLibrary (AlphaTheta, formerly *Device Library Plus*) is the library format
the CDJ-3000X, XDJ-AZ, OPUS-QUAD and OMNIS-DUO read. Those players do **not**
fall back to the legacy ``export.pdb``: a stick without this file shows
"Rekordbox Device Library Plus not found". Older players — CDJ-3000, XDJ-RX3,
XDJ-XZ, NXS2 and everything before — ignore it and read ``export.pdb``, so a
stick that carries both works everywhere.

The file is a SQLite database encrypted with SQLCipher 4 at its default
settings (AES-256-CBC, 4096-byte pages, PBKDF2-HMAC-SHA512 × 256 000, per-page
HMAC-SHA512), keyed with one passphrase shared by every rekordbox install.
Rekordbox does not put cues, beat grids or waveforms in it: those stay in the
ANLZ files, which the rows point at. So the database is written **from the
finished ``export.pdb``**, after the audio and analysis are on the stick, and
mirrors it row for row — which is also what rekordbox's own *Convert from
Device Library* does, and what AlphaTheta's export guide asks for (both
libraries on a stick must list the same tracks and playlists).

Schema and the fixed browse-menu rows were captured from a rekordbox 7 export
by the MIT-licensed `onelibrary <https://github.com/v1vendi/onelibrary>`_
project (``spec/schema.sql``, ``defaults.py``); the DDL is reproduced verbatim,
typos included, because a player may refuse a database whose columns differ.
A test pins :data:`SCHEMA` against the captured copy in ``tests/unit/data``.

**Not confirmed on a OneLibrary player yet** — nobody outside AlphaTheta and
its partners has published a hardware result for a third-party
``exportLibrary.db``. The strongest check available without one has been done:
rekordbox 7.2.18 opened an 874-track stick written here, listed it under its
OneLibrary node, and its own *Convert from Device Library* — run on that very
file — changed nothing but the My Tag list, where it adds its library's
default tags (a Traktor collection has none). Two earlier differences it
corrected are reproduced below: colour 8 is ``Purple``, not ``Dark`` as the
onelibrary capture had it, and ``property.deviceName`` is empty.
"""

from __future__ import annotations

import logging
import shutil
import tempfile
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from core.atomic_write import replacing

log = logging.getLogger(__name__)

DB_NAME = "exportLibrary.db"

#: The SQLCipher passphrase rekordbox keys every ``exportLibrary.db`` with.
#: Identical across installations and versions — the players have to open
#: every stick ever exported — and published by the reverse-engineering
#: community (pyrekordbox, onelibrary, DjManager all carry the same value).
#: Supplied to SQLCipher as a passphrase, *not* as a raw hex key.
PASSPHRASE = "r8gddnr4k847830ar6cqzbkk0el6qytmb3trbbx805jm74vez64i5o8fnrqryqls"

#: ``property.dbVersion`` as written by rekordbox 7.
DB_VERSION = "1000"

#: ``content.analysedBits`` on every fully analysed track in a rekordbox
#: export. Per-bit meaning unknown; the value is what the players see.
ANALYSED_BITS = 41

#: ``content.contentLink`` — constant on every row of a rekordbox export
#: (reported by DjManager from a real capture). Meaning unknown.
CONTENT_LINK = 0x000C0700

#: ``content.fileType`` by extension. The enum is rekordbox's own
#: (``djmdContent.FileType`` in master.db, documented by pyrekordbox); ``1``
#: for MP3 was also observed in a OneLibrary export.
FILE_TYPES = {
    ".mp3": 1,
    ".m4a": 4,
    ".aac": 4,
    ".mp4": 4,
    ".flac": 5,
    ".wav": 11,
    ".aif": 12,
    ".aiff": 12,
}

#: The DDL of a rekordbox 7 ``exportLibrary.db`` — 22 tables and 4 indexes,
#: in declaration order, verbatim. ``album.isComplation`` and
#: ``cue.OutFileOffsetInBlock`` are rekordbox's spellings; keep them.
SCHEMA: tuple[str, ...] = (
    "CREATE TABLE content(content_id integer primary key, title varchar, "
    "titleForSearch varchar, subtitle varchar, bpmx100 integer, length integer, "
    "trackNo integer, discNo integer, artist_id_artist integer, "
    "artist_id_remixer integer, artist_id_originalArtist integer, "
    "artist_id_composer integer, artist_id_lyricist integer, album_id integer, "
    "genre_id integer, label_id integer, key_id integer, color_id integer, "
    "image_id integer, djComment varchar, rating integer, releaseYear integer, "
    "releaseDate varchar, dateCreated varchar, dateAdded varchar, path varchar, "
    "fileName varchar, fileSize integer, fileType integer, bitrate integer, "
    "bitDepth integer, samplingRate integer, isrc varchar, djPlayCount integer, "
    "isHotCueAutoLoadOn integer, isKuvoDeliverStatusOn integer, "
    "kuvoDeliveryComment varchar, masterDbId integer, masterContentId integer, "
    "analysisDataFilePath varchar, analysedBits integer, contentLink integer, "
    "hasModified integer, cueUpdateCount integer, "
    "analysisDataUpdateCount integer, informationUpdateCount integer)",
    "CREATE TABLE genre(genre_id integer primary key, name varchar)",
    "CREATE TABLE artist(artist_id integer primary key, name varchar, nameForSearch varchar)",
    "CREATE TABLE album(album_id integer primary key, name varchar, "
    "artist_id integer, image_id integer, isComplation integer, "
    "nameForSearch varchar)",
    "CREATE TABLE label(label_id integer primary key, name varchar)",
    "CREATE TABLE key(key_id integer primary key, name varchar)",
    "CREATE TABLE color(color_id integer primary key, name varchar)",
    "CREATE TABLE playlist(playlist_id integer primary key, sequenceNo integer, "
    "name varchar, image_id integer, attribute integer, "
    "playlist_id_parent integer)",
    "CREATE TABLE playlist_content(playlist_id integer, content_id integer, sequenceNo integer)",
    "CREATE TABLE hotCueBankList(hotCueBankList_id integer primary key, "
    "sequenceNo integer, name varchar, image_id integer, attribute integer, "
    "hotCueBankList_id_parent integer)",
    "CREATE TABLE hotCueBankList_cue(hotCueBankList_id integer, cue_id integer, "
    "sequenceNo integer)",
    "CREATE TABLE history(history_id integer primary key, sequenceNo integer, "
    "name varchar, attribute integer, history_id_parent integer)",
    "CREATE TABLE history_content(history_id integer, content_id integer, sequenceNo integer)",
    "CREATE TABLE image(image_id integer primary key, path varchar)",
    "CREATE TABLE cue(cue_id integer primary key, content_id integer, "
    "kind integer, colorTableIndex integer, cueComment varchar, "
    "isActiveLoop integer, beatLoopNumerator integer, "
    "beatLoopDenominator integer, inUsec integer, outUsec integer, "
    "in150FramePerSec integer, out150FramePerSec integer, "
    "inMpegFrameNumber integer, outMpegFrameNumber integer, inMpegAbs integer, "
    "outMpegAbs integer, inDecodingStartFramePosition integer, "
    "outDecodingStartFramePosition integer, inFileOffsetInBlock integer, "
    "OutFileOffsetInBlock integer, inNumberOfSampleInBlock integer, "
    "outNumberOfSampleInBlock integer)",
    "CREATE TABLE menuItem(menuItem_id integer primary key, kind integer, name varchar)",
    "CREATE TABLE category(category_id integer primary key, menuItem_id integer, "
    "sequenceNo integer, isVisible integer)",
    "CREATE TABLE sort(sort_id integer primary key, menuItem_id integer, "
    "sequenceNo integer, isVisible integer, isSelectedAsSubColumn integer)",
    "CREATE TABLE property(deviceName varchar, dbVersion varchar, "
    "numberOfContents integer, createdDate varchar, backGroundColorType integer, "
    "myTagMasterDBID integer)",
    "CREATE TABLE recommendedLike(content_id_1 integer, content_id_2 integer, "
    "rating integer, createdDate integer)",
    "CREATE TABLE myTag(myTag_id integer primary key, sequenceNo integer, "
    "name varchar, attribute integer, myTag_id_parent integer)",
    "CREATE TABLE myTag_content(myTag_id integer, content_id integer)",
    "CREATE INDEX index_hotCueBankList_cue_hotCueBankList_id on "
    "hotCueBankList_cue(hotCueBankList_id)",
    "CREATE INDEX index_playlist_content_playlist_id on playlist_content(playlist_id)",
    "CREATE INDEX index_myTag_content_myTag_id on myTag_content(myTag_id)",
    "CREATE INDEX index_myTag_content_content_id on myTag_content(content_id)",
)

# ── Fixed rows ──────────────────────────────────────────────────────────────
#
# Browse configuration and the colour palette: present in every rekordbox
# export regardless of content, captured verbatim. ``menuItem.name`` is wrapped
# in U+FFFA/U+FFFB, Unicode's interlinear-annotation anchor and terminator —
# rekordbox's marker for "the player localises this". Strip them and the
# player shows this ASCII instead of its own translation.

_A, _T = "￺", "￻"

#: (menuItem_id, kind, name)
MENU_ITEMS: tuple[tuple[int, int, str], ...] = tuple(
    (i, kind, f"{_A}{name}{_T}")
    for i, kind, name in (
        (1, 128, "GENRE"),
        (2, 129, "ARTIST"),
        (3, 130, "ALBUM"),
        (4, 131, "TRACK"),
        (5, 133, "BPM"),
        (6, 134, "RATING"),
        (7, 135, "YEAR"),
        (8, 136, "REMIXER"),
        (9, 137, "LABEL"),
        (10, 138, "ORIGINAL ARTIST"),
        (11, 139, "KEY"),
        (12, 141, "CUE"),
        (13, 142, "COLOR"),
        (14, 146, "TIME"),
        (15, 147, "BITRATE"),
        (16, 148, "FILE NAME"),
        (17, 132, "PLAYLIST"),
        (18, 152, "HOT CUE BANK"),
        (19, 149, "HISTORY"),
        (20, 145, "SEARCH"),
        (21, 150, "COMMENTS"),
        (22, 140, "DATE ADDED"),
        (23, 151, "DJ PLAY COUNT"),
        (24, 144, "FOLDER"),
        (25, 161, "DEFAULT"),
        (26, 162, "ALPHABET"),
        (27, 170, "MATCHING"),
    )
)

#: (category_id, menuItem_id, sequenceNo, isVisible)
CATEGORIES: tuple[tuple[int, int, int, int], ...] = (
    (1, 1, 0, 0),
    (2, 2, 1, 1),
    (3, 3, 2, 1),
    (4, 4, 3, 1),
    (5, 17, 5, 1),
    (6, 5, 0, 0),
    (7, 6, 0, 0),
    (8, 7, 0, 0),
    (9, 8, 0, 0),
    (10, 9, 0, 0),
    (11, 10, 0, 0),
    (12, 11, 4, 1),
    (15, 13, 0, 0),
    (17, 24, 9, 1),
    (18, 20, 7, 1),
    (19, 14, 0, 0),
    (20, 15, 0, 0),
    (21, 16, 0, 0),
    (22, 19, 6, 1),
    (23, 18, 0, 0),
    (26, 27, 8, 1),
    (27, 22, 10, 1),
)

#: (sort_id, menuItem_id, sequenceNo, isVisible, isSelectedAsSubColumn)
SORT_OPTIONS: tuple[tuple[int, int, int, int, int], ...] = (
    (0, 25, 1, 1, 0),
    (1, 26, 2, 1, 0),
    (2, 2, 3, 1, 0),
    (3, 3, 4, 1, 0),
    (4, 5, 5, 1, 0),
    (5, 6, 6, 1, 0),
    (6, 1, 0, 0, 0),
    (7, 21, 0, 0, 0),
    (8, 14, 0, 0, 0),
    (9, 8, 0, 0, 0),
    (10, 9, 0, 0, 0),
    (11, 10, 0, 0, 0),
    (12, 11, 7, 1, 0),
    (13, 15, 0, 0, 0),
    (15, 13, 0, 0, 0),
    (16, 23, 0, 0, 0),
    (17, 22, 0, 0, 0),
)

#: The eight rekordbox track colours — always all present. Names as
#: rekordbox 7.2.18 wrote them when it converted one of our sticks.
COLORS: tuple[tuple[int, str], ...] = (
    (1, "Pink"),
    (2, "Red"),
    (3, "Orange"),
    (4, "Yellow"),
    (5, "Green"),
    (6, "Aqua"),
    (7, "Blue"),
    (8, "Purple"),
)

#: The four My Tag folders rekordbox creates by default: (id, sequenceNo, name).
#: Rekordbox also lists its library's tags under them (24 in a stock install,
#: with library-scoped ids); a Traktor collection has none, so only the
#: folders are written.
MYTAG_ROOTS: tuple[tuple[int, int, str], ...] = (
    (1, 0, "Genre"),
    (2, 1, "Components"),
    (3, 2, "Situation"),
    (4, 3, "Untitled Column"),
)

#: ``attribute`` in the tree tables (playlist, myTag): a folder or a leaf.
FOLDER, LEAF = 1, 0


# ── What gets written ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class Content:
    """One ``content`` row, as read from ``export.pdb``."""

    id: int
    title: str
    path: str
    filename: str
    tempo: int  # BPM × 100
    duration: int  # seconds
    artist_id: int
    album_id: int
    genre_id: int
    label_id: int
    key_id: int
    color_id: int
    remixer_id: int
    original_artist_id: int
    composer_id: int
    rating: int
    year: int
    track_number: int
    disc_number: int
    comment: str
    date_added: str
    release_date: str
    file_size: int
    bitrate: int
    sample_rate: int
    sample_depth: int
    analyze_path: str


@dataclass(frozen=True)
class Node:
    """A playlist or a playlist folder."""

    id: int
    parent_id: int
    order: int
    name: str
    is_folder: bool


@dataclass
class Library:
    """Everything the database says, gathered from ``export.pdb``."""

    tracks: list[Content] = field(default_factory=list)
    artists: dict[int, str] = field(default_factory=dict)
    albums: dict[int, tuple[str, int]] = field(default_factory=dict)
    """id -> (name, artist_id)."""
    genres: dict[int, str] = field(default_factory=dict)
    labels: dict[int, str] = field(default_factory=dict)
    keys: dict[int, str] = field(default_factory=dict)
    playlists: list[Node] = field(default_factory=list)
    entries: dict[int, list[int]] = field(default_factory=dict)
    """playlist id -> content ids, in playlist order."""


def read_pdb(pdb: Path) -> Library:
    """Everything ``export.pdb`` holds that the OneLibrary database repeats.

    Lookup tables keep only the rows a track references: that is what
    rekordbox writes, and a dangling name would be a row players never show.
    """
    from rekordbox_pdb import Database

    db = Database.from_file(str(pdb))
    lib = Library()
    for r in db.tracks:
        if not r.file_path:
            continue
        lib.tracks.append(
            Content(
                id=r.id,
                title=r.title,
                path=r.file_path,
                filename=r.filename or Path(r.file_path).name,
                tempo=r.tempo,
                duration=r.duration,
                artist_id=r.artist_id,
                album_id=r.album_id,
                genre_id=r.genre_id,
                label_id=r.label_id,
                key_id=r.key_id,
                color_id=r.color_id,
                remixer_id=r.remixer_id,
                original_artist_id=r.original_artist_id,
                composer_id=r.composer_id,
                rating=r.rating,
                year=r.year,
                track_number=r.track_number,
                disc_number=r.disc_number,
                comment=r.comment,
                date_added=r.date_added,
                release_date=r.release_date,
                file_size=r.file_size,
                bitrate=r.bitrate,
                sample_rate=r.sample_rate,
                sample_depth=r.sample_depth,
                analyze_path=r.analyze_path,
            )
        )

    used_artists = {
        i
        for t in lib.tracks
        for i in (t.artist_id, t.remixer_id, t.original_artist_id, t.composer_id)
    }
    used = {
        "album": {t.album_id for t in lib.tracks},
        "genre": {t.genre_id for t in lib.tracks},
        "label": {t.label_id for t in lib.tracks},
        "key": {t.key_id for t in lib.tracks},
    }
    lib.albums = {a.id: (a.name, a.artist_id) for a in db.albums if a.id in used["album"]}
    used_artists |= {artist for _name, artist in lib.albums.values()}
    lib.artists = {a.id: a.name for a in db.artists if a.id in used_artists}
    lib.genres = {g.id: g.name for g in db.genres if g.id in used["genre"]}
    lib.labels = {x.id: x.name for x in db.labels if x.id in used["label"]}
    lib.keys = {k.id: k.name for k in db.keys if k.id in used["key"]}

    lib.playlists = [
        Node(p.id, p.parent_id, p.sort_order, p.name, p.is_folder) for p in db.playlist_tree
    ]
    known = {t.id for t in lib.tracks}
    for e in sorted(db.playlist_entries, key=lambda e: (e.playlist_id, e.entry_index)):
        if e.track_id in known:
            lib.entries.setdefault(e.playlist_id, []).append(e.track_id)
    return lib


# ── Writing ─────────────────────────────────────────────────────────────────


def _connect(path: Path, passphrase: str = PASSPHRASE):
    import sqlcipher3

    conn = sqlcipher3.connect(str(path))
    # PRAGMA takes no bound parameters; quote as a SQL string literal.
    conn.execute("PRAGMA key = '" + passphrase.replace("'", "''") + "'")
    return conn


def open_readonly(path: Path, passphrase: str = PASSPHRASE):
    """A connection to an existing database, with rows addressable by name.

    Raises ``sqlcipher3.DatabaseError`` when the passphrase does not decrypt
    it — SQLCipher only notices on the first read, so one is forced here.
    """
    import sqlcipher3

    conn = sqlcipher3.connect(f"file:{path}?mode=ro", uri=True)
    conn.execute("PRAGMA key = '" + passphrase.replace("'", "''") + "'")
    conn.row_factory = sqlcipher3.Row
    conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
    return conn


def _for_search(name: str) -> str:
    """The ``*ForSearch`` twin of a name: accents dropped, upper-cased."""
    folded = unicodedata.normalize("NFKD", name)
    return "".join(c for c in folded if not unicodedata.combining(c)).upper()


def _or_none(value: int) -> int | None:
    return value or None


def _write_fixed_rows(conn) -> None:
    conn.executemany("INSERT INTO menuItem VALUES (?,?,?)", MENU_ITEMS)
    conn.executemany("INSERT INTO category VALUES (?,?,?,?)", CATEGORIES)
    conn.executemany("INSERT INTO sort VALUES (?,?,?,?,?)", SORT_OPTIONS)
    conn.executemany("INSERT INTO color VALUES (?,?)", COLORS)
    conn.executemany(
        "INSERT INTO myTag (myTag_id, sequenceNo, name, attribute, myTag_id_parent) "
        "VALUES (?,?,?,?,0)",
        [(i, seq, name, FOLDER) for i, seq, name in MYTAG_ROOTS],
    )


def _write_lookups(conn, lib: Library) -> None:
    conn.executemany(
        "INSERT INTO artist VALUES (?,?,?)",
        [(i, n, _for_search(n)) for i, n in lib.artists.items()],
    )
    conn.executemany(
        "INSERT INTO album VALUES (?,?,?,NULL,0,?)",
        [(i, n, _or_none(artist), _for_search(n)) for i, (n, artist) in lib.albums.items()],
    )
    conn.executemany("INSERT INTO genre VALUES (?,?)", list(lib.genres.items()))
    conn.executemany("INSERT INTO label VALUES (?,?)", list(lib.labels.items()))
    conn.executemany("INSERT INTO key VALUES (?,?)", list(lib.keys.items()))


def _content_row(t: Content) -> tuple:
    return (
        t.id,
        t.title,
        _for_search(t.title),
        t.tempo,
        t.duration,
        _or_none(t.track_number),
        _or_none(t.disc_number),
        _or_none(t.artist_id),
        _or_none(t.remixer_id),
        _or_none(t.original_artist_id),
        _or_none(t.composer_id),
        _or_none(t.album_id),
        _or_none(t.genre_id),
        _or_none(t.label_id),
        _or_none(t.key_id),
        _or_none(t.color_id),
        t.comment or None,
        t.rating,
        _or_none(t.year),
        t.release_date or None,
        t.date_added or None,
        t.path,
        t.filename,
        t.file_size,
        FILE_TYPES.get(Path(t.path).suffix.lower()),
        t.bitrate,
        _or_none(t.sample_depth),
        t.sample_rate,
        t.analyze_path or None,
        ANALYSED_BITS if t.analyze_path else None,
        CONTENT_LINK,
    )


def _write_content(conn, lib: Library) -> None:
    conn.executemany(
        "INSERT INTO content (content_id, title, titleForSearch, bpmx100, length, "
        "trackNo, discNo, artist_id_artist, artist_id_remixer, "
        "artist_id_originalArtist, artist_id_composer, album_id, genre_id, "
        "label_id, key_id, color_id, djComment, rating, releaseYear, releaseDate, "
        "dateAdded, path, fileName, fileSize, fileType, bitrate, bitDepth, "
        "samplingRate, analysisDataFilePath, analysedBits, contentLink) "
        "VALUES (" + ",".join("?" * 31) + ")",
        [_content_row(t) for t in lib.tracks],
    )


def _write_playlists(conn, lib: Library) -> None:
    conn.executemany(
        "INSERT INTO playlist (playlist_id, sequenceNo, name, image_id, attribute, "
        "playlist_id_parent) VALUES (?,?,?,NULL,?,?)",
        [
            (p.id, p.order, p.name, FOLDER if p.is_folder else LEAF, p.parent_id)
            for p in lib.playlists
        ],
    )
    conn.executemany(
        "INSERT INTO playlist_content VALUES (?,?,?)",
        [(pid, cid, n) for pid, cids in lib.entries.items() for n, cid in enumerate(cids, start=1)],
    )


def _write_property(conn, lib: Library, created: date) -> None:
    # deviceName is left empty: that is what rekordbox itself writes when it
    # converts a stick. myTagMasterDBID identifies the rekordbox library the
    # My Tags came from; there is none here.
    conn.execute(
        "INSERT INTO property VALUES ('',?,?,?,0,0)",
        (DB_VERSION, len(lib.tracks), created.isoformat()),
    )


def _build(path: Path, lib: Library, created: date, passphrase: str) -> None:
    """The whole database at *path*, which should be on a fast local disk."""
    conn = _connect(path, passphrase)
    try:
        conn.execute("PRAGMA journal_mode = DELETE")
        for statement in SCHEMA:
            conn.execute(statement)
        _write_fixed_rows(conn)
        _write_lookups(conn, lib)
        _write_content(conn, lib)
        _write_playlists(conn, lib)
        _write_property(conn, lib, created)
        conn.commit()
    finally:
        conn.close()


def write(
    lib: Library, out: Path, *, created: date | None = None, passphrase: str = PASSPHRASE
) -> Path:
    """Write *lib* as a fresh ``exportLibrary.db`` at *out*.

    Built on the computer's own disk and copied over in one go: SQLite on a
    USB stick pays a journal create, sync and delete for every commit, which
    made this the slowest step of an export. The copy lands in a sibling temp
    file and is moved into place, so a failure half-way never leaves a
    truncated database a player would choke on. Any journal left from an
    earlier writer is removed too: rekordbox itself leaves most of a fresh
    export in a ``-wal`` file, and a stale one beside a new database would be
    read as part of it.
    """
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    for stale in (out.with_name(out.name + "-wal"), out.with_name(out.name + "-shm")):
        if stale.exists():
            stale.unlink()

    with tempfile.TemporaryDirectory(prefix="onelibrary-") as work:
        built = Path(work) / out.name
        _build(built, lib, created or date.today(), passphrase)
        with replacing(out) as part, built.open("rb") as src, part.open("wb") as dst:
            shutil.copyfileobj(src, dst, 1 << 20)
    log.info("wrote %s: %d track(s), %d playlist node(s)", out, len(lib.tracks), len(lib.playlists))
    return out


def write_from_pdb(pdb: Path, out: Path, *, created: date | None = None) -> Path:
    """The OneLibrary twin of a finished ``export.pdb``. Returns *out*."""
    return write(read_pdb(Path(pdb)), Path(out), created=created)
