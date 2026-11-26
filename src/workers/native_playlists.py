"""Bring the Kommander date playlists up to date inside Traktor, through libtk.

The plan is :func:`core.date_playlists.plan`; this compares it with the playlists Traktor has
(its memory, :func:`core.playlists.parse_playlists`) and makes only the changes:

- a list that is no longer planned, or is still a smartlist (written by standalone TK or an
  older launcher), is deleted; a kind that is off loses its folder;
- a planned list is created, or refilled when its tracks differ.

Never collection.nml (User, 2026-11-24). Traktor's main thread.
"""

from __future__ import annotations

import ctypes
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from core.date_playlists import SEP, DateList
from core.date_smartlists import ROOT_FOLDER
from core.playlists import Playlist
from workers.native_lib import process_lib

log = logging.getLogger(__name__)


@dataclass
class Api:
    """libtk's playlist calls (tk_kplaylists.mm); each returns 1 done, 0 refused, -1 not here."""

    folder: Callable[[str], int]
    delete: Callable[[str], int]
    delete_folder: Callable[[str], int]
    fill: Callable[[str, list[str]], int]


def attach() -> Api | None:
    """libtk's calls inside Traktor; None elsewhere or with an older libtk."""
    lib = process_lib()
    names = ("tk_pl_folder", "tk_pl_delete", "tk_pl_delete_folder", "tk_pl_fill")
    if lib is None or not all(hasattr(lib, n) for n in names):
        return None
    for n in names[:3]:
        getattr(lib, n).argtypes = [ctypes.c_char_p]
        getattr(lib, n).restype = ctypes.c_int
    lib.tk_pl_fill.argtypes = [ctypes.c_char_p, ctypes.c_char_p]
    lib.tk_pl_fill.restype = ctypes.c_int

    def fill(path: str, paths: list[str]) -> int:
        return int(lib.tk_pl_fill(path.encode(), "\n".join(paths).encode()))

    return Api(
        folder=lambda p: int(lib.tk_pl_folder(p.encode())),
        delete=lambda p: int(lib.tk_pl_delete(p.encode())),
        delete_folder=lambda p: int(lib.tk_pl_delete_folder(p.encode())),
        fill=fill,
    )


@dataclass
class SyncResult:
    created: list[str] = field(default_factory=list)
    refilled: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    unchanged: int = 0


def _path(*parts: str) -> str:
    return SEP.join(parts)


def sync(planned: dict[str, list[DateList]], existing: list[Playlist], api: Api) -> SyncResult:
    """Make Traktor's Kommander/<kind> lists match *planned*."""
    result = SyncResult()
    api.folder(ROOT_FOLDER)  # 0 when it is already there
    for kind, lists in planned.items():
        have = {p.name: p for p in existing if p.path == (ROOT_FOLDER, kind)}
        if not lists:
            if have:
                for name in have:
                    api.delete(_path(ROOT_FOLDER, kind, name))
                    result.deleted.append(_path(ROOT_FOLDER, kind, name))
                api.delete_folder(_path(ROOT_FOLDER, kind))
            continue
        api.folder(_path(ROOT_FOLDER, kind))
        wanted = {d.name for d in lists}
        for name, pl in have.items():
            if name not in wanted or pl.smart:
                api.delete(_path(ROOT_FOLDER, kind, name))
                result.deleted.append(_path(ROOT_FOLDER, kind, name))
        for d in lists:
            old = have.get(d.name)
            if old is not None and not old.smart and tuple(old.paths) == d.paths:
                result.unchanged += 1
                continue
            api.fill(d.path, list(d.paths))
            (result.refilled if old is not None and not old.smart else result.created).append(
                d.path
            )
    return result


def refresh() -> SyncResult | None:
    """Inside Traktor: the date playlists from Traktor's memory, with the options saved on
    standalone TK's page. None when libtk or the collection is not there."""
    from core.collection_source import collection_metadata
    from core.date_playlists import plan, saved_options
    from core.playlists import parse_playlists

    api = attach()
    if api is None:
        return None
    meta = collection_metadata(None)
    if not meta:
        log.warning("date playlists: Traktor's collection is not readable, nothing changed")
        return None
    choice, prefixes = saved_options()

    result = sync(plan(meta, choice, prefixes), parse_playlists(Path()), api)
    log.info(
        "date playlists %s: %d created, %d refilled, %d deleted, %d unchanged",
        choice,
        len(result.created),
        len(result.refilled),
        len(result.deleted),
        result.unchanged,
    )
    return result
