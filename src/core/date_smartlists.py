"""Write date smartlists into Traktor's collection: one per calendar window.

Each kind — Imported, Released — is split into windows of a chosen size
(3, 6 or 12 months, or off), one smartlist per window, newest first, back to
the oldest dated track; empty windows are skipped::

    Track Kommander/
        Imported/
            imported-2026-07 to 09
            imported-2026-04 to 06
            …
        Released/
            released-2026-01 to 06
            …

Each list's name is the kind's prefix (settable, ``imported-`` and
``released-`` by default, may be empty) then its window. Window names use
two-digit months so they sort in date order wherever they are
sorted by name, and plain ASCII: Traktor 4.5.1 showed an en dash as "™".

They are smartlists: Traktor evaluates each one live, so the current
window fills up as tracks arrive. A new window needs a new run. Running
again rebuilds the Imported and Released folders — removing a kind set to
off — and leaves anything else under Track Kommander alone (a Spotify
folder, later). A window's list keeps its UUID across runs.

The query syntax
----------------

Copied from a date smartlist Traktor 4.5.1 saved itself::

    $IMPORTDATE > 2026-03-31 & $IMPORTDATE < 2026-07-01

``&`` for "and", ISO dates unquoted, and only "is after" / "is before" — no
"on or after". Anything else was written back empty when Traktor next saved
the collection (``AND`` and quoted dates), or lost a condition (``>=``). So
a window is after the day before its first day and before the first day of
the next. Built by :func:`range_query` alone.
"""

from __future__ import annotations

import logging
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from core.collection_write import write_collection

log = logging.getLogger(__name__)

ROOT_FOLDER = "Kommander"
#: The folder's name until 2026-11 (User: "just Kommander"). A collection that still has it gets
#: it renamed in place, lists and order kept, instead of a second folder beside it.
LEGACY_ROOT_FOLDER = "Track Kommander"
#: Seeds the smartlists' UUIDs. Frozen at the old folder name: a renamed folder must keep its
#: lists' identities, or Traktor would see every list as new.
_UUID_SEED = LEGACY_ROOT_FOLDER

#: Kind name → (Traktor query field, NML ``INFO`` attribute).
DATE_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("Imported", "IMPORTDATE", "IMPORT_DATE"),
    ("Released", "RELEASEDATE", "RELEASE_DATE"),
)

#: Window sizes offered, in months.
WINDOW_MONTHS: tuple[int, ...] = (3, 6, 12)

#: Kind → window size in months, or None for no lists. Release dates are
#: often only a year (stored as 1 January), so Released starts off.
DEFAULT_CHOICE: dict[str, int | None] = {"Imported": 3, "Released": None}

Choice = dict[str, "int | None"]

#: Kind → text put before each of its lists' names.
DEFAULT_PREFIX: dict[str, str] = {"Imported": "imported-", "Released": "released-"}

#: Seed for stable UUIDs: a window's list keeps its UUID on every run.
_UUID_NAMESPACE = uuid.UUID("5b0e7c1e-7a4d-4f5e-9d61-2f7e1c0d8a11")


@dataclass(frozen=True)
class Window:
    start: date  # first day, inclusive
    end: date  # first day of the next window, exclusive
    name: str


@dataclass(frozen=True)
class SmartlistResult:
    lists: int
    replaced: bool
    backup: Path


# ── Windows ─────────────────────────────────────────────────────────────────


def _add_months(day: date, months: int) -> date:
    index = day.year * 12 + day.month - 1 + months
    return date(index // 12, index % 12 + 1, 1)


def window_of(day: date, months: int) -> Window:
    """The calendar window of *months* holding *day*: quarters, halves, years."""
    if months not in WINDOW_MONTHS:
        raise ValueError(f"unsupported window of {months} months")
    first_month = (day.month - 1) // months * months + 1
    start = date(day.year, first_month, 1)
    end = _add_months(start, months)
    if months == 12:
        name = f"{start.year}"
    else:
        last_month = (end.month - 2) % 12 + 1
        name = f"{start.year}-{start.month:02d} to {last_month:02d}"
    return Window(start, end, name)


def windows_for(dates: list[date], months: int) -> list[Window]:
    """The windows holding at least one of *dates*, newest first."""
    seen: dict[date, Window] = {}
    for d in dates:
        w = window_of(d, months)
        seen.setdefault(w.start, w)
    return [seen[k] for k in sorted(seen, reverse=True)]


def range_query(field: str, window: Window) -> str:
    """Traktor query for "*field* within *window*": after the day before its
    start, before the start of the next window — Traktor has no ``>=``."""
    day_before = window.start - timedelta(days=1)
    return f"${field} > {day_before.isoformat()} & ${field} < {window.end.isoformat()}"


# ── The collection ──────────────────────────────────────────────────────────


def _parse_traktor_date(text: str | None) -> date | None:
    if not text:
        return None
    bits = text.split("/")
    try:
        y, m, d = (int(b) for b in bits)
        return date(y, m, d)
    except ValueError:
        return None


def collection_dates(tree: ET.ElementTree[ET.Element], info_attr: str) -> list[date]:
    """Every track's date from its ``INFO`` attribute, skipping undated ones."""
    out = []
    for info in tree.getroot().iterfind("COLLECTION/ENTRY/INFO"):
        d = _parse_traktor_date(info.get(info_attr))
        if d is not None and d.year > 1900:
            out.append(d)
    return out


def _uuid(kind: str, window: Window) -> str:
    return uuid.uuid5(_UUID_NAMESPACE, f"{_UUID_SEED}/{kind}/{window.start.isoformat()}").hex


def _folder(name: str) -> ET.Element:
    node = ET.Element("NODE", TYPE="FOLDER", NAME=name)
    ET.SubElement(node, "SUBNODES", COUNT="0")
    return node


def _subnodes_of(folder: ET.Element) -> ET.Element:
    """The folder's ``SUBNODES``, made when a hand-edited one lacks it."""
    subnodes = folder.find("SUBNODES")
    if subnodes is None:
        subnodes = ET.SubElement(folder, "SUBNODES", COUNT="0")
    return subnodes


def _recount(subnodes: ET.Element) -> None:
    subnodes.set("COUNT", str(len(subnodes.findall("NODE"))))


def build_kind_folder(kind: str, field: str, windows: list[Window], prefix: str = "") -> ET.Element:
    """A kind's folder: one smartlist per window, named *prefix* + window."""
    node = _folder(kind)
    subnodes = _subnodes_of(node)
    for w in windows:
        item = ET.SubElement(subnodes, "NODE", TYPE="SMARTLIST", NAME=prefix + w.name)
        smart = ET.SubElement(item, "SMARTLIST", UUID=_uuid(kind, w))
        ET.SubElement(smart, "SEARCH_EXPRESSION", VERSION="1", QUERY=range_query(field, w))
    _recount(subnodes)
    return node


def _root_subnodes(tree: ET.ElementTree[ET.Element]) -> ET.Element:
    root = tree.getroot().find("PLAYLISTS/NODE")
    if root is None or root.get("NAME") != "$ROOT":
        raise ValueError("collection has no playlist root")
    return _subnodes_of(root)


def _child_folder(subnodes: ET.Element, name: str) -> ET.Element | None:
    return next(
        (
            n
            for n in subnodes.findall("NODE")
            if n.get("TYPE") == "FOLDER" and n.get("NAME") == name
        ),
        None,
    )


def install(
    tree: ET.ElementTree[ET.Element],
    choice: Choice | None = None,
    prefixes: dict[str, str] | None = None,
) -> tuple[int, bool]:
    """Put the window lists for *choice* into *tree*, named with *prefixes*
    (a kind left out takes its default prefix).

    Returns (lists written, whether earlier ones were replaced).
    """
    choice = DEFAULT_CHOICE if choice is None else choice
    prefixes = {**DEFAULT_PREFIX, **(prefixes or {})}
    top = _root_subnodes(tree)
    ours = _child_folder(top, ROOT_FOLDER)
    if ours is None:
        ours = _child_folder(top, LEGACY_ROOT_FOLDER)
        if ours is not None:
            ours.set("NAME", ROOT_FOLDER)
            log.info('date smartlists: folder "%s" renamed "%s"', LEGACY_ROOT_FOLDER, ROOT_FOLDER)
    if ours is None:
        ours = _folder(ROOT_FOLDER)
        top.append(ours)
        _recount(top)
    inner = _subnodes_of(ours)
    written, replaced = 0, False
    for kind, field, info_attr in DATE_FIELDS:
        old = _child_folder(inner, kind)
        months = choice.get(kind)
        new = None
        if months:
            windows = windows_for(collection_dates(tree, info_attr), months)
            if windows:
                new = build_kind_folder(kind, field, windows, prefixes.get(kind, ""))
                written += len(windows)
        if old is not None:
            replaced = True
            if new is None:
                inner.remove(old)
            else:
                # Same place in the folder, so the user's ordering survives.
                inner[list(inner).index(old)] = new
        elif new is not None:
            inner.append(new)
    _recount(inner)
    return written, replaced


def write_to_collection(
    nml_path: Path, choice: Choice | None = None, prefixes: dict[str, str] | None = None
) -> SmartlistResult:
    """Install the window lists for *choice* into *nml_path*, after a ``.bak``."""
    nml_path = Path(nml_path)
    tree = ET.parse(nml_path)
    lists, replaced = install(tree, choice, prefixes)
    backup = write_collection(tree, nml_path)
    log.info(
        "date smartlists %s: %d list(s), %s, in %s",
        DEFAULT_CHOICE if choice is None else choice,
        lists,
        "replaced" if replaced else "created",
        nml_path,
    )
    return SmartlistResult(lists=lists, replaced=replaced, backup=backup)
