"""The Library's one-line account of how far analysis has got.

It names what tracks are missing, by compute group, rather than a version
number: "Structure · bassline: 40" tells the user what a scan will do, and
"38 have no BPM in Traktor" tells them what a scan cannot — those features
are measured in bars, and the tempo comes from Traktor.
"""

from __future__ import annotations

from core.db import AnalysisGaps
from core.features import COMPUTE_GROUPS

#: Order the groups are listed in: the order a scan works through them.
_ORDER = ("signal", "melodic", "shape", "ml", "embed")


def describe(gaps: AnalysisGaps) -> str:
    """The stats line for *gaps*."""
    if gaps.total == 0:
        return "No tracks in database — scan to analyse the collection"
    s = "s" if gaps.total != 1 else ""
    head = f"{gaps.total:,} track{s} in database"
    if gaps.complete == gaps.total:
        return f"{head} — all fully analysed"

    parts = []
    for group in sorted(
        gaps.missing, key=lambda g: (_ORDER.index(g) if g in _ORDER else len(_ORDER), g)
    ):
        label = COMPUTE_GROUPS.get(group, {}).get("label", group)
        part = f"{label}: {gaps.missing[group]:,}"
        if group == "shape" and gaps.shape_without_bpm:
            part += (
                f" ({gaps.shape_without_bpm:,} have no BPM in Traktor — analyse them there first)"
            )
        parts.append(part)
    return f"{head} — {gaps.complete:,} fully analysed. Still missing: " + "; ".join(parts)
