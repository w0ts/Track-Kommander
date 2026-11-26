"""The library, read once and kept as columns.

Ranking used to start by reading every track out of the database, on every
re-rank: a slider moved, and 20,000 rows of fifty columns were fetched,
decoded and thrown away again. On a library that size the fetch alone was
80 ms of a 120 ms re-rank, all of it on the GUI thread.

A :class:`Snapshot` is that read, done once and kept until the database
changes. It holds the rows as they came, and gives out columns of them as
arrays — built the first time each is asked for, since a given re-rank only
looks at the few its axes use.

**Knowing when it is stale.** The database file, and its write-ahead log,
are looked at before every use: a commit changes the size or the
modification time of one of them. That is two ``stat`` calls, and it sees a
write from anywhere — this process, another, a test.
"""

from __future__ import annotations

import contextlib
import logging
import os
import sqlite3
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

import numpy as np

from core import track_meta
from core.embeddings import EMBEDDING_MODELS, EmbeddingModel, load_vectors
from core.features import FeatureSpec
from core.genre_match import style_vector

log = logging.getLogger(__name__)

#: Columns holding a whole embedding per track (CLAP's vector is ~2 KB).
#: Left out of the rows: the results table never shows them, and the vectors
#: are read separately, once, by :meth:`Snapshot.stacked`.
_EMBEDDING_COLUMNS: frozenset[str] = frozenset(
    m.column for m in EMBEDDING_MODELS.values() if m.storage == "column" and m.column
)

#: How many databases' snapshots are kept. The application has one; the
#: second is room for a test, or a library being switched.
_KEPT = 2
_T = TypeVar("_T")

_Signature = tuple[tuple[int, int], ...]


class Snapshot:
    """Every track in the library, as rows and as columns.

    Treat it as read-only. The arrays it hands out are shared between
    re-ranks; index them, never write to them.
    """

    def __init__(self, rows: list[sqlite3.Row], get_conn: Callable[[], sqlite3.Connection]) -> None:
        self.rows = rows
        self.columns: frozenset[str] = frozenset(rows[0].keys()) if rows else frozenset()
        self.paths: list[str | None] = [r["path"] for r in rows]
        self.index: dict[str, int] = {p: i for i, p in enumerate(self.paths) if p is not None}
        self._get_conn = get_conn
        self._lock = threading.RLock()
        self._cache: dict[tuple, Any] = {}

    def __len__(self) -> int:
        return len(self.rows)

    def _once(self, key: tuple, build: Callable[[], _T]) -> _T:
        with self._lock:
            if key not in self._cache:
                self._cache[key] = build()
            return self._cache[key]

    # ── columns ─────────────────────────────────────────────────────────────

    def raw(self, column: str) -> list:
        """The column as stored, ``None`` where it is null or not there."""

        def build() -> list:
            if column in DERIVED:
                rule = DERIVED[column]
                return [rule(r) for r in self.rows]
            if column not in self.columns:
                return [None] * len(self.rows)
            return [r[column] for r in self.rows]

        return self._once(("raw", column), build)

    def numbers(self, column: str) -> tuple[np.ndarray, np.ndarray]:
        """``(values, has)``: the column as floats, and where it holds one.

        A null, or something that is not a number, has no value; *values*
        is 0 there, so look at *has* before trusting it.
        """

        def build() -> tuple[np.ndarray, np.ndarray]:
            values = np.zeros(len(self.rows), dtype=np.float64)
            has = np.zeros(len(self.rows), dtype=bool)
            for i, v in enumerate(self.raw(column)):
                if v is None:
                    continue
                try:
                    values[i] = float(v)
                except (TypeError, ValueError):
                    continue
                has[i] = True
            return values, has

        return self._once(("numbers", column), build)

    def normalised(self, spec: FeatureSpec) -> tuple[np.ndarray, np.ndarray]:
        """``(values, has)``: a scalar feature mapped into [0, 1] by its own
        normaliser — what :func:`core.match_scores._norm_value` gives, for
        every track."""

        def build() -> tuple[np.ndarray, np.ndarray]:
            values = np.zeros(len(self.rows), dtype=np.float64)
            has = np.zeros(len(self.rows), dtype=bool)
            if spec.column is None or spec.normalize is None:
                return values, has
            for i, v in enumerate(self.raw(spec.column)):
                if v is None:
                    continue
                try:
                    values[i] = spec.normalize(float(v))
                except Exception:  # a normaliser may refuse a value: no score
                    continue
                has[i] = True
            return values, has

        return self._once(("normalised", spec.key), build)

    def texts(self, column: str) -> list[str]:
        """The column as text, empty where it is null or not text."""

        def build() -> list[str]:
            return [v if isinstance(v, str) else "" for v in self.raw(column)]

        return self._once(("texts", column), build)

    def coded(self, column: str) -> tuple[np.ndarray, list]:
        """``(codes, distinct)``: each track's value as an index into the
        distinct values the column holds.

        For the columns with few distinct values — a key is one of two
        dozen codes — where a rule can be worked out once per value and
        looked up for every track.
        """

        def build() -> tuple[np.ndarray, list]:
            seen: dict = {}
            codes = np.empty(len(self.rows), dtype=np.int64)
            for i, v in enumerate(self.raw(column)):
                try:
                    codes[i] = seen.setdefault(v, len(seen))
                except TypeError:  # not hashable: treat as no value
                    codes[i] = seen.setdefault(None, len(seen))
            return codes, list(seen)

        return self._once(("coded", column), build)

    def path_order(self) -> np.ndarray:
        """Each track's place when the library is sorted by path — the
        tiebreak between tracks that score the same."""

        def build() -> np.ndarray:
            order = sorted(range(len(self.paths)), key=lambda i: self.paths[i] or "")
            place = np.empty(len(self.paths), dtype=np.int64)
            place[order] = np.arange(len(order))
            return place

        return self._once(("path_order",), build)

    # ── style: the detected genres, as one matrix ───────────────────────────

    def styles(self) -> tuple[np.ndarray, np.ndarray, dict[str, int]]:
        """``(matrix, norms, labels)``: every track's detected-genre vector
        as a row, one column per label."""

        def build() -> tuple[np.ndarray, np.ndarray, dict[str, int]]:
            vectors = [style_vector(t) for t in self.texts("genres")]
            labels: dict[str, int] = {}
            for vector in vectors:
                for name, _prob in vector:
                    labels.setdefault(name, len(labels))
            matrix = np.zeros((len(vectors), len(labels)), dtype=np.float64)
            for i, vector in enumerate(vectors):
                for name, prob in vector:
                    matrix[i, labels[name]] = prob
            norms = np.sqrt((matrix * matrix).sum(axis=1))
            return matrix, norms, labels

        return self._once(("styles",), build)

    # ── embeddings ──────────────────────────────────────────────────────────

    def _vectors(self, model: EmbeddingModel) -> dict[str, np.ndarray]:
        def build() -> dict[str, np.ndarray]:
            if model.storage == "column" and model.column:
                if model.column not in self._all_columns():
                    return {}
                with contextlib.closing(self._get_conn()) as c:
                    rows = c.execute(f'SELECT path, "{model.column}" FROM tracks').fetchall()
                return load_vectors(model, rows)
            return load_vectors(model, self.rows)

        return self._once(("vectors", model.key), build)

    def _all_columns(self) -> frozenset[str]:
        def build() -> frozenset[str]:
            with contextlib.closing(self._get_conn()) as c:
                return frozenset(r[1] for r in c.execute("PRAGMA table_info(tracks)"))

        return self._once(("all_columns",), build)

    def stacked(
        self, model: EmbeddingModel, shape: tuple[int, ...]
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """``(where, matrix, norms)`` for the tracks whose vector has
        *shape*: their places in the library, their vectors as rows, and
        each row's length."""

        def build() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
            vectors = self._vectors(model)
            where = [
                (i, p)
                for i, p in enumerate(self.paths)
                if p is not None and p in vectors and vectors[p].shape == shape
            ]
            if not where:
                empty = np.zeros((0, *shape), dtype=np.float32)
                return (np.zeros(0, dtype=np.int64), empty, np.zeros(0, dtype=np.float32))
            matrix = np.stack([vectors[p] for _i, p in where]).astype(np.float32)
            return (
                np.asarray([i for i, _p in where], dtype=np.int64),
                matrix,
                np.linalg.norm(matrix, axis=1),
            )

        return self._once(("stacked", model.key, shape), build)


# ── keeping one per database ────────────────────────────────────────────────

_kept: dict[str, tuple[_Signature, Snapshot]] = {}
_kept_lock = threading.Lock()


def _stat(path: str) -> tuple[int, int]:
    try:
        st = os.stat(path)
    except OSError:
        return (0, 0)
    return (st.st_mtime_ns, st.st_size)


def _signature(database: str) -> _Signature:
    """What changes when the database is written to."""
    return (_stat(database), _stat(database + "-wal"))


def _select(c: sqlite3.Connection) -> str:
    names = [r[1] for r in c.execute("PRAGMA table_info(tracks)") if r[1] not in _EMBEDDING_COLUMNS]
    return ", ".join('"' + n.replace('"', '""') + '"' for n in names)


#: The accepted Discogs match's fields, joined onto each track's row for the
#: genre, label and year rules (core.track_meta). Null without a match.
_DISCOGS_FIELDS = {
    "discogs_genres": "$.genres",
    "discogs_styles": "$.styles",
    "discogs_label": "$.label",
    "discogs_year": "$.year",
    "discogs_original_year": "$.original_year",
}


def _query(c: sqlite3.Connection) -> str:
    """The snapshot's SELECT: every track column, and its Discogs match's
    fields when the database has the table."""
    tables = {
        r[0]
        for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name IN ('track_discogs', 'track_spotify')"
        )
    }
    if "track_discogs" not in tables:
        return f"SELECT {_select(c)} FROM tracks"
    names = [r[1] for r in c.execute("PRAGMA table_info(tracks)") if r[1] not in _EMBEDDING_COLUMNS]
    cols = ", ".join('t."' + n.replace('"', '""') + '"' for n in names)
    extra = ", ".join(
        f"json_extract(d.record, '{path}') AS {name}" for name, path in _DISCOGS_FIELDS.items()
    )
    spotify = (
        (
            ", s.release_date AS spotify_release_date",
            " LEFT JOIN track_spotify AS s ON s.path = t.path",
        )
        if "track_spotify" in tables
        else ("", "")
    )
    return (
        f"SELECT {cols}, {extra}{spotify[0]} FROM tracks AS t "
        "LEFT JOIN track_discogs AS d ON d.path = t.path AND d.status = 'matched'"
        f"{spotify[1]}"
    )


def _cell(row, column: str):
    try:
        return row[column]
    except (IndexError, KeyError):
        return None


#: Columns worked out per row from others (core.track_meta), asked for by name
#: like any stored column.
DERIVED: dict[str, Callable] = {
    "genre_effective": lambda r: track_meta.effective_genre(
        _cell(r, "genre"), _cell(r, "discogs_genres"), _cell(r, "discogs_styles")
    ),
    "label_effective": lambda r: track_meta.effective_label(
        _cell(r, "label"), _cell(r, "discogs_label")
    ),
    "year_effective": lambda r: track_meta.effective_year(
        _cell(r, "release_date"),
        _cell(r, "discogs_original_year"),
        _cell(r, "discogs_year"),
        _cell(r, "spotify_release_date"),
    ),
    "import_day": lambda r: track_meta.import_day(_cell(r, "import_date")),
}


def snapshot(get_conn: Callable[[], sqlite3.Connection]) -> Snapshot:
    """The library behind *get_conn*, as it is now.

    The same object as last time if the database has not been written to
    since; a new one, read afresh, if it has.
    """
    with contextlib.closing(get_conn()) as c:
        database = (
            next((row[2] for row in c.execute("PRAGMA database_list") if row[1] == "main"), "")
            or ""
        )
        if database:
            database = str(Path(database))
        signature = _signature(database) if database else ()
        with _kept_lock:
            held = _kept.get(database) if database else None
        if held is not None and held[0] == signature:
            return held[1]
        # Read before it is stored under the signature taken above: a write
        # that lands in between makes the next look see a change, and read
        # again. The other order could keep a stale library as current.
        rows = c.execute(_query(c)).fetchall()
    taken = Snapshot(rows, get_conn)
    if database:
        with _kept_lock:
            _kept.pop(database, None)
            _kept[database] = (signature, taken)
            while len(_kept) > _KEPT:
                _kept.pop(next(iter(_kept)))
    log.debug(
        "library snapshot: %d track(s) read from %s",
        len(rows),
        database or "a database with no file",
    )
    return taken


def forget() -> None:
    """Drop every kept snapshot. For tests, and for a library that was
    replaced rather than written to."""
    with _kept_lock:
        _kept.clear()


__all__ = ["Snapshot", "forget", "snapshot"]
