"""Convert a Traktor collection into a Rekordbox XML library.

Rekordbox imports an XML library natively (Preferences → Advanced → Database →
rekordbox xml), which is the only documented, version-stable way in. Writing
Rekordbox's own ``master.db`` is not attempted: it is encrypted and its schema
moves between releases.

Pure functions over parsed NML elements — no Qt, no I/O except the final
write — so the mapping is testable without a Traktor installation.

Field mapping
-------------

=========================== ======================================
Traktor (NML)               Rekordbox XML
=========================== ======================================
``LOCATION`` DIR/FILE/VOL   ``TRACK@Location`` (``file://localhost/…``)
``INFO@RANKING``            ``TRACK@Rating`` — same 0/51/…/255 scale
``INFO@PLAYTIME_FLOAT``     ``TRACK@TotalTime`` (whole seconds)
``TEMPO@BPM``               ``TRACK@AverageBpm`` + ``<TEMPO>``
``MUSICAL_KEY@VALUE``       ``TRACK@Tonality`` (see :func:`tonality`)
``CUE_V2 TYPE=4`` (grid)    ``<TEMPO Inizio=… Bpm=… Metro="4/4" Battito="1">``
``CUE_V2 TYPE=0`` + HOTCUE  ``<POSITION_MARK Type="0" Num="0-7">``
``CUE_V2 TYPE=5`` (loop)    ``<POSITION_MARK Type="4" Start=… End=…>``
``CUE_V2`` without a hotcue ``<POSITION_MARK Num="-1">`` (memory cue)
=========================== ======================================

Sharp edges, all handled below rather than discovered later:

* **Units.** Traktor cue/grid positions are milliseconds; Rekordbox wants
  seconds. Positions are kept in ms until the final format step.
* **Hotcue slots.** Both have 8 (A–H). Traktor's ``HOTCUE`` is already 0-based,
  so it maps straight to ``Num``; anything outside 0–7 becomes a memory cue.
* **MP3 encoder delay.** Traktor and Rekordbox do not always agree on where
  t=0 is in an MP3, so grids can land a few ms apart. ``offset_ms`` shifts
  every position; it is a knob for the user, not something to guess.
* **Rekordbox needs the audio files** at the exported paths. This writes
  metadata only.
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

from core.constants import CAMELOT_MAP

log = logging.getLogger(__name__)

# Traktor CUE_V2 TYPE values (see core/hotcues.py for the full list).
CUE_HOTCUE = 0
CUE_FADE_IN = 1
CUE_FADE_OUT = 2
CUE_LOAD = 3
CUE_GRID = 4
CUE_LOOP = 5

# Rekordbox POSITION_MARK types.
RB_CUE = 0
RB_FADE_IN = 1
RB_FADE_OUT = 2
RB_LOAD = 3
RB_LOOP = 4

# Rekordbox supports hotcues A–H.
MAX_HOTCUES = 8

_PITCH_MAJOR = ("C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B")
_PITCH_MINOR = ("Cm", "C#m", "Dm", "Ebm", "Em", "Fm", "F#m", "Gm", "G#m", "Am", "Bbm", "Bm")


def key_parts(musical_key: int | None) -> tuple[int, bool] | None:
    """Split Traktor's ``MUSICAL_KEY@VALUE`` into (pitch class, is_major).

    0–11 are the major keys in pitch-class order, 12–23 the minor ones.
    Verified against all 24 values in a real collection by cross-checking the
    derived Camelot code against the Open Key string Traktor writes to
    ``INFO@KEY``.
    """
    if musical_key is None or not 0 <= musical_key <= 23:
        return None
    return (musical_key, True) if musical_key < 12 else (musical_key - 12, False)


def camelot(musical_key: int | None) -> str:
    """Camelot code ("8B") for a Traktor key value, or "" if unknown."""
    parts = key_parts(musical_key)
    return CAMELOT_MAP.get(parts, "") if parts else ""


def tonality(musical_key: int | None) -> str:
    """Rekordbox ``Tonality`` ("Am", "F#") for a Traktor key value."""
    parts = key_parts(musical_key)
    if parts is None:
        return ""
    pitch, major = parts
    return _PITCH_MAJOR[pitch] if major else _PITCH_MINOR[pitch]


def location_url(path: str) -> str:
    """Rekordbox ``Location`` URL for an absolute filesystem path.

    Rekordbox uses ``file://localhost/`` with percent-encoding. Windows paths
    keep their drive letter (``file://localhost/C:/Music/x.mp3``).
    """
    if not path:
        return ""
    p = str(path).replace("\\", "/")
    if not p.startswith("/"):
        p = "/" + p  # C:/… → /C:/…
    return "file://localhost" + quote(p, safe="/:")


def _f(value: str | None, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _i(value: str | None, default: int = 0) -> int:
    if value is None:
        return default
    try:
        return int(float(value))
    except ValueError:
        return default


def _secs(ms: float) -> str:
    """Milliseconds → Rekordbox's 3-decimal seconds string."""
    return f"{ms / 1000.0:.3f}"


def _iso_date(traktor_date: str | None) -> str:
    """Traktor's ``YYYY/M/D`` → Rekordbox's ``YYYY-MM-DD``."""
    if not traktor_date:
        return ""
    bits = traktor_date.split("/")
    if len(bits) != 3:
        return ""
    try:
        y, m, d = (int(b) for b in bits)
    except ValueError:
        return ""
    return f"{y:04d}-{m:02d}-{d:02d}"


@dataclass
class Cue:
    """One Traktor ``CUE_V2``, positions in milliseconds."""

    name: str
    type: int
    start_ms: float
    len_ms: float = 0.0
    hotcue: int = -1
    grid_bpm: float | None = None

    @property
    def is_grid(self) -> bool:
        return self.type == CUE_GRID

    @property
    def is_loop(self) -> bool:
        return self.type == CUE_LOOP and self.len_ms > 0


#: Traktor's RANKING ladder: one star is 51, five are 255.
_RANKING_STEP = 51


def stars(ranking: int) -> int:
    """0–5 stars from Traktor's 0–255 ``RANKING``.

    The rekordbox.xml ``Rating`` attribute uses Traktor's scale as it is;
    the PDB row's rating byte means stars. Off-ladder values round down.
    """
    return max(0, min(5, int(ranking) // _RANKING_STEP))


@dataclass
class Track:
    """One track's worth of Traktor metadata, ready to emit."""

    path: str
    title: str = ""
    artist: str = ""
    album: str = ""
    genre: str = ""
    label: str = ""
    comment: str = ""
    bpm: float = 0.0
    musical_key: int | None = None
    rating: int = 0
    play_count: int = 0
    bitrate: int = 0
    total_time_s: float = 0.0
    date_added: str = ""
    release_date: str = ""
    cues: list[Cue] = field(default_factory=list)

    @property
    def grid_cue(self) -> Cue | None:
        return next((c for c in self.cues if c.is_grid), None)


# ── Parsing a Traktor ENTRY ─────────────────────────────────────────────────


def parse_cue(el: ET.Element) -> Cue:
    grid = el.find("GRID")
    return Cue(
        name=el.get("NAME") or "",
        type=_i(el.get("TYPE"), -1),
        start_ms=_f(el.get("START")),
        len_ms=_f(el.get("LEN")),
        hotcue=_i(el.get("HOTCUE"), -1),
        grid_bpm=_f(grid.get("BPM")) if grid is not None else None,
    )


def parse_entry(el: ET.Element, path: str) -> Track:
    """Build a :class:`Track` from an NML ``ENTRY``.

    *path* is the resolved filesystem path — reconstructing it from
    ``LOCATION`` is Traktor-specific and already handled by
    ``core.traktor._nml_build_path``, so it is passed in rather than redone.
    """
    info = el.find("INFO")
    tempo = el.find("TEMPO")
    mkey = el.find("MUSICAL_KEY")

    def _info(attr: str) -> str:
        return (info.get(attr) or "") if info is not None else ""

    title = el.get("TITLE") or ""
    artist = el.get("ARTIST") or ""
    album_el = el.find("ALBUM")
    album = (album_el.get("TITLE") or "") if album_el is not None else ""

    return Track(
        path=path,
        title=title,
        artist=artist,
        album=album,
        genre=_info("GENRE"),
        label=_info("LABEL"),
        comment=_info("COMMENT"),
        bpm=_f(tempo.get("BPM")) if tempo is not None else 0.0,
        musical_key=_i(mkey.get("VALUE"), -1) if mkey is not None else None,
        rating=_i(_info("RANKING")),
        play_count=_i(_info("PLAYCOUNT")),
        bitrate=_i(_info("BITRATE")) // 1000,  # Traktor stores bits/s
        total_time_s=_f(_info("PLAYTIME_FLOAT")) or _f(_info("PLAYTIME")),
        date_added=_iso_date(_info("IMPORT_DATE")),
        release_date=_iso_date(_info("RELEASE_DATE")),
        cues=[parse_cue(c) for c in el.findall("CUE_V2")],
    )


# ── Emitting Rekordbox XML ──────────────────────────────────────────────────

#: How near the grid anchor a cue has to sit to be its mirror, in ms. The two
#: are written from the same value and differ only in the last decimal place.
GRID_MIRROR_MS = 1.0


def is_grid_mirror(cue: Cue, grid: Cue | None) -> bool:
    """Is *cue* Traktor's duplicate of the beat-grid anchor?

    Traktor stores the anchor twice: once as the grid itself (``TYPE="4"``)
    and once as an ordinary cue on the same millisecond, so it shows up on a
    hotcue button. Exporting the second one puts a meaningless cue on the
    first beat and spends one of the eight hotcue slots a player has.

    Matched on **position and slot, not on name**. The name is whatever
    Traktor or the user last left there: one real 897-track collection spells
    it ``AutoGrid`` 854 times, ``n.n.`` three times and ``Beat Marker`` three
    times, so a name test leaks. Every one of those 860 mirrors is in slot 0,
    and no cue the user placed anywhere in that collection sits on the anchor
    at all — so requiring slot 0 as well costs nothing and keeps a cue
    deliberately parked on the first beat in any other slot.

    A cue the user put in slot 0 *exactly* on the anchor is indistinguishable
    from the mirror in what Traktor records, and is dropped. That is the one
    case this gets wrong, it did not occur once in 897 real tracks, and the
    grid itself already marks that position.

    Both exports have to ask this the same way. They are written onto the same
    stick — the ANLZ files and the Rekordbox XML describing the same track —
    so a cue that one drops and the other keeps makes the key disagree with
    itself.
    """
    if grid is None or cue.is_grid or cue.hotcue != 0:
        return False
    return abs(cue.start_ms - grid.start_ms) < GRID_MIRROR_MS


def position_marks(track: Track, offset_ms: float = 0.0) -> list[ET.Element]:
    """``POSITION_MARK`` elements for a track's cues, in hotcue order.

    The grid marker is not a cue — it becomes ``<TEMPO>`` instead — and the
    cue Traktor puts on the same millisecond duplicates it, so both are
    dropped (see :func:`is_grid_mirror`). Cues without a usable hotcue slot
    become memory cues (``Num="-1"``) rather than being discarded.
    """
    grid = track.grid_cue
    out: list[ET.Element] = []
    for cue in track.cues:
        if cue.is_grid or is_grid_mirror(cue, grid):
            continue

        start = cue.start_ms + offset_ms
        attrs = {
            "Name": cue.name,
            "Type": str(_rb_type(cue)),
            "Start": _secs(start),
            "Num": str(cue.hotcue if 0 <= cue.hotcue < MAX_HOTCUES else -1),
        }
        if cue.is_loop:
            attrs["End"] = _secs(start + cue.len_ms)
        out.append(ET.Element("POSITION_MARK", attrs))

    # Hotcues first, in slot order, then memory cues by position — Rekordbox
    # does not care, but a stable order makes diffs and tests readable.
    out.sort(
        key=lambda e: (int(e.get("Num", -1)) < 0, int(e.get("Num", -1)), float(e.get("Start", 0)))
    )
    return out


def _rb_type(cue: Cue) -> int:
    if cue.is_loop:
        return RB_LOOP
    return {
        CUE_HOTCUE: RB_CUE,
        CUE_FADE_IN: RB_FADE_IN,
        CUE_FADE_OUT: RB_FADE_OUT,
        CUE_LOAD: RB_LOAD,
    }.get(cue.type, RB_CUE)


def track_element(track: Track, track_id: int, offset_ms: float = 0.0) -> ET.Element:
    """One ``<TRACK>`` element for the collection."""
    el = ET.Element(
        "TRACK",
        {
            "TrackID": str(track_id),
            "Name": track.title,
            "Artist": track.artist,
            "Album": track.album,
            "Genre": track.genre,
            "Label": track.label,
            "Comments": track.comment,
            "Kind": _kind(track.path),
            "TotalTime": str(int(round(track.total_time_s))),
            "AverageBpm": f"{track.bpm:.2f}" if track.bpm else "",
            "Tonality": tonality(track.musical_key),
            "Rating": str(track.rating),
            "PlayCount": str(track.play_count),
            "BitRate": str(track.bitrate),
            "DateAdded": track.date_added,
            "Location": location_url(track.path),
        },
    )

    # Traktor stores a single grid anchor plus a constant BPM, which is exactly
    # one Rekordbox TEMPO node. Inizio is the anchor in seconds.
    grid = track.grid_cue
    if grid is not None:
        el.append(
            ET.Element(
                "TEMPO",
                {
                    "Inizio": _secs(grid.start_ms + offset_ms),
                    "Bpm": f"{(grid.grid_bpm or track.bpm):.2f}",
                    "Metro": "4/4",
                    "Battito": "1",
                },
            )
        )

    for mark in position_marks(track, offset_ms):
        el.append(mark)
    return el


def _kind(path: str) -> str:
    ext = Path(path).suffix.lower().lstrip(".")
    return f"{ext.upper()} File" if ext else ""


def build_library(
    tracks: list[Track],
    *,
    playlists: dict[str, list[int]] | None = None,
    offset_ms: float = 0.0,
    product_name: str = "Track Kommander",
    product_version: str = "1.0.0",
) -> ET.ElementTree:
    """Assemble the whole ``DJ_PLAYLISTS`` document.

    *playlists* maps a playlist name to indices into *tracks*, preserving the
    order given — a set's order is the point of exporting it, so it is never
    re-sorted.
    """
    root = ET.Element("DJ_PLAYLISTS", {"Version": "1.0.0"})
    ET.SubElement(
        root,
        "PRODUCT",
        {
            "Name": product_name,
            "Version": product_version,
            "Company": "",
        },
    )

    collection = ET.SubElement(root, "COLLECTION", {"Entries": str(len(tracks))})
    for i, track in enumerate(tracks, start=1):
        collection.append(track_element(track, i, offset_ms))

    pl_root = ET.SubElement(root, "PLAYLISTS")
    node_root = ET.SubElement(
        pl_root,
        "NODE",
        {
            "Type": "0",
            "Name": "ROOT",
            "Count": str(len(playlists or {})),
        },
    )
    for name, indices in (playlists or {}).items():
        node = ET.SubElement(
            node_root,
            "NODE",
            {
                "Name": name,
                "Type": "1",
                "KeyType": "0",
                "Entries": str(len(indices)),
            },
        )
        for idx in indices:
            ET.SubElement(node, "TRACK", {"Key": str(idx + 1)})

    return ET.ElementTree(root)


def write_library(tree: ET.ElementTree, dest: Path) -> None:
    """Write the document to *dest* as UTF-8 with an XML declaration."""
    ET.indent(tree, space=" ")
    tree.write(dest, encoding="UTF-8", xml_declaration=True)
    log.info("wrote Rekordbox library → %s", dest)
