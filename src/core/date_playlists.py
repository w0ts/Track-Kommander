"""The Kommander date playlists as plain playlists, kept by TK inside Traktor.

Standalone TK writes them into ``collection.nml`` as smartlists
(:mod:`core.date_smartlists`). Inside Traktor TK never touches that file (User,
2026-11-24): it works out each window's tracks itself, from the import and
release dates Traktor holds in memory, and has libtk create and fill normal
playlists in the same place, with the same names::

    Kommander/
        Imported/
            imported-2026-07 to 09
            …

A playlist does not fill itself the way a smartlist does: TK brings them up to
date each time it starts inside Traktor. This module only plans; applying the
plan is :mod:`workers.native_playlists`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from core.date_smartlists import (
    DATE_FIELDS,
    DEFAULT_CHOICE,
    DEFAULT_PREFIX,
    ROOT_FOLDER,
    Choice,
    window_of,
    windows_for,
)

#: The NML INFO attribute (DATE_FIELDS) → the key the collection snapshot uses.
_META_KEY = {"IMPORT_DATE": "import_date", "RELEASE_DATE": "release_date"}

#: Traktor's separator in a StructurePath ("Kommander\\Imported\\imported-2026-07 to 09").
SEP = "\\"


@dataclass(frozen=True)
class DateList:
    """One window's playlist: where it goes and what it holds, newest track first."""

    kind: str
    name: str
    paths: tuple[str, ...]

    @property
    def path(self) -> str:
        return SEP.join((ROOT_FOLDER, self.kind, self.name))


def _date(text: str | None) -> date | None:
    """A snapshot date ("2023/12/29", the NML's form) or None when there is none."""
    if not text:
        return None
    try:
        y, m, d = (int(b) for b in text.split("/"))
        found = date(y, m, d)
    except ValueError:
        return None
    return found if found.year > 1900 else None


def plan(
    meta: dict[str, dict], choice: Choice, prefixes: dict[str, str]
) -> dict[str, list[DateList]]:
    """Kind → its window playlists, OLDEST window first (the order they are created in, so a
    new window lands at the end). A kind that is off maps to ``[]``: its lists go."""
    out: dict[str, list[DateList]] = {}
    for kind, _field, attr in DATE_FIELDS:
        months = choice.get(kind)
        if not months:
            out[kind] = []
            continue
        dated: list[tuple[date, str]] = []
        for path, tags in meta.items():
            d = _date(tags.get(_META_KEY[attr]))
            if d is not None:
                dated.append((d, path))
        by_window: dict[date, list[tuple[date, str]]] = {}
        for d, path in dated:
            by_window.setdefault(window_of(d, months).start, []).append((d, path))
        prefix = prefixes.get(kind, DEFAULT_PREFIX.get(kind, ""))
        lists = []
        for w in reversed(windows_for([d for d, _ in dated], months)):
            tracks = sorted(by_window[w.start], key=lambda dp: (dp[0], dp[1]), reverse=True)
            lists.append(DateList(kind, prefix + w.name, tuple(p for _, p in tracks)))
        out[kind] = lists
    return out


def saved_options() -> tuple[Choice, dict[str, str]]:
    """The window sizes and name prefixes last saved on standalone TK's Automatic Playlists
    page (the same QSettings), else the defaults."""
    from PySide6.QtCore import QSettings

    settings = QSettings()
    choice: Choice = {}
    prefixes: dict[str, str] = {}
    for kind, _field, _attr in DATE_FIELDS:
        months = settings.value(f"date_windows/{kind}", DEFAULT_CHOICE.get(kind) or 0, type=int)
        choice[kind] = int(months) if isinstance(months, int) and months > 0 else None
        prefix = settings.value(f"date_prefix/{kind}", DEFAULT_PREFIX.get(kind, ""), type=str)
        prefixes[kind] = prefix if isinstance(prefix, str) else DEFAULT_PREFIX.get(kind, "")
    return choice, prefixes
