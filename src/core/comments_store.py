"""Per-track and per-playlist comments (goal 5).

A sidecar to Traktor's own comment fields, stored in TK's SQLite DB
(``track_comments``, created by :func:`core.db.init_db`) and never written back
to the collection. A comment is keyed by the track's file path plus a *playlist*
scope: :data:`GENERAL` (the empty string) is the track's general note, while a
playlist name scopes a comment to that playlist — so the same track can carry a
different note per set.

Setting a blank comment clears it (the row is removed), so a track with no note
holds no row and :func:`has_any_comment` is cheap.
"""

from __future__ import annotations

import time

from core.db import get_conn

#: Scope for a track's general (not playlist-specific) comment.
GENERAL = ""


def get_comment(path: str, playlist: str = GENERAL) -> str:
    """The comment on *path* in *playlist* (:data:`GENERAL` for the track's
    general note), or ``""`` when there is none."""
    with get_conn() as c:
        row = c.execute(
            "SELECT comment FROM track_comments WHERE path = ? AND playlist = ?",
            (path, playlist),
        ).fetchone()
    return row["comment"] if row else ""


def set_comment(path: str, comment: str, playlist: str = GENERAL) -> None:
    """Set the comment on *path* in *playlist*; a blank *comment* clears it.

    The text is stripped; writing only whitespace removes the row, so an empty
    note never leaves a stale entry behind.
    """
    text = (comment or "").strip()
    with get_conn() as c:
        if text:
            c.execute(
                """
                INSERT INTO track_comments (path, playlist, comment, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(path, playlist)
                DO UPDATE SET comment = excluded.comment, updated_at = excluded.updated_at
                """,
                (path, playlist, text, time.time()),
            )
        else:
            c.execute(
                "DELETE FROM track_comments WHERE path = ? AND playlist = ?",
                (path, playlist),
            )


def comments_for_track(path: str) -> dict[str, str]:
    """Every comment on *path*, as ``{playlist: comment}`` (:data:`GENERAL`
    included when set). Empty when the track has no notes."""
    with get_conn() as c:
        rows = c.execute(
            "SELECT playlist, comment FROM track_comments WHERE path = ?",
            (path,),
        ).fetchall()
    return {r["playlist"]: r["comment"] for r in rows}


def comments_for_playlist(playlist: str) -> dict[str, str]:
    """Every track commented in *playlist*, as ``{path: comment}``."""
    with get_conn() as c:
        rows = c.execute(
            "SELECT path, comment FROM track_comments WHERE playlist = ?",
            (playlist,),
        ).fetchall()
    return {r["path"]: r["comment"] for r in rows}


def has_any_comment(path: str) -> bool:
    """Whether *path* carries any comment (general or per-playlist) — for a dot
    in the UI without fetching the text."""
    with get_conn() as c:
        row = c.execute(
            "SELECT 1 FROM track_comments WHERE path = ? LIMIT 1",
            (path,),
        ).fetchone()
    return row is not None
