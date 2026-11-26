"""Replay Traktor's play history against the matcher: where did the track
actually played next rank, had this ranking been on screen?

A sanity check, not a gate. The history was made by someone browsing —
with Track Kommander's suggestions open, or their own crates — so it
rewards whatever resembles how those choices were made: a ranking like
today's, or a label-sorted crate. Use it to catch a change that makes
things clearly worse, and compare configurations on the same history; do
not read a higher score as "better transitions". ``before`` limits the
replay to sets played before a date (say, before the app was used).

Qt-free: :func:`transitions` reads the history files, :func:`replay` ranks;
``scripts/replay_history.py`` is the command line around them.
"""

from __future__ import annotations

import statistics
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from core.features import BY_KEY, ActiveAxis
from core.library_snapshot import Snapshot
from core.playlists import _primarykey_to_path
from core.ranking import rank_axes

#: A track played for less than this was an audition or a false start, not
#: a choice: it neither ends nor starts a transition.
MIN_PLAYED_S = 60.0


@dataclass(frozen=True)
class Transition:
    played: str  # path of the track that was playing
    next: str  # path of the track played after it
    session: str  # history file name


def _session_date(name: str) -> date | None:
    """``history_2023y07m10d_06h26m50s.nml`` → 2023-07-10."""
    try:
        y = int(name[8:12])
        m = int(name[13:15])
        d = int(name[16:18])
        return date(y, m, d)
    except (ValueError, IndexError):
        return None


def played_tracks(history_file: Path, min_played: float = MIN_PLAYED_S) -> list[str]:
    """The tracks of one history file that played long enough, in play order."""
    try:
        root = ET.parse(history_file).getroot()
    except (ET.ParseError, OSError):
        return []
    plays: list[tuple[int, int, int, str]] = []
    for n, entry in enumerate(root.iter("ENTRY")):
        key = entry.find("PRIMARYKEY")
        data = entry.find("EXTENDEDDATA")
        if key is None or data is None or key.get("TYPE") != "TRACK":
            continue
        try:
            played = float(data.get("DURATION", "0") or 0)
        except ValueError:
            continue
        if played < min_played:
            continue
        start_day = int(data.get("STARTDATE", "0") or 0)
        start_s = int(data.get("STARTTIME", "0") or 0)
        plays.append((start_day, start_s, n, _primarykey_to_path(key.get("KEY", ""))))
    return [path for *_order, path in sorted(plays)]


def transitions(
    history_files: Iterable[Path],
    library: set[str],
    *,
    before: date | None = None,
    min_played: float = MIN_PLAYED_S,
) -> list[Transition]:
    """Every adjacent pair of long-enough plays, both tracks in *library*."""
    out: list[Transition] = []
    for f in sorted(history_files):
        when = _session_date(f.name)
        if before is not None and (when is None or when >= before):
            continue
        plays = played_tracks(f, min_played)
        for a, b in zip(plays, plays[1:], strict=False):
            if a != b and a in library and b in library:
                out.append(Transition(a, b, f.name))
    return out


def axes_for(snap: Snapshot, played: int, spec: Sequence[tuple[str, float]]) -> list[ActiveAxis]:
    """*spec* as active axes for one source track: scalar axes target the
    source's own value, as the app's default "follow the track" does."""
    axes = []
    for key, strength in spec:
        feature = BY_KEY.get(key)
        if feature is None or strength <= 0:
            continue
        value = 0.5
        if not feature.relational and feature.column:
            values, has = snap.normalised(feature)
            value = float(values[played]) if has[played] else 0.5
        axes.append(ActiveAxis(key, value=value, strength=strength))
    return axes


@dataclass
class Result:
    name: str
    ranks: list[int | None] = field(default_factory=list)  # 0-based; None: not ranked

    @property
    def n(self) -> int:
        return len(self.ranks)

    def recall(self, k: int) -> float:
        return sum(r is not None and r < k for r in self.ranks) / self.n if self.n else 0.0

    @property
    def mrr(self) -> float:
        return sum(1.0 / (r + 1) for r in self.ranks if r is not None) / self.n if self.n else 0.0

    @property
    def median_rank(self) -> float | None:
        found = [r + 1 for r in self.ranks if r is not None]
        return statistics.median(found) if found else None


def replay(
    snap: Snapshot,
    played: Sequence[Transition],
    name: str,
    spec: Sequence[tuple[str, float]],
) -> Result:
    """Rank the library against each transition's first track with *spec*;
    record where the second landed."""
    result = Result(name)
    for t in played:
        i = snap.index.get(t.played)
        j = snap.index.get(t.next)
        if i is None or j is None:
            continue
        ranked = rank_axes(snap, snap.rows[i], axes_for(snap, i, spec))
        result.ranks.append(ranked.rank_of(j))
    return result


def configurations(
    current: Sequence[tuple[str, float]],
) -> list[tuple[str, list[tuple[str, float]]]]:
    """What to compare: the current setup, then each released axis added to it
    (at full strength) or taken out of it, one at a time — what each axis
    changes, on the same transitions."""
    from core.features import FEATURES

    active = [(k, s) for k, s in current if s > 0]
    on = {k for k, _s in active}
    out = [("Your setup", active)]
    for f in FEATURES:
        if f.key in on:
            out.append((f"− {f.label}", [(k, s) for k, s in active if k != f.key]))
        else:
            out.append((f"+ {f.label}", [*active, (f.key, 1.0)]))
    return out
