"""Replacing a file in one step.

The files this protects are the ones something else depends on being whole:
the database a player reads off a stick, and the user's own Traktor
collection. Writing over them in place means an interrupted write leaves half
a file and nothing to go back to.
"""

from __future__ import annotations

import os
import stat
import sys

import pytest

from core import atomic_write
from core.atomic_write import write_bytes_atomic, write_text_atomic


def test_the_new_content_replaces_the_old(tmp_path) -> None:
    target = tmp_path / "export.pdb"
    target.write_bytes(b"old")

    write_bytes_atomic(target, b"new content")

    assert target.read_bytes() == b"new content"


def test_a_file_that_was_not_there_is_created(tmp_path) -> None:
    target = tmp_path / "export.pdb"

    write_bytes_atomic(target, b"first")

    assert target.read_bytes() == b"first"


def test_nothing_is_left_beside_the_file(tmp_path) -> None:
    target = tmp_path / "export.pdb"

    write_bytes_atomic(target, b"data")

    assert [p.name for p in tmp_path.iterdir()] == ["export.pdb"]


def test_a_failed_write_leaves_the_original_whole(tmp_path, monkeypatch) -> None:
    """The disk filling up half-way is the case this exists for."""
    target = tmp_path / "export.pdb"
    target.write_bytes(b"the library a player can still read")

    def full(_fd: int) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(atomic_write.os, "fsync", full)
    with pytest.raises(OSError, match="No space left"):
        write_bytes_atomic(target, b"half a datab")

    assert target.read_bytes() == b"the library a player can still read"
    assert [p.name for p in tmp_path.iterdir()] == ["export.pdb"], (
        "the partial file must not stay on the stick"
    )


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="permission bits are POSIX: Windows keeps one read-only flag per "
    "file, and chmod there can set nothing else",
)
def test_the_mode_can_be_carried_over(tmp_path) -> None:
    target = tmp_path / "D2.qml"
    target.write_text("old")
    os.chmod(target, 0o640)

    write_text_atomic(target, "new", keep_mode=True)

    assert target.read_text() == "new"
    assert stat.S_IMODE(target.stat().st_mode) == 0o640


# ── A file marked read-only ─────────────────────────────────────────────────
# The one permission Windows does have, and it works the other way round
# there: the flag protects the file itself, so it can be neither replaced nor
# deleted while it is set. With the mode carried over, the sibling was made
# read-only before the move, the move was refused, and the sibling could not
# be removed either — it stayed, and every later write failed on it, even
# once the user had made the file writable again.


def _cannot_be_replaced() -> None:
    """Whether a read-only file can be replaced is the platform's call; what
    follows a refusal is ours. Stands in for the move where it is allowed."""
    raise PermissionError(13, "Access is denied")


def test_a_refused_move_leaves_nothing_beside_a_read_only_file(tmp_path, monkeypatch) -> None:
    target = tmp_path / "collection.nml"
    target.write_bytes(b"the library")
    os.chmod(target, stat.S_IREAD)
    monkeypatch.setattr(atomic_write.os, "replace", lambda _src, _dst: _cannot_be_replaced())

    with pytest.raises(PermissionError):
        write_bytes_atomic(target, b"new", keep_mode=True)

    assert target.read_bytes() == b"the library"
    assert [p.name for p in tmp_path.iterdir()] == ["collection.nml"]


def test_a_write_works_again_once_the_file_is_writable(tmp_path, monkeypatch) -> None:
    target = tmp_path / "collection.nml"
    target.write_bytes(b"the library")
    os.chmod(target, stat.S_IREAD)
    with monkeypatch.context() as refused:
        refused.setattr(atomic_write.os, "replace", lambda _src, _dst: _cannot_be_replaced())
        with pytest.raises(PermissionError):
            write_bytes_atomic(target, b"new", keep_mode=True)

    os.chmod(target, stat.S_IREAD | stat.S_IWRITE)
    write_bytes_atomic(target, b"new", keep_mode=True)

    assert target.read_bytes() == b"new"
    assert [p.name for p in tmp_path.iterdir()] == ["collection.nml"]


def test_a_sibling_left_by_a_crash_is_written_over(tmp_path) -> None:
    """Even a read-only one, which is what the earlier failure left."""
    target = tmp_path / "collection.nml"
    target.write_bytes(b"the library")
    stale = tmp_path / ("collection.nml" + atomic_write.PART_SUFFIX)
    stale.write_bytes(b"half a libr")
    os.chmod(stale, stat.S_IREAD)

    write_bytes_atomic(target, b"new")

    assert target.read_bytes() == b"new"
    assert [p.name for p in tmp_path.iterdir()] == ["collection.nml"]


def test_text_is_written_as_utf8(tmp_path) -> None:
    target = tmp_path / "collection.nml"

    write_text_atomic(target, "Motörhead – Ace of Spades")

    assert target.read_bytes() == "Motörhead – Ace of Spades".encode()
