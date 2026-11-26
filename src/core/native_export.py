"""The USB export's collection, from Traktor's memory instead of collection.nml.

Inside Traktor TK never reads collection.nml (User, 2026-11-24). The export needs two things
from the collection, and both are built here from libtk's dumps (the snapshot of
:mod:`core.collection_source` and the cue points of :mod:`core.cue_points`):

- every track as a :class:`core.rekordbox.Track`, as :func:`core.rekordbox.parse_entry` builds
  it from an NML ``ENTRY`` — for the PIONEER database and the analysis files;
- an NML ``ENTRY`` per track, for the Traktor playlists written to the stick
  (:mod:`core.traktor_playlists`), laid out as Traktor writes them: LOCATION, ALBUM, INFO,
  TEMPO, MUSICAL_KEY, then one CUE_V2 per cue (the grid marker with its GRID BPM).

What memory does not give stays out (Traktor's AUDIO_ID fingerprint, loudness, stripe): a
Traktor reading the stick's playlist analyses such a track again, as it does any new file.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from core import rekordbox as rb
from core.constants import AUDIO_EXTS
from core.cue_points import GRID_MARKER, TrackCues


def _track(path: str, tags: dict, cues: TrackCues | None) -> rb.Track:
    bpm = float(tags.get("traktor_bpm") or 0.0)
    key = int(tags.get("musical_key", -1))
    raw_bitrate = int(tags.get("bitrate") or 0)  # Traktor's bits/s, as the NML's BITRATE
    return rb.Track(
        path=path,
        title=str(tags.get("title") or ""),
        artist=str(tags.get("artist") or ""),
        album=str(tags.get("album") or ""),
        genre=str(tags.get("genre") or ""),
        label=str(tags.get("label") or ""),
        comment=str(tags.get("comment") or ""),
        bpm=bpm,
        musical_key=key if key >= 0 else None,
        rating=int(tags.get("ranking") or 0),
        play_count=int(tags.get("play_count") or 0),
        bitrate=raw_bitrate // 1000,
        total_time_s=float(tags.get("playtime_s") or 0),
        date_added=rb._iso_date(tags.get("import_date")),
        release_date=rb._iso_date(tags.get("release_date")),
        cues=[
            rb.Cue(
                name=c.name,
                type=c.type,
                start_ms=c.start,
                len_ms=c.length,
                hotcue=c.hotcue,
                grid_bpm=bpm if c.type == GRID_MARKER and bpm > 0 else None,
            )
            for c in (cues.cues if cues is not None else ())
        ],
    )


def export_collection(
    meta: dict[str, dict], track_cues: list[TrackCues]
) -> tuple[dict[str, rb.Track], list[str], int]:
    """(track by path, paths in collection order, entries skipped), as the NML reader of the
    export (``ui.library_helpers._read_export_collection``) returns them."""
    by_cues = {t.path: t for t in track_cues}
    by_path: dict[str, rb.Track] = {}
    order: list[str] = []
    skipped = 0
    for path, tags in meta.items():
        if not path or Path(path).suffix.lower() not in AUDIO_EXTS:
            skipped += 1
            continue
        by_path[path] = _track(path, tags, by_cues.get(path))
        order.append(path)
    return by_path, order, skipped


def _num(value: float) -> str:
    return f"{value:.6f}"


def _traktor_date(iso: str) -> str:
    """Rekordbox's ``YYYY-MM-DD`` back to Traktor's ``YYYY/M/D``."""
    try:
        y, m, d = (int(b) for b in iso.split("-"))
    except ValueError:
        return ""
    return f"{y}/{m}/{d}"


def entry(track: rb.Track) -> ET.Element:
    """An NML ``ENTRY`` for *track*. Its LOCATION is a placeholder: the stick's playlist
    writer replaces it with the copy's (``core.traktor_playlists._entry_for``)."""
    el = ET.Element("ENTRY")
    if track.title:
        el.set("TITLE", track.title)
    if track.artist:
        el.set("ARTIST", track.artist)
    p = Path(track.path)
    ET.SubElement(el, "LOCATION", DIR=f"/:{'/:'.join(p.parent.parts[1:])}/:", FILE=p.name)
    if track.album:
        ET.SubElement(el, "ALBUM", TITLE=track.album)
    info: dict[str, str] = {}
    if track.bitrate:
        info["BITRATE"] = str(track.bitrate * 1000)
    for attr, value in (("GENRE", track.genre), ("LABEL", track.label), ("COMMENT", track.comment)):
        if value:
            info[attr] = value
    if track.play_count:
        info["PLAYCOUNT"] = str(track.play_count)
    if track.total_time_s:
        info["PLAYTIME"] = str(round(track.total_time_s))
        info["PLAYTIME_FLOAT"] = _num(track.total_time_s)
    if track.rating:
        info["RANKING"] = str(track.rating)
    for attr, iso in (("IMPORT_DATE", track.date_added), ("RELEASE_DATE", track.release_date)):
        if iso and _traktor_date(iso):
            info[attr] = _traktor_date(iso)
    ET.SubElement(el, "INFO", info)
    if track.bpm > 0:
        ET.SubElement(el, "TEMPO", BPM=_num(track.bpm), BPM_QUALITY="100.000000")
    if track.musical_key is not None:
        ET.SubElement(el, "MUSICAL_KEY", VALUE=str(track.musical_key))
    for cue in track.cues:
        attrs = {
            "NAME": cue.name or "n.n.",
            "DISPL_ORDER": "0",
            "TYPE": str(cue.type),
            "START": _num(cue.start_ms),
            "LEN": _num(cue.len_ms),
            "REPEATS": "-1",
            "HOTCUE": str(cue.hotcue),
        }
        node = ET.SubElement(el, "CUE_V2", attrs)
        if cue.is_grid and cue.grid_bpm:
            ET.SubElement(node, "GRID", BPM=_num(cue.grid_bpm))
    return el


def entries(meta: dict[str, dict], track_cues: list[TrackCues]) -> dict[str, ET.Element]:
    """path → ``ENTRY``, as :func:`core.traktor_playlists.entries_by_path` gives them."""
    by_path, order, _skipped = export_collection(meta, track_cues)
    return {str(Path(p)): entry(by_path[p]) for p in order}
