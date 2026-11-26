"""Values for TK's own columns in Traktor's list (libtk tk_columns.mm).

Score, BPM and Key come from here (each result's score and BPM/key match, sent with every list
push; libtk shows them first in TK's list only, the match text coloured as in TK's table). Directory and Directory (short) libtk works out from each row's track,
in any playlist; Directory (short) with Traktor's music folders, sent once (User, 2026-11-24:
"Tracks/2025-10/"). Traktor's main thread (Qt's, there).
"""

from __future__ import annotations

import ctypes
import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from workers.native_lib import process_symbol

log = logging.getLogger(__name__)


def _call(name: str):
    fn = process_symbol(name)
    if fn is not None:
        fn.argtypes = [ctypes.c_char_p]
    return fn


def score_text(composite: float | None) -> str:
    """The score as TK's own table shows it: 0–100, whole."""
    return "" if composite is None else f"{composite:.0f}"


@dataclass(frozen=True)
class MatchCells:
    """One result's TK cells: SCORE, and the BPM and KEY match text as TK's own table shows it,
    each with its text colour (0xAARRGGBB; None = Traktor's own colour)."""

    score: float | None
    bpm: str = ""
    bpm_argb: int | None = None
    key: str = ""
    key_argb: int | None = None


def _argb(colour: int | None) -> str:
    return "" if colour is None else f"{colour & 0xFFFFFFFF:08x}"


def _line(path: str, c: MatchCells) -> str:
    fields = (score_text(c.score), c.bpm, _argb(c.bpm_argb), c.key, _argb(c.key_argb))
    return "\t".join((path, *(f.replace("\t", " ").replace("\n", " ") for f in fields)))


def set_scores(cells: Mapping[str, MatchCells]) -> bool:
    """libtk's line per result: path, score, BPM text, its colour, KEY text, its colour."""
    fn = _call("tk_cols_set_values")
    if fn is None:
        return False
    text = "\n".join(_line(p, c) for p, c in cells.items() if "\t" not in p and "\n" not in p)
    fn(text.encode())
    return True


def set_roots(folders: Iterable[str]) -> bool:
    fn = _call("tk_cols_set_roots")
    if fn is None:
        return False
    fn("\n".join(str(f) for f in folders).encode())
    return True
