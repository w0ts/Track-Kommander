"""What a scan has to do: which tracks, and which compute groups for each.

Both answers come from one integer per track — see :mod:`core.feature_bits`.
Compute stays grouped because the features share a decode, an HPSS and a CQT;
only the bookkeeping is per feature.
"""

from __future__ import annotations

from core.feature_bits import missing_features, stale_clause


def computable_features() -> tuple:
    """The features a scan actually works out.

    Excludes the core group — BPM, key, rating and genre come from Traktor's
    NML and are re-read free on every scan. They are not switchable, and
    counting them towards staleness would make every unrated track look
    permanently unanalysed.
    """
    from core.features import FEATURES

    return tuple(f for f in FEATURES if f.column and f.group != "core")


def stale_paths(want: int, *, conn=None) -> set[str]:
    """Paths that have not been analysed for every feature in *want*.

    Asks for the stale tracks rather than the current ones: normally there
    are none, and returning nothing is much cheaper than returning the whole
    library.
    """
    if not want:
        return set()
    sql, params = stale_clause(want)
    query = f"SELECT path FROM tracks WHERE {sql} OR computed IS NULL"

    if conn is not None:
        return {row[0] for row in conn.execute(query, params)}
    from core.db import get_conn

    with get_conn() as c:
        return {row[0] for row in c.execute(query, params)}


def features_to_compute(*, have: int | None, want: int) -> set[str]:
    """The compute groups needed to fill in what this track is missing."""
    from core.features import BY_KEY

    missing = missing_features(have, want)
    return {BY_KEY[key].group for key in missing if key in BY_KEY}
