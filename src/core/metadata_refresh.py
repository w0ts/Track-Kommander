"""What a scan changes in the library's metadata, from Traktor's collection.

Tracks that are already analysed need no audio work, only their tags brought
up to date. This works out which rows to write and nothing else, so it can be
tested without a worker thread or a database.

**The collection can only change a track it lists.** One it does not list
keeps what the library holds. Without that rule a collection that failed to
load — read while Traktor was rewriting it, or on a volume that is not
mounted — reads as every track having no artist, title, genre or rating, and
the refresh writes those blanks over the whole library.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass

from core.analysis import collection_lists, read_metadata, traktor_key_to_fields

#: What the library holds for a track it has no metadata for yet, in the
#: order :func:`core.db.get_metadata_batch` returns it.
_NOTHING_HELD = ("", "", "", "", 0, "", "", "", None, None)


@dataclass(frozen=True)
class MetadataRefresh:
    """Rows to write, each ending in the path it is for."""

    metadata: tuple[tuple, ...] = ()
    """For :func:`core.db.batch_update_metadata`."""

    keys: tuple[tuple, ...] = ()
    """For :func:`core.db.batch_update_key`."""

    tempos: tuple[tuple[float, str], ...] = ()
    """For :func:`core.db.batch_update_bpm`."""

    unlisted: int = 0
    """Tracks the collection does not list, which were left as they are."""


def plan_refresh(
    paths: Iterable[str],
    held: Mapping[str, tuple],
    *,
    should_stop: Callable[[], bool] | None = None,
    tick: Callable[[int], None] | None = None,
) -> MetadataRefresh:
    """The rows that bring *paths* up to date with the collection.

    *held* is what the library has now, by path. *tick* hears the index of
    each track as it is reached; *should_stop* ends the pass early, keeping
    what was worked out so far.
    """
    metadata: list[tuple] = []
    keys: list[tuple] = []
    tempos: list[tuple[float, str]] = []
    unlisted = 0
    for index, path in enumerate(paths):
        if should_stop is not None and should_stop():
            break
        if tick is not None:
            tick(index)
        if not collection_lists(path):
            unlisted += 1
            continue
        (artist, album, title, genre, musical_key, traktor_bpm, rating, extra) = read_metadata(path)
        now = (
            artist,
            album,
            title,
            genre,
            rating,
            extra.get("release_date", ""),
            extra.get("import_date", ""),
            extra.get("label", ""),
            extra.get("bitrate"),
            extra.get("play_count"),
        )
        if now != held.get(path, _NOTHING_HELD):
            metadata.append((*now, path))
        key_fields = traktor_key_to_fields(musical_key)
        if key_fields is not None:
            keys.append((*key_fields, path))
        if traktor_bpm > 0:
            tempos.append((traktor_bpm, path))
    return MetadataRefresh(
        metadata=tuple(metadata), keys=tuple(keys), tempos=tuple(tempos), unlisted=unlisted
    )
