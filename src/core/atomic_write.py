"""Replace a file in one step, so nothing ever reads half of it.

For the files something else depends on being whole: the database a player
reads off a stick, the user's Traktor collection, Traktor's own QML. Written
over in place, an interrupted write — a stick pulled, a full disk, a crash —
leaves a truncated file and nothing to go back to.

The new content goes to a sibling first, is flushed to the disk, and is then
moved over the original. The move is a rename on the same volume, which either
happens or does not: a reader sees the old file or the new one.
"""

from __future__ import annotations

import contextlib
import os
import stat
from collections.abc import Iterator
from pathlib import Path

#: Appended to the file's name for the sibling the content is written to.
PART_SUFFIX = ".part"


@contextlib.contextmanager
def replacing(path: Path, *, keep_mode: bool = False) -> Iterator[Path]:
    """Yield a sibling of *path* to write to; move it over *path* afterwards.

    For writers that take a file name rather than bytes. If the block raises,
    or the move fails, *path* is exactly as it was and the sibling is gone.

    With *keep_mode* the file keeps the permissions it had. Its timestamps
    are never carried over: a cache keyed on the modification time has to
    see that the file changed.

    A file marked read-only is replaced where the platform allows it, which
    Windows does not: there the move is refused, this raises
    :class:`PermissionError`, and the file stays as it is until the user
    clears the flag.
    """
    path = Path(path)
    part = path.with_name(path.name + PART_SUFFIX)
    try:
        # One left by a write that was killed would be written over anyway;
        # one that is read-only could not be, and would fail every write.
        _remove(part)
        yield part
        _flush_to_disk(part)
        if keep_mode and path.exists():
            part.chmod(path.stat().st_mode)
        os.replace(part, path)
    except BaseException:
        # Whatever stopped the write, half a file must not stay behind. The
        # volume may be the thing that failed, so removing it can fail too —
        # the error worth reporting is the first one.
        with contextlib.suppress(OSError):
            _remove(part)
        raise


def _remove(path: Path) -> None:
    """Delete *path* if it is there, read-only or not.

    Windows does not delete a file marked read-only, and the sibling of a
    read-only original is exactly that once it has taken its mode.
    """
    try:
        path.unlink(missing_ok=True)
    except PermissionError:
        path.chmod(stat.S_IREAD | stat.S_IWRITE)
        path.unlink()


def _flush_to_disk(path: Path) -> None:
    # Read-write: Windows refuses to flush a handle opened for reading.
    fd = os.open(path, os.O_RDWR)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_bytes_atomic(path: Path, data: bytes, *, keep_mode: bool = False) -> None:
    """Write *data* to *path*, replacing what is there in one step.

    Raises :class:`OSError` if the write fails, in which case *path* is
    exactly as it was. See :func:`replacing` for *keep_mode*.
    """
    with replacing(path, keep_mode=keep_mode) as part:
        part.write_bytes(data)


def write_text_atomic(
    path: Path, text: str, *, keep_mode: bool = False, encoding: str = "utf-8"
) -> None:
    """:func:`write_bytes_atomic` for text."""
    write_bytes_atomic(path, text.encode(encoding), keep_mode=keep_mode)
