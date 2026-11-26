"""Fill empty Traktor fields from accepted Discogs matches.

Two steps, so the user sees what will change before anything does:

1. :func:`plan_fill` reads ``collection.nml`` and returns, per track, the
   fields its Discogs match would fill (:func:`core.discogs_fields.planned_changes`
   decides: mapped fields only, empty ones only).
2. :func:`apply_fill` re-reads the collection, applies exactly those changes
   where the field is still as planned, and writes it the one safe way
   (:func:`core.collection_write.write_collection`: dated backup, atomic
   replace). Traktor must be closed — the UI checks that before calling.

Our own database picks the new values up on the next scan, the same way it
picks up an edit made in Traktor.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from core.collection_write import write_collection
from core.discogs_fields import TRAKTOR_FIELDS, DiscogsRecord, FieldChange, planned_changes


@dataclass(frozen=True)
class TrackFill:
    path: str
    artist: str
    title: str
    changes: tuple[FieldChange, ...]
    release_id: int = 0  # the Discogs release the values come from


def _entries_by_path(tree: ET.ElementTree[ET.Element]) -> dict[str, ET.Element]:
    from core.traktor import _nml_build_path

    out: dict[str, ET.Element] = {}
    for entry in tree.getroot().iter("ENTRY"):
        loc = entry.find("LOCATION")
        if loc is None:
            continue  # a playlist's reference to a track, not the track
        path = _nml_build_path(loc)
        if path:
            out[path] = entry
    return out


def _current(entry: ET.Element) -> dict[str, str]:
    info = entry.find("INFO")
    if info is None:
        return {}
    return {f.attr: info.get(f.attr, "") for f in TRAKTOR_FIELDS}


def plan_fill(
    nml_path: Path,
    records: Mapping[str, DiscogsRecord],
    mapping: Mapping[str, str],
    replace_placeholder_dates: bool = False,
) -> list[TrackFill]:
    """What filling would change, track by track; tracks with nothing to fill
    are left out."""
    tree = ET.parse(str(nml_path))
    plan: list[TrackFill] = []
    for path, entry in _entries_by_path(tree).items():
        record = records.get(path)
        if record is None:
            continue
        changes = planned_changes(record, _current(entry), mapping, replace_placeholder_dates)
        if changes:
            plan.append(
                TrackFill(
                    path,
                    entry.get("ARTIST", ""),
                    entry.get("TITLE", ""),
                    tuple(changes),
                    record.release_id,
                )
            )
    return sorted(plan, key=lambda t: (t.artist.casefold(), t.title.casefold()))


#: What Traktor writes before an entry's INFO; a new INFO goes after these.
_BEFORE_INFO = ("LOCATION", "ALBUM", "MODIFICATION_INFO")


def _new_info(entry: ET.Element) -> ET.Element:
    """An empty INFO in the place Traktor keeps it, not after the cues."""
    at = 0
    for i, child in enumerate(entry):
        if child.tag in _BEFORE_INFO:
            at = i + 1
    info = ET.Element("INFO")
    entry.insert(at, info)
    return info


def apply_fill(nml_path: Path, plan: Sequence[TrackFill]) -> int:
    """Write *plan* into the collection. Returns how many tracks changed.

    A field is set only if it still holds what the plan saw — so an edit made
    in Traktor between preview and write is never overwritten. Nothing is
    written when nothing would change.
    """
    tree = ET.parse(str(nml_path))
    entries = _entries_by_path(tree)
    changed = 0
    for fill in plan:
        entry = entries.get(fill.path)
        if entry is None:
            continue
        info = entry.find("INFO")
        if info is None:
            info = _new_info(entry)
        touched = False
        for change in fill.changes:
            if info.get(change.field.attr, "").strip() != change.old:
                continue
            info.set(change.field.attr, change.new)
            touched = True
        changed += touched
    if changed:
        write_collection(tree, nml_path)
    return changed
