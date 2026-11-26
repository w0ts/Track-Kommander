"""Write a Pioneer/AlphaTheta device export — a USB stick CDJs can read.

Produces the tree a player expects::

    Contents/<audio files>
    PIONEER/rekordbox/export.pdb          the track/playlist database
    PIONEER/rekordbox/exportLibrary.db    the same, in OneLibrary form
    PIONEER/*SETTING*.DAT                 player settings (core.my_settings)
    PIONEER/USBANLZ/Pxxx/xxxxxxxx/ANLZ0000.DAT   beat grid, cues A–C, waveform
                                  /ANLZ0000.EXT   cues D–H, coloured cues

Why this exists rather than the Rekordbox round trip: the round trip needs
Rekordbox installed and a manual import/export step. This writes the stick
directly from the Traktor collection.

Two databases, because the players split in 2023: the CDJ-3000X, XDJ-AZ,
OPUS-QUAD and OMNIS-DUO read only `exportLibrary.db` (OneLibrary, formerly
Device Library Plus); everything before them reads only `export.pdb`. The
OneLibrary one is derived from the finished `export.pdb` — see
:mod:`core.onelibrary` — so the two always list the same tracks.

What it does *not* write
------------------------

* **`.3EX`** — a newer analysis variant players do not require.

The seed database
-----------------

`export.pdb` cannot be created from nothing: the writer library edits an
existing file, and a blank one is 41 pages of real table scaffolding. So a
blank `export.pdb` has to be supplied — see :func:`find_seed`. Generating one
would remove that requirement and is the right long-term fix.

**Hardware:** a full export loads on an XDJ-1000 (``export.pdb`` side, with
waveforms and hot cues). The OneLibrary players (CDJ-3000X and later) are
untested.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import struct
import time
import unicodedata
from collections.abc import Callable, Container, Sequence
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

from rekordbox_pdb.pdb import TableType

from core import anlz, my_settings
from core.anlz_path import anlz_dir
from core.atomic_write import write_bytes_atomic
from core.pdb_pages import fix_row_counts
from core.pdb_writer import AlignedPdbEditor
from core.playlists import PlaylistKey, key_parts

if TYPE_CHECKING:
    from rekordbox_pdb.edit import PdbEditor

    from core.rekordbox import Track
from core.usb_export import (
    AUDIO_DIRNAME,
    Source,
    UsbPlan,
    conforms,
    plan_export,
    run_export,
    strip_macos_metadata,
)

log = logging.getLogger(__name__)

PIONEER_DIR = "PIONEER"
DB_DIR = f"{PIONEER_DIR}/rekordbox"
ANLZ_DIR = f"{PIONEER_DIR}/USBANLZ"

#: Where a cached blank database lives once we've found one.
SEED_NAME = "export-seed.pdb"


@dataclass
class DeviceTrack:
    """One track, with everything the device export needs about it."""

    source: Path
    """Where the audio is now."""

    device_path: str = ""
    """Path as the player will see it, e.g. ``/Contents/Track.mp3``."""

    title: str = ""
    artist: str = ""
    album: str = ""
    genre: str = ""
    key: str = ""
    bpm: float = 0.0
    duration_s: int = 0
    bitrate_kbps: int = 0
    sample_rate: int = 44100
    file_size: int = 0
    rating: int = 0
    """0–5 stars — not Traktor's 0–255 RANKING; see ``rekordbox.stars``."""
    year: int = 0

    first_beat_ms: float = 0.0
    cues: list[anlz.Cue] = field(default_factory=list)
    waveform: list[int] = field(default_factory=list)
    #: The full analysis, when it has been computed. Carries the detail
    #: and colour waveforms the .EXT needs; ``waveform`` above is only
    #: the 400-column overview.
    analysis: anlz.Waveform | None = None

    #: Assigned during export: /PIONEER/USBANLZ/Pxxx/xxxxxxxx/ANLZ0000.DAT
    analyze_path: str = ""


@dataclass
class DeviceReport:
    """What the export did."""

    tracks: int = 0
    copied: int = 0
    missing: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    db_path: Path | None = None
    onelibrary_path: Path | None = None
    """The OneLibrary twin of ``db_path``, once written."""
    sidecars_removed: int = 0
    settings_written: list[str] = field(default_factory=list)
    """Player-settings files the key lacked and now has — see
    :mod:`core.my_settings`."""
    on_key: int = 0
    """Rows in the database once written — everything the key now lists."""
    added: int = 0
    """Tracks the key did not have before this export."""
    replaced: int = 0
    """Tracks the key already had, whose row and analysis were rewritten."""
    kept: int = 0
    """Tracks the key already had that were left exactly as they were."""
    unchanged: int = 0
    """Tracks nothing had changed about, so nothing was written."""
    reused: int = 0
    """Tracks whose cues changed but whose waveform was reused, so the
    audio was never decoded."""

    @property
    def ok(self) -> bool:
        return self.db_path is not None and not self.errors


# ── The seed database ───────────────────────────────────────────────────────

#: The file-type code a track row carries, by extension, as Rekordbox
#: writes it. The writer library's own table has no FLAC and falls back to
#: MP3 (1) for anything it does not know; an XDJ-1000, which cannot decode
#: FLAC, crashed on a key holding a FLAC labelled MP3.
FILE_TYPES = {
    ".mp3": 0x01,
    ".m4a": 0x04,
    ".mp4": 0x04,
    ".flac": 0x05,
    ".wav": 0x0B,
    ".aif": 0x0C,
    ".aiff": 0x0C,
}

#: Offsets inside a TRACKS row: the track id, and the u16 file type.
_ROW_ID = 0x48
_ROW_FILE_TYPE = 0x5A


def _set_file_type(editor: PdbEditor, track_id: int, device_path: str) -> None:
    """Correct the row's file type where the library guessed it wrong."""
    code = FILE_TYPES.get(Path(device_path).suffix.lower())
    if code is None:
        return
    for loc in editor.db.row_locations(TableType.TRACKS):
        if editor._u32(loc + _ROW_ID) == track_id:
            editor._put(loc + _ROW_FILE_TYPE, "<H", code)
            return
    raise ValueError(f"no row for track {track_id} ({device_path})")


def find_seed(cache_dir: Path) -> Path | None:
    """A **blank** ``export.pdb`` to build on, or None.

    Only an empty database will do. Seeding from a populated one silently
    inherits its tracks: an earlier version merely *preferred* the emptiest
    candidate, picked up a stick Rekordbox had already exported 17 tracks to,
    and produced a device library where those 17 phantom entries pointed at
    files that were not there — "the file could not be found" on a player.

    A cached copy is re-validated for the same reason: a cache poisoned once
    stays poisoned.

    One is generated if there is nothing usable (:mod:`core.pdb_seed`), so
    this never returns None in practice — a mounted stick is no longer a
    requirement, only a fallback ahead of generating, since a blank Rekordbox
    wrote is by definition the shape Rekordbox expects.
    """
    cached = Path(cache_dir) / SEED_NAME
    if cached.is_file():
        if seed_is_blank(cached):
            return cached
        log.warning(
            "cached seed %s is not blank (%d tracks) — discarding", cached, _track_count(cached)
        )
        cached.unlink(missing_ok=True)

    for found in scan_for_seed():
        try:
            cached.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(found, cached)
            log.info("cached a blank export.pdb seed from %s", found)
            return cached
        except OSError:
            log.warning("could not cache seed from %s", found, exc_info=True)

    try:
        from core import pdb_seed

        pdb_seed.write(cached)
        if seed_is_blank(cached):
            log.info("no blank export.pdb found — generated one at %s", cached)
            return cached
        log.error("the generated seed did not read back as blank")
        cached.unlink(missing_ok=True)
    except Exception:
        log.warning("could not generate a seed", exc_info=True)
    return None


def scan_for_seed() -> list[Path]:
    """Every **blank** ``export.pdb`` on a mounted volume.

    Populated ones are rejected outright rather than ranked last — building on
    someone else's database is never the right answer, and a stick that has
    been exported to is the common case.
    """
    found: list[Path] = []
    for volumes in (Path("/Volumes"), Path("/media"), Path("/mnt")):
        if not volumes.is_dir():
            continue
        try:
            entries = list(volumes.iterdir())
        except OSError:
            continue
        for vol in entries:
            pdb = vol / DB_DIR / "export.pdb"
            if pdb.is_file() and seed_is_blank(pdb):
                found.append(pdb)
    return found


def _track_count(pdb: Path) -> int:
    try:
        from rekordbox_pdb import Database

        return len(list(Database.from_file(str(pdb)).tracks))
    except Exception:
        return 1 << 30  # unreadable: sort last, never pick it


def seed_is_blank(pdb: Path) -> bool:
    return _track_count(pdb) == 0


# ── Laying out the analysis files ───────────────────────────────────────────


class _Lap:
    """Logs how long each step of an export took, so a slow one says where."""

    def __init__(self) -> None:
        self._t0 = time.monotonic()

    def done(self, what: str, *args: object) -> None:
        now = time.monotonic()
        log.info("export: " + what + " took %.1fs", *args, now - self._t0)
        self._t0 = now


@dataclass
class _Carried:
    """A track already on the key, as much of it as ``add_track`` needs."""

    file_path: str
    analyze_path: str
    fields: dict[str, object]


@dataclass
class _Existing:
    """What a key already holds, read back from its own ``export.pdb``."""

    tracks: dict[str, _Carried] = field(default_factory=dict)
    """By device path."""

    playlists: dict[tuple[str, ...], list[str]] = field(default_factory=dict)
    """Tree position (folders, then name) -> the device paths in it, in order."""

    unreadable: bool = False
    """There is a database and it could not be read. Not the same as an
    empty key: whatever it listed is about to drop out of the library, and
    the caller has to say so rather than report a merge that went fine."""


def _tree_positions(tree: list) -> dict[int, tuple[str, ...]]:
    """Playlist id -> (enclosing folders..., name), for the lists (not the
    folders) in a ``playlist_tree``."""
    nodes = {n.id: n for n in tree}

    def position(n, seen: frozenset[int] = frozenset()) -> tuple[str, ...]:
        parent = nodes.get(n.parent_id)
        # A parent loop in a damaged database must not recurse forever.
        if parent is None or parent.id in seen or not parent.is_folder:
            return (n.name,)
        return (*position(parent, seen | {n.id}), n.name)

    return {n.id: position(n) for n in tree if not n.is_folder}


class _Folders:
    """Folder nodes in a database being written, made the first time a
    playlist needs one, so the tree matches Traktor's."""

    def __init__(self, editor) -> None:
        self._editor = editor
        self._ids: dict[tuple[str, ...], int] = {(): 0}

    def id_for(self, folders: tuple[str, ...]) -> int:
        if folders not in self._ids:
            parent = self.id_for(folders[:-1])
            self._ids[folders] = self._editor.create_playlist(
                folders[-1], parent_id=parent, is_folder=True
            )
        return self._ids[folders]


#: What a database that could not be read is kept as, beside the new one.
UNREADABLE_SUFFIX = ".unreadable"


def _set_aside_unreadable(pdb: Path) -> str:
    """Keep a copy of a database that could not be read; return what to tell
    the user.

    The export goes on and replaces it, so this copy is the only thing left
    of whatever it listed. A copy rather than a move: if the export then
    fails, the key is no worse than it was.
    """
    said = (
        f"{pdb.name} on the key could not be read, so the library was "
        "rebuilt from this export alone. Tracks and playlists only the "
        "old one listed are no longer in it"
    )
    kept = pdb.with_name(pdb.name + UNREADABLE_SUFFIX)
    try:
        shutil.copyfile(pdb, kept)
    except OSError:
        log.warning("could not keep a copy of %s", pdb, exc_info=True)
        return said + "."
    return f"{said}; the old file is kept beside it as {kept.name}."


def _write_analysis(
    tracks: list[tuple[int, DeviceTrack]],
    dest: Path,
    report: DeviceReport,
    should_stop: Callable[[], bool] | None,
    progress: Callable[[int, int, str], None] | None,
) -> set[int]:
    """Write each track's analysis files; return the indices that have them.

    They go in the folder a player derives from the track's path
    (:mod:`core.anlz_path`) — it ignores the database's analyze path. A track
    that fails is reported and left out; one failure must not cost the rest
    of the export, which has already copied its audio.
    """
    written: set[int] = set()
    for count, (index, t) in enumerate(tracks, start=1):
        if should_stop is not None and should_stop():
            break
        if progress is not None:
            progress(count, len(tracks), t.source.name)
        rel = anlz_dir(t.device_path)
        folder = dest / rel
        duration_ms = t.duration_s * 1000.0
        overview = t.waveform or (list(t.analysis.preview) if t.analysis else [])
        try:
            beats = anlz.beats_from_grid(t.bpm, t.first_beat_ms, duration_ms)
            exact = anlz.exact_beat_times(t.bpm, t.first_beat_ms, duration_ms)
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "ANLZ0000.DAT").write_bytes(
                anlz.make_dat(t.device_path, beats, t.cues, overview)
            )
            (folder / "ANLZ0000.EXT").write_bytes(
                anlz.make_ext(
                    t.device_path,
                    t.cues,
                    beats=beats,
                    exact_ms=exact,
                    duration_ms=duration_ms,
                    waveform=t.analysis,
                )
            )
            # Rekordbox 7's browser draws its Preview column from the PWV6 in
            # here; without the file that column stays blank.
            two_ex = anlz.make_2ex(t.device_path, t.analysis)
            if two_ex is not None:
                (folder / "ANLZ0000.2EX").write_bytes(two_ex)
        except (OSError, struct.error, ValueError) as exc:
            # struct.error and ValueError: a value the tags cannot hold.
            report.errors.append(f"{t.source.name}: analysis: {exc}")
            continue
        t.analyze_path = f"/{rel}/ANLZ0000.DAT"
        written.add(index)
    return written


def _read_existing(pdb: Path) -> _Existing:
    """Read a key's current library. Empty if there is nothing readable.

    A corrupt or absent database must not lose the tracks being added, so
    anything unreadable comes back holding nothing and the export falls back
    to building on the blank seed — but marked :attr:`_Existing.unreadable`,
    because a key with a damaged database is not an empty key.
    """
    if not pdb.is_file():
        return _Existing()
    try:
        from rekordbox_pdb import Database

        db = Database.from_file(str(pdb))
        rows = list(db.tracks)
        names = {
            "artist": {a.id: a.name for a in db.artists},
            "album": {a.id: a.name for a in db.albums},
            "genre": {g.id: g.name for g in db.genres},
            "key": {k.id: k.name for k in db.keys},
        }
        tree = list(db.playlist_tree)
        entries = list(db.playlist_entries)
    except Exception:
        log.warning("cannot read %s — starting a fresh library", pdb, exc_info=True)
        return _Existing(unreadable=True)

    out = _Existing()
    by_id: dict[int, str] = {}
    for r in rows:
        if not r.file_path:
            continue
        by_id[r.id] = r.file_path
        out.tracks[r.file_path] = _Carried(
            file_path=r.file_path,
            analyze_path=r.analyze_path,
            fields={
                "title": r.title,
                "filename": r.filename or Path(r.file_path).name,
                "artist": names["artist"].get(r.artist_id) or None,
                "album": names["album"].get(r.album_id) or None,
                "genre": names["genre"].get(r.genre_id) or None,
                "key": names["key"].get(r.key_id) or None,
                "comment": r.comment or "",
                "analyze_path": r.analyze_path,
                "tempo": r.tempo,
                "duration": r.duration,
                "bitrate": r.bitrate,
                "sample_rate": r.sample_rate,
                "file_size": r.file_size,
                "rating": r.rating,
                "year": r.year,
                "track_number": r.track_number,
            },
        )

    lists = _tree_positions(tree)
    ordered = sorted(entries, key=lambda e: (e.playlist_id, e.entry_index))
    for e in ordered:
        key = lists.get(e.playlist_id)
        path = by_id.get(e.track_id)
        if key and path:
            out.playlists.setdefault(key, []).append(path)
    for key in lists.values():  # keep empty playlists too
        out.playlists.setdefault(key, [])
    return out


#: Our own record beside the database, so a later export can tell what
#: actually changed. Players ignore files they do not know; Rekordbox leaves
#: it alone too.
MANIFEST_NAME = "track-kommander.json"


class Change(Enum):
    """What an export has to do to one track."""

    NEW = "new"
    """Not on the key: copy, analyse, write."""

    FULL = "full"
    """The audio itself changed — everything has to be redone."""

    CUES = "cues"
    """Only cues or metadata changed. The waveform on the key is still
    correct, so it is reused and the audio is never decoded."""

    UNCHANGED = "unchanged"
    """Nothing to do."""


def device_track_from(t: Track, *, offset_ms: float) -> DeviceTrack:
    """A collection track as it goes onto the key.

    Cues and the grid are shifted by *offset_ms*. The grid's own mirror cue
    is dropped for the same reason the Rekordbox XML drops it: it is not a
    cue the user placed, and a player has only eight slots. Both exports
    land on the same stick, so they have to make the same call.
    """
    from core.rekordbox import camelot, is_grid_mirror, stars

    grid = t.grid_cue
    cues = [
        anlz.Cue(time_ms=int(c.start_ms + offset_ms), hot_cue=min(8, c.hotcue + 1))
        for c in t.cues
        if not c.is_grid and 0 <= c.hotcue < 8 and not is_grid_mirror(c, grid)
    ]
    return DeviceTrack(
        source=Path(t.path),
        title=t.title,
        artist=t.artist,
        album=t.album,
        genre=t.genre,
        key=camelot(t.musical_key),
        bpm=t.bpm,
        duration_s=int(t.total_time_s),
        bitrate_kbps=t.bitrate,
        rating=stars(t.rating),
        first_beat_ms=(grid.start_ms if grid else 0.0) + offset_ms,
        cues=cues,
    )


def audio_fingerprint(source: Path) -> str:
    """Identifies the audio file itself — size and modification time.

    Not a content hash: hashing 220 MB of audio costs more than the decode
    this is meant to avoid. Size plus nanosecond mtime is what the copy
    planner already trusts to decide a file is the same one.
    """
    try:
        st = Path(source).stat()
    except OSError:
        return "missing"
    return f"{st.st_size}:{st.st_mtime_ns}"


def track_fingerprint(track: DeviceTrack) -> str:
    """Identifies everything about a track that reaches the device.

    Anything in here changing means the row or the analysis has to be
    rewritten; anything outside it is not exported and cannot matter.
    """
    cues = ";".join(
        f"{c.hot_cue}@{int(c.time_ms)}+{c.loop_end_ms or 0}"
        for c in sorted(track.cues, key=lambda c: (c.hot_cue, c.time_ms))
    )
    parts = (
        audio_fingerprint(track.source),
        track.title,
        track.artist,
        track.album,
        track.genre,
        track.key,
        f"{track.bpm:.4f}",
        str(track.duration_s),
        str(track.bitrate_kbps),
        str(track.rating),
        str(track.year),
        f"{track.first_beat_ms:.3f}",
        cues,
    )
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()[:32]


#: Raise when what the export writes for an unchanged track changes, so a
#: key written before is rewritten in full once. 2: the ANLZ file header and
#: the PWV2 byte layout were fixed after an XDJ-1000 ignored the old files.
#: 3: analysis moved to the folder a player hashes from the track's path,
#: and FLAC rows got their own file type. 4: names became plain ASCII of at
#: most 32 characters, so tracks the old record pins must be renamed.
#: 5: the cap dropped to 20.
MANIFEST_VERSION = 5


def read_manifest(dest: Path) -> dict[str, dict[str, str]]:
    """``{device path: {"all": …, "audio": …}}`` for a key, or empty.

    Empty covers a key written by an older build or by Rekordbox, and a
    corrupt file — in every case the safe answer is "assume nothing is known",
    which makes each track look new and simply costs time.
    """
    path = Path(dest) / DB_DIR / MANIFEST_NAME
    try:
        data = json.loads(path.read_text())
        tracks = data["tracks"]
        version = data.get("version")
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return {}
    if not isinstance(tracks, dict) or version != MANIFEST_VERSION:
        return {}
    return {k: v for k, v in tracks.items() if isinstance(v, dict)}


def write_manifest(dest: Path, tracks: dict[str, dict[str, str]]) -> None:
    path = Path(dest) / DB_DIR / MANIFEST_NAME
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"version": MANIFEST_VERSION, "tracks": tracks}, indent=1, sort_keys=True)
        )
    except OSError:
        # Cosmetic: without it the next export re-does everything, which is
        # slow but correct. Never a reason to fail an export that worked.
        log.warning("could not write %s", path, exc_info=True)


def _decide_one(
    device_path: str,
    track: DeviceTrack,
    listed: Container[str],
    manifest: dict[str, dict[str, str]],
    *,
    merge: bool,
) -> Change:
    known = manifest.get(device_path)
    # The manifest alone is not proof. A track it knows but the database
    # does not list has no row to carry over, so "unchanged" would drop it
    # from the key — it has to be written like a new one.
    if not merge or known is None or device_path not in listed:
        return Change.NEW
    if known.get("all") == track_fingerprint(track):
        return Change.UNCHANGED
    if known.get("audio") == audio_fingerprint(track.source):
        return Change.CUES
    return Change.FULL


def decide_changes(
    placed: list[tuple[int, str, DeviceTrack]],
    listed: Container[str],
    manifest: dict[str, dict[str, str]],
    *,
    merge: bool,
    refresh_existing: bool = True,
    reanalyse: bool = False,
) -> dict[int, Change]:
    """What an export does to each track: the one rule, used both by the panel
    before anything is decoded and by :func:`export` itself.

    *placed* is ``(index, device path, track)`` for each track with a place on
    the key; *listed* the device paths the key's database lists; *manifest*
    what :func:`read_manifest` returned. Reads the source files' size and
    time, writes nothing.

    *reanalyse* upgrades rather than overwrites: a track the key does not
    have stays NEW. *refresh_existing* off then wins over it — every track
    the database lists is left exactly as it is, even if it did change.
    """
    decisions = {i: _decide_one(path, t, listed, manifest, merge=merge) for i, path, t in placed}
    if merge and reanalyse:
        decisions = {i: (c if c is Change.NEW else Change.FULL) for i, c in decisions.items()}
    if merge and not refresh_existing:
        kept = {i: Change.UNCHANGED for i, path, _t in placed if path in listed}
        decisions = {**decisions, **kept}
    return decisions


def plan_changes(
    dest: Path, tracks: list[DeviceTrack], *, refresh_existing: bool = True, reanalyse: bool = False
) -> dict[int, Change]:
    """What a merge onto *dest* will do to each track, by its index in
    *tracks* — decided by :func:`decide_changes`, as :func:`export` does.

    Call this before computing any waveforms: the whole point is to find the
    tracks whose audio does not need decoding. A track with no place on the
    key (its source is missing) is NEW; the export will not list it at all.
    """
    dest = Path(dest)
    existing = _read_existing(dest / DB_DIR / "export.pdb")
    mapping = plan_for(sources_of(tracks), dest).mapping
    placed = [
        (i, "/" + Path(mapping[str(t.source)]).relative_to(dest).as_posix(), t)
        for i, t in enumerate(tracks)
        if mapping.get(str(t.source))
    ]
    decided = decide_changes(
        placed,
        existing.tracks,
        read_manifest(dest),
        merge=True,
        refresh_existing=refresh_existing,
        reanalyse=reanalyse,
    )
    return {i: decided.get(i, Change.NEW) for i in range(len(tracks))}


def analysis_plan(
    dest: Path, tracks: list[DeviceTrack], *, merge: bool, refresh_existing: bool, reanalyse: bool
) -> dict[int, Change]:
    """What each track needs *before any audio is decoded*, as the UI sees it.

    :func:`plan_changes` answers the question for a merge, with the two
    overrides the export panel offers, in the order they have to apply:

    *reanalyse* — "Re-analyse the audio" — means every track, not just the
    ones this export would have decoded anyway. The reason to tick it is a
    waveform that looks wrong on a player, and that track is most likely one
    already on the key that nothing would otherwise touch.

    *refresh_existing* off wins over it regardless, because analysing a track
    it forbids rewriting is work whose result is thrown away. A track the key does *not* have is unaffected by
    either: it is new, so it is exported and decoded whatever is ticked.

    :func:`export` decides with the same rule (:func:`decide_changes`), so
    what is decoded here is what it writes.
    """
    if not merge:
        # Nothing is being kept, so every track is new and gets decoded;
        # *reanalyse* has nothing left to force.
        return dict.fromkeys(range(len(tracks)), Change.NEW)
    return plan_changes(dest, tracks, refresh_existing=refresh_existing, reanalyse=reanalyse)


def needs_analysis(
    plan: dict[int, Change],
    tracks: list[DeviceTrack],
) -> list[DeviceTrack]:
    """The tracks in *plan* whose audio has to be decoded.

    A cues-only change is not one of them: the waveform already on the key is
    still correct and :func:`export` reads it back.
    """
    return [t for i, t in enumerate(tracks) if plan.get(i) in (Change.NEW, Change.FULL)]


def plan_for(items: Sequence[str | Path | Source], dest: Path) -> UsbPlan:
    """The copy plan for *items* on the key at *dest*.

    Every planner call goes through here so they all agree: a track the
    manifest says is on the key is pinned to the path it has there, however
    its tags have changed since. Keys written before the manifest recorded
    sources are matched on the audio fingerprint instead, and so is a file
    that has moved since the key was written.

    The fingerprint is a stand-in, not an identity: size and modification
    time, which two files can share. So it never overrules a source the
    manifest names that is still where it was — that file is the one on the
    key, and another with its size and time is another track.
    """
    dest = Path(dest)
    pinned: dict[str, str] = {}
    by_audio: dict[str, tuple[str, str]] = {}
    for device_path, entry in read_manifest(dest).items():
        on_key = dest / device_path.lstrip("/")
        if not on_key.is_file():
            continue
        source = entry.get("source") or ""
        if source:
            pinned[source] = str(on_key)
        if entry.get("audio"):
            by_audio.setdefault(entry["audio"], (str(on_key), source))
    for item in items:
        path = item.path if isinstance(item, Source) else str(item)
        if path not in pinned and by_audio:
            on_key, source = by_audio.get(audio_fingerprint(Path(path)), ("", ""))
            if on_key and not (source and Path(source).is_file()):
                pinned[path] = on_key
    return plan_export([i if isinstance(i, Source) else str(i) for i in items], dest, pinned=pinned)


def sources_of(tracks: list[DeviceTrack]) -> list[Source]:
    """What the planner files each track under. Every caller that plans the
    same tracks must go through this, or their device paths disagree."""
    return [Source(str(t.source), artist=t.artist, album=t.album) for t in tracks]


def already_on_device(dest: Path, sources: list[Path | Source]) -> set[Path]:
    """Which of *sources* the key at *dest* already has, by device path.

    The caller computes the waveforms — the slow part, since it decodes every
    file — before handing tracks to :func:`export`. Without this it would pay
    that cost even for tracks ``refresh_existing=False`` is about to skip,
    which is the whole point of the option.

    Cheap: it plans the copy without performing it, and reads the database
    that is already on the key.
    """
    dest = Path(dest)
    existing = _read_existing(dest / DB_DIR / "export.pdb")
    if not existing.tracks:
        return set()
    plan = plan_for(list(sources), dest)
    out: set[Path] = set()
    for source, target in plan.mapping.items():
        device_path = "/" + Path(target).relative_to(dest).as_posix()
        if device_path in existing.tracks:
            out.add(Path(source))
    return out


# ── The export ──────────────────────────────────────────────────────────────


def export(
    tracks: list[DeviceTrack],
    dest: Path,
    seed_pdb: Path,
    *,
    playlists: dict[PlaylistKey, list[int]] | None = None,
    merge: bool = False,
    refresh_existing: bool = True,
    reanalyse: bool = False,
    progress: Callable[[int, int, str], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
    analysis_progress: Callable[[int, int, str], None] | None = None,
    phase: Callable[[str], None] | None = None,
) -> DeviceReport:
    """Write the whole device tree. Returns what happened.

    *progress* hears about each audio copy, *analysis_progress* about each
    set of analysis files written, and *phase* about the steps that have no
    per-track count — the database, the sidecar sweep. Every step is also
    timed into the log, so an export that takes a long time says where.

    The audio is copied first, then the analysis files, then the database —
    so a database is only written once the things it points at exist.

    With *merge*, tracks are **added** to whatever the key already holds
    instead of replacing it: the existing ``export.pdb`` becomes the base,
    tracks already on the key keep their rows and their analysis, new ones
    take slots after the last one in use, and a playlist whose name is already
    there is extended rather than duplicated. Without it the library is
    rebuilt from the blank seed, which leaves earlier audio on the key as
    orphans no player can see.

    *refresh_existing* decides what happens to a track the key already has.
    The default rewrites it, because the reason to export a track again is
    usually that something about it changed. Turning it off leaves those
    tracks exactly as they are, which skips decoding their audio again for the
    waveforms — the slow part of an export — at the cost of not picking up
    anything that did change.

    *reanalyse* rewrites every track the key already has, as the panel's
    "Re-analyse the audio" decoded them all (:func:`analysis_plan`);
    *refresh_existing* off still wins over it.
    """
    dest = Path(dest)
    report = DeviceReport(tracks=len(tracks))
    lap = _Lap()

    # 1. Audio.
    on_device = _copy_audio(tracks, dest, report, progress, should_stop)
    lap.done("copying %d file(s)", report.copied)
    existing, manifest = _read_key(dest, merge, report)
    was_there = {i for i, t in on_device if t.device_path in existing.tracks}

    # What each track actually needs. Skipping the ones nothing changed about
    # is where a re-export gets its speed: decoding audio is the largest cost
    # and a track that did not change needs none of it.
    decisions = decide_changes(
        [(i, t.device_path, t) for i, t in on_device],
        existing.tracks,
        manifest,
        merge=merge,
        refresh_existing=refresh_existing,
        reanalyse=reanalyse,
    )
    wanted = _changed(on_device, decisions, report)
    _reuse_waveforms(wanted, decisions, existing, dest)

    # 2. Analysis files. A Stop or a failed write leaves some tracks without.
    # Those are never recorded as done, so the next export picks them up
    # again. One that was already on the key keeps the row it had, carried
    # over below like any track this export does not mention — its old
    # analysis is still there. One that is new is listed without analysis:
    # its audio is on the key and a player can play it.
    analysed = _write_analysis(wanted, dest, report, should_stop, analysis_progress)
    unfinished = {i for i, _t in wanted} - analysed
    todo = [(i, t) for i, t in wanted if i in analysed or i not in was_there]
    _count_changes(
        report,
        todo,
        decisions,
        was_there,
        kept=len(was_there) if merge and not refresh_existing else None,
    )
    lap.done("writing analysis for %d track(s)", len(todo))

    # 3. Database.
    if phase is not None:
        phase("Writing the database…")
    _write_database(todo, on_device, existing, playlists, seed_pdb, dest, report)
    lap.done("writing the database")
    if report.db_path is not None:
        _write_after_database(
            dest, report, report.db_path, manifest, existing, on_device, unfinished, phase, lap
        )
    _sweep_sidecars(dest, report, phase, lap)
    return report


def _sweep_sidecars(
    dest: Path, report: DeviceReport, phase: Callable[[str], None] | None, lap: _Lap
) -> None:
    """Last, so it also catches the sidecars the database and analysis writes
    produced. A stick a player walks should hold only tracks and analysis."""
    if phase is not None:
        phase("Removing macOS sidecars…")
    report.sidecars_removed = strip_macos_metadata(dest)
    lap.done("sweeping sidecars")


def _copy_audio(
    tracks: list[DeviceTrack],
    dest: Path,
    report: DeviceReport,
    progress: Callable[[int, int, str], None] | None,
    should_stop: Callable[[], bool] | None,
) -> list[tuple[int, DeviceTrack]]:
    """Copy the audio; return the tracks now on the key, by their index in
    *tracks*, each with its device path and file size filled in."""
    plan: UsbPlan = plan_for(sources_of(tracks), dest)
    report.missing = [str(c.source) for c in plan.missing]
    report.copied, copy_errors = run_export(plan, progress, should_stop)
    report.errors += copy_errors
    mapping = plan.mapping

    # Keep each track's index in the caller's list: playlists are expressed as
    # indices into it, and some tracks may not make it onto the device — a
    # copy that failed, or every one after a Stop. Only what is actually on
    # the key goes into the database; the plan alone is not proof.
    on_device: list[tuple[int, DeviceTrack]] = [
        (i, t)
        for i, t in enumerate(tracks)
        if str(t.source) in mapping and Path(mapping[str(t.source)]).is_file()
    ]
    for _i, t in on_device:
        target = Path(mapping[str(t.source)])
        t.device_path = "/" + target.relative_to(dest).as_posix()
        if not t.file_size:
            t.file_size = target.stat().st_size
    return on_device


def _read_key(
    dest: Path,
    merge: bool,
    report: DeviceReport,
) -> tuple[_Existing, dict[str, dict[str, str]]]:
    """What the key already holds: its library and its manifest.

    It decides both the base database and where the new analysis can go, so
    it has to be read before either is written. Nothing, without *merge*.
    """
    existing_pdb = dest / DB_DIR / "export.pdb"
    existing = _read_existing(existing_pdb) if merge else _Existing()
    if existing.unreadable:
        report.errors.append(_set_aside_unreadable(existing_pdb))
    manifest = read_manifest(dest) if merge else {}
    return existing, manifest


def _changed(
    on_device: list[tuple[int, DeviceTrack]], decisions: dict[int, Change], report: DeviceReport
) -> list[tuple[int, DeviceTrack]]:
    """The tracks this export writes; the rest are counted as unchanged."""
    report.unchanged = sum(1 for i, _t in on_device if decisions[i] is Change.UNCHANGED)
    return [(i, t) for i, t in on_device if decisions[i] is not Change.UNCHANGED]


def _reuse_waveforms(
    wanted: list[tuple[int, DeviceTrack]],
    decisions: dict[int, Change],
    existing: _Existing,
    dest: Path,
) -> None:
    """A cue change does not alter a single sample, so the waveform already on
    the key is still correct — read it back rather than decoding again."""
    for i, t in wanted:
        if decisions[i] is not Change.CUES or t.analysis is not None:
            continue
        was = existing.tracks.get(t.device_path)
        if was is None:
            continue
        reused = anlz.waveform_from_files(dest / was.analyze_path.lstrip("/"))
        if reused is not None:
            t.analysis = reused
            t.waveform = list(reused.preview)


def _count_changes(
    report: DeviceReport,
    todo: list[tuple[int, DeviceTrack]],
    decisions: dict[int, Change],
    was_there: set[int],
    *,
    kept: int | None,
) -> None:
    """Fill in how many tracks were added, reused, replaced or kept.

    *kept* is the number the key already had when those are left alone, or
    None when they are rewritten.
    """
    report.added = sum(1 for i, _t in todo if i not in was_there)
    report.reused = sum(1 for i, _t in todo if decisions[i] is Change.CUES)
    if kept is not None:
        report.kept = kept
    else:
        report.replaced = sum(1 for i, _t in todo if i in was_there)


def _write_database(
    todo: list[tuple[int, DeviceTrack]],
    on_device: list[tuple[int, DeviceTrack]],
    existing: _Existing,
    playlists: dict[PlaylistKey, list[int]] | None,
    seed_pdb: Path,
    dest: Path,
    report: DeviceReport,
) -> None:
    """Write ``export.pdb``; a failure is reported, never raised."""
    db_dir = dest / DB_DIR
    db_dir.mkdir(parents=True, exist_ok=True)
    try:
        # Always built on the blank seed, even when merging: the writer can
        # add rows but not change or remove them, so "replace this track" and
        # "replace this playlist" are only expressible by rebuilding. What
        # this export does not mention is carried over below.
        # Aligned: artist and album names laid out like Rekordbox's (see
        # core.pdb_writer); the library's own layout crashed an XDJ-1000.
        editor = AlignedPdbEditor.from_file(str(seed_pdb))
        id_by_path = _add_rows(editor, todo, existing, report)
        # Playlists are indices into the caller's list; resolve each to the row
        # it ended up as, whether that row was written now or carried over.
        id_by_index: dict[int, int] = {
            i: id_by_path[t.device_path] for i, t in on_device if t.device_path in id_by_path
        }
        _add_playlists(editor, playlists, existing, id_by_path, id_by_index)
        out = db_dir / "export.pdb"
        for path, track_id in id_by_path.items():
            _set_file_type(editor, track_id, path)
        # Row counts in the page headers the way Rekordbox writes them; the
        # library's own crashed an XDJ-1000 once a table held two rows.
        # Replaced in one step: this is the only database an older player
        # reads, and a key pulled mid-write must still hold the last good one.
        write_bytes_atomic(out, fix_row_counts(editor.to_bytes()))
        report.db_path = out
        report.on_key = len(id_by_path)
        log.info(
            "wrote %s: %d row(s) — %d written, %d replaced, %d kept",
            out,
            len(id_by_path),
            len(todo),
            report.replaced,
            report.kept,
        )
    except Exception as exc:
        report.errors.append(f"export.pdb: {exc}")


def _add_rows(
    editor, todo: list[tuple[int, DeviceTrack]], existing: _Existing, report: DeviceReport
) -> dict[str, int]:
    """Add a row for each track written now and each one carried over;
    return the row id by device path."""
    id_by_path: dict[str, int] = {}
    for _orig, t in todo:
        id_by_path[t.device_path] = editor.add_track(
            title=t.title or t.source.stem,
            file_path=t.device_path,
            filename=Path(t.device_path).name,
            artist=t.artist or None,
            album=t.album or None,
            genre=t.genre or None,
            key=t.key or None,
            analyze_path=t.analyze_path,
            tempo=int(round(t.bpm * 100)),
            duration=int(t.duration_s),
            bitrate=int(t.bitrate_kbps),
            sample_rate=int(t.sample_rate),
            file_size=int(t.file_size),
            rating=int(t.rating),
            year=int(t.year),
        )
    # Tracks the key had that this export says nothing about. Their
    # analysis is already on the drive and untouched, so the row keeps
    # pointing at it.
    for path, carried in existing.tracks.items():
        if path in id_by_path:
            continue
        if not conforms(path):
            report.errors.append(
                f"{path}: not carried over — its name breaks the stick's "
                "naming rules. Export that track again."
            )
            continue
        id_by_path[path] = editor.add_track(file_path=path, **carried.fields)
    bad = [p for p in id_by_path if not conforms(p)]
    if bad:
        raise ValueError(
            "refusing to list paths that break the naming rules: " + ", ".join(bad[:5])
        )
    return id_by_path


def _add_playlists(
    editor,
    playlists: dict[PlaylistKey, list[int]] | None,
    existing: _Existing,
    id_by_path: dict[str, int],
    id_by_index: dict[int, int],
) -> None:
    """The playlists this export names, then the ones the key already had.

    Playlists are indices into the caller's `tracks` list. Anything that
    didn't reach the device is skipped rather than breaking the playlist.
    """
    # Each playlist goes in the folders it has in Traktor; a CDJ browses
    # the same tree.
    folders = _Folders(editor)
    named: set[tuple[str, ...]] = set()
    for key, members in (playlists or {}).items():
        where, name = key_parts(key)
        named.add((*where, name))
        chosen = [id_by_index[m] for m in members if m in id_by_index]
        if not chosen:
            continue
        pid = editor.create_playlist(name, parent_id=folders.id_for(where))
        for tid in chosen:
            editor.add_to_playlist(pid, tid)

    # Playlists this export does not name are carried over as they were,
    # in their folders. One it *does* name has already been written above,
    # fully — replaced rather than appended to, so a track dropped from it
    # in Traktor is dropped from the key.
    for key, paths in existing.playlists.items():
        if key in named:
            continue
        chosen = [id_by_path[p] for p in paths if p in id_by_path]
        if not chosen:
            continue
        *where, name = key
        pid = editor.create_playlist(name, parent_id=folders.id_for(tuple(where)))
        for tid in chosen:
            editor.add_to_playlist(pid, tid)


def _write_after_database(
    dest: Path,
    report: DeviceReport,
    pdb: Path,
    manifest: dict[str, dict[str, str]],
    existing: _Existing,
    on_device: list[tuple[int, DeviceTrack]],
    unfinished: set[int],
    phase: Callable[[str], None] | None,
    lap: _Lap,
) -> None:
    """What only makes sense once ``export.pdb`` is written: the OneLibrary
    database, the manifest and the player-settings files."""
    # 4. The OneLibrary database, derived from the one just written so the
    # two can never disagree. Its own step: a failure here leaves a stick the
    # older players still read in full, and the report says which file failed.
    if phase is not None:
        phase("Writing the OneLibrary database…")
    try:
        from core import onelibrary

        report.onelibrary_path = onelibrary.write_from_pdb(pdb, dest / DB_DIR / onelibrary.DB_NAME)
    except Exception as exc:
        report.errors.append(f"{onelibrary.DB_NAME}: {exc}")
    lap.done("writing the OneLibrary database")

    _record_what_is_on_the_key(dest, manifest, existing, on_device, unfinished)

    # The player-settings files. An XDJ-1000 refuses a key without them
    # ("MY SETTING DATA was not found"), so a database alone is not enough.
    try:
        report.settings_written = my_settings.write_missing(dest / PIONEER_DIR)
    except OSError as exc:
        report.errors.append(f"player settings: {exc}")


def _record_what_is_on_the_key(
    dest: Path,
    manifest: dict[str, dict[str, str]],
    existing: _Existing,
    on_device: list[tuple[int, DeviceTrack]],
    unfinished: set[int],
) -> None:
    """What the key now holds, so the next export can tell what changed.

    Tracks carried over keep the entry they had — and so does one whose
    refresh did not finish, since the row it has is still the old one.
    """
    record = {path: dict(entry) for path, entry in manifest.items() if path in existing.tracks}
    for i, t in on_device:
        if i in unfinished:
            continue
        record[t.device_path] = {
            "all": track_fingerprint(t),
            "audio": audio_fingerprint(t.source),
            "source": str(t.source),
        }
    write_manifest(dest, record)


def audio_dir(dest: Path) -> Path:
    return Path(dest) / AUDIO_DIRNAME


# ── Sweeping what the database no longer lists ──────────────────────────────


@dataclass
class Orphans:
    """Files on the key that no row in ``export.pdb`` points at.

    A rebuild (``merge=False``) writes a fresh library from the blank seed,
    and :func:`plan_export` does not recopy audio that is already there — so
    the files stay on the drive while their rows do not. They are invisible
    to a player and they are the largest thing on the stick.
    """

    audio: list[Path] = field(default_factory=list)
    analysis: list[Path] = field(default_factory=list)
    """Analysis *directories*, each holding one track's ANLZ files."""

    bytes: int = 0

    def __bool__(self) -> bool:
        return bool(self.audio or self.analysis)

    @property
    def count(self) -> int:
        return len(self.audio) + len(self.analysis)


def _audio_files(audio_root: Path) -> list[Path]:
    """Every audio file under ``Contents/``, however deep it is filed.

    Dotfiles and anything under a dot-directory are the OS's, not ours.
    """
    return sorted(
        f
        for f in audio_root.rglob("*")
        if f.is_file() and not any(part.startswith(".") for part in f.relative_to(audio_root).parts)
    )


def _prune_empty_folders(audio_root: Path) -> None:
    """Drop artist/album folders the sweep emptied. ``Contents/`` itself stays."""
    for folder in sorted(
        (d for d in audio_root.rglob("*") if d.is_dir()), key=lambda d: len(d.parts), reverse=True
    ):
        try:
            folder.rmdir()  # refuses unless empty
        except OSError:
            continue


def _same_name(path: Path) -> str:
    """A path as a string both sides of a comparison can agree on.

    macOS writes decomposed filenames to exFAT — "Maōh" is one code point in
    the database row and two on the drive — so comparing the two spellings
    byte for byte makes a track the key still lists look unreferenced. And
    FAT/exFAT do not distinguish case: artist tags spelled Benza and BENZA
    share one folder under whichever spelling came first, while the row
    keeps the other. This deletes audio, so the comparison is done on one
    normal form, case folded.
    """
    return unicodedata.normalize("NFC", str(path.resolve())).casefold()


def find_orphans(dest: Path) -> Orphans:
    """What is on the key that its own database does not reference.

    Read-only. Returns nothing at all if the database cannot be read: an
    unreadable ``export.pdb`` would otherwise make every file on the stick
    look unreferenced, which is the one mistake that must never happen here.
    """
    dest = Path(dest)
    pdb = dest / DB_DIR / "export.pdb"
    if not pdb.is_file():
        return Orphans()

    existing = _read_existing(pdb)
    if not existing.tracks:
        # Either genuinely empty or unreadable — _read_existing cannot tell us
        # which, and deleting on a misread is unrecoverable. Do nothing.
        return Orphans()

    keep_audio = {_same_name(dest / p.lstrip("/")) for p in existing.tracks}
    keep_analysis = {
        _same_name((dest / c.analyze_path.lstrip("/")).parent)
        for c in existing.tracks.values()
        if c.analyze_path
    }

    out = Orphans()

    audio_root = dest / AUDIO_DIRNAME
    if audio_root.is_dir():
        for f in _audio_files(audio_root):
            if _same_name(f) not in keep_audio:
                out.audio.append(f)
                out.bytes += f.stat().st_size

    anlz_root = dest / ANLZ_DIR
    if anlz_root.is_dir():
        # PIONEER/USBANLZ/Pxxx/xxxxxxxx/ — the leaf directories are the ones
        # that belong to a track.
        for bank in sorted(p for p in anlz_root.iterdir() if p.is_dir()):
            for slot in sorted(p for p in bank.iterdir() if p.is_dir()):
                if _same_name(slot) in keep_analysis:
                    continue
                out.analysis.append(slot)
                out.bytes += sum(f.stat().st_size for f in slot.rglob("*") if f.is_file())

    log.info(
        "orphan sweep on %s: %d audio, %d analysis, %.1f MB",
        dest,
        len(out.audio),
        len(out.analysis),
        out.bytes / 1e6,
    )
    return out


def audio_not_in(dest: Path, sources: list[Path | Source]) -> tuple[int, int]:
    """Audio on the key that *sources* would not write. Returns (files, bytes).

    What a Replace deletes, worked out before a single byte is written, so
    the confirmation can name the number instead of the user finding it out
    afterwards. Read-only, and compared on one normal form for the same
    reason :func:`find_orphans` is.
    """
    dest = Path(dest)
    audio_root = dest / AUDIO_DIRNAME
    if not audio_root.is_dir():
        return 0, 0

    keep = {_same_name(Path(t)) for t in plan_for(list(sources), dest).mapping.values()}

    files = size = 0
    for f in _audio_files(audio_root):
        if _same_name(f) in keep:
            continue
        files += 1
        size += f.stat().st_size
    return files, size


def remove_orphans(dest: Path, orphans: Orphans) -> tuple[int, list[str]]:
    """Delete what :func:`find_orphans` found. Returns (removed, errors).

    Every path is re-checked to be inside the two directories the export owns
    before it is touched. *orphans* comes straight from ``find_orphans`` in
    normal use, but this is the only thing standing between a bad caller and
    someone's music, so it does not take that on trust.
    """
    dest = Path(dest).resolve()
    allowed = (dest / AUDIO_DIRNAME, dest / ANLZ_DIR)
    removed = 0
    errors: list[str] = []

    for path in [*orphans.audio, *orphans.analysis]:
        target = Path(path).resolve()
        if not any(target.is_relative_to(root.resolve()) for root in allowed):
            errors.append(f"refused to remove outside the export: {target}")
            continue
        try:
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
            removed += 1
        except OSError as exc:
            errors.append(f"{target.name}: {exc}")

    if removed:
        _prune_empty_folders(dest / AUDIO_DIRNAME)
    log.info("orphan sweep removed %d item(s) from %s", removed, dest)
    return removed, errors
