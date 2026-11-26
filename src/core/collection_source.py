"""Where Traktor's collection is read from: ``collection.nml``, or Traktor's memory.

Everything that wants the list of tracks or their tags asks here
(:func:`collection_paths`, :func:`collection_metadata`) rather than parsing the
NML itself. Normally that is the NML. When Track Kommander runs inside Traktor
(the patched copy with libtk injected), the integrated app registers a *native
source* that returns a snapshot of the collection read straight from Traktor's
memory (``workers.native_collection``): always current, including changes
Traktor has not written to the NML yet, and no XML parse.

A snapshot has the same shapes the NML parsers return, so callers cannot tell
the two apart: paths as Unicode NFC (Traktor's NML form; macOS's file system
hands out NFD), ratings 0-5, dates as the NML writes them ("2023/12/29"). It also
carries :data:`NATIVE_EXPORT_KEYS`, read only by the native USB export.

One known difference: the NML parser drops a second entry that has the same
AUDIO_ID as another (a renamed copy); memory lists both files.
"""

from __future__ import annotations

import logging
import unicodedata
from collections.abc import Callable
from pathlib import Path

from core.constants import AUDIO_EXTS
from core.cue_points import TrackCues
from core.runtime import in_traktor
from core.traktor import nml_cues, parse_traktor_nml, parse_traktor_nml_metadata

log = logging.getLogger(__name__)

Snapshot = tuple[list[str], dict[str, dict]]
"""(paths, path → metadata), as :func:`parse_traktor_nml` and
:func:`parse_traktor_nml_metadata` return them."""

_native: Callable[[], Snapshot | None] | None = None
_native_cues: Callable[[], list[TrackCues] | None] | None = None

# libtk's dump columns (traktor-native/src/tk_collection.mm, collection_dump); a newer libtk adds
# a 17th, the duration in seconds.
_COLUMNS = 16
_STARS = 51  # Traktor's raw rating is 0-255; the NML's RANKING / 51 is stars

#: Keys a native snapshot has beyond the NML parser's, read only by the native USB export
#: (:mod:`core.native_export`): the comment, Traktor's raw 0-255 rating, the duration.
NATIVE_EXPORT_KEYS = ("comment", "ranking", "playtime_s")


def _nml_allowed(what: str) -> bool:
    """False inside Traktor: TK never reads collection.nml there (User, 2026-11-24). A failed
    native read is an error to see, not a reason to parse the XML behind Traktor's back."""
    if in_traktor():
        log.error(
            "inside Traktor, %s unavailable from Traktor's memory (collection.nml not read)", what
        )
        return False
    return True


def set_native_source(source: Callable[[], Snapshot | None] | None) -> None:
    """Read the collection from *source* from now on (None: back to the NML).

    *source* returns None when it has nothing to offer right now; the NML is
    read then — except inside Traktor, where nothing is (see :func:`_nml_allowed`).
    """
    global _native
    _native = source
    log.info("collection source: %s", "Traktor's memory" if source else "collection.nml")


def _snapshot() -> Snapshot | None:
    source = _native
    if source is None:
        return None
    try:
        return source()
    except Exception:
        log.exception("native collection source failed")
        return None


def collection_paths(nml: Path | None) -> list[str]:
    """Every audio track in the collection (what :func:`parse_traktor_nml` gives)."""
    snap = _snapshot()
    if snap is not None:
        return list(snap[0])
    if nml is None or not _nml_allowed("the track list is"):
        return []
    return parse_traktor_nml(nml)


def collection_metadata(nml: Path | None) -> dict[str, dict]:
    """path → tags (what :func:`parse_traktor_nml_metadata` gives)."""
    snap = _snapshot()
    if snap is not None:
        return dict(snap[1])
    if nml is None or not _nml_allowed("track tags are"):
        return {}
    return parse_traktor_nml_metadata(nml)


def set_native_cues(source: Callable[[], list[TrackCues] | None] | None) -> None:
    """Read cue points from *source* (Traktor's memory) from now on; None: the NML."""
    global _native_cues
    _native_cues = source


def collection_cues(nml: Path | None) -> list[TrackCues]:
    """Every audio track's BPM and cue points (what :func:`core.traktor.nml_cues` gives)."""
    source = _native_cues
    if source is not None:
        try:
            tracks = source()
        except Exception:
            log.exception("native cue source failed")
            tracks = None
        if tracks is not None:
            return [t for t in tracks if Path(t.path).suffix.lower() in AUDIO_EXTS]
    if nml is None or not _nml_allowed("cue points are"):
        return []
    return nml_cues(nml)


def _date(packed: int) -> str:
    """Traktor's packed date (year << 16 | month << 8 | day) as the NML writes it."""
    if packed <= 0:
        return ""
    return f"{packed >> 16}/{(packed >> 8) & 0xFF}/{packed & 0xFF}"


def _int(text: str, default: int = 0) -> int:
    try:
        return int(text)
    except ValueError:
        return default


def _float(text: str) -> float:
    try:
        value = float(text)
    except ValueError:
        return 0.0
    return value if value == value and value > 0 else 0.0  # NaN or ≤ 0: no BPM


def parse_native_dump(text: str) -> Snapshot:
    """libtk's TSV dump of the collection → the NML parsers' shapes."""
    paths: list[str] = []
    meta: dict[str, dict] = {}
    for line in text.splitlines():
        f = line.split("\t")
        if len(f) not in (_COLUMNS, _COLUMNS + 1) or not f[1]:
            continue
        path = unicodedata.normalize("NFC", f[1])
        rating = _int(f[11], -1)
        bitrate = _int(f[15])
        plays = _int(f[12])
        # NATIVE_EXPORT_KEYS: what only the native USB export reads (core.native_export).
        export = {
            "comment": f[7],
            "ranking": max(0, rating),
            "playtime_s": _int(f[16]) if len(f) > _COLUMNS else 0,
        }
        meta[path] = export | {
            "title": f[2],
            "artist": f[3],
            "album": f[4],
            "genre": f[5],
            "label": f[6],
            "musical_key": _int(f[10], -1),
            "traktor_bpm": _float(f[9]),
            "rating": max(0, rating) // _STARS,
            "play_count": plays or None,
            "import_date": _date(_int(f[13])),
            "release_date": _date(_int(f[14])),
            "bitrate": bitrate or None,
        }
        if Path(path).suffix.lower() in AUDIO_EXTS:
            paths.append(path)
    return paths, meta
