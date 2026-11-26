"""macOS leaves sidecar files on a FAT/exFAT stick; a player should not see them.

Copying with metadata onto exFAT makes macOS write an AppleDouble ``._name``
beside every file, to hold the extended attributes the filesystem cannot. On a
real export this produced one ``._`` file per track plus one per directory.
They are not audio and not analysis, and a player that walks the tree can list
them as tracks.
"""

from __future__ import annotations

import inspect
import os
from pathlib import Path

from core import usb_export
from core.usb_export import strip_macos_metadata


def _stick(tmp_path: Path) -> Path:
    root = tmp_path / "usb"
    (root / "Contents").mkdir(parents=True)
    (root / "PIONEER" / "USBANLZ" / "P000" / "00000001").mkdir(parents=True)
    (root / "Contents" / "track.mp3").write_bytes(b"audio")
    (root / "Contents" / "._track.mp3").write_bytes(b"\x00\x05\x16\x07")
    (root / "Contents" / ".DS_Store").write_bytes(b"junk")
    (root / "._Contents").write_bytes(b"\x00\x05\x16\x07")
    (root / "._PIONEER").write_bytes(b"\x00\x05\x16\x07")
    (root / "PIONEER" / "USBANLZ" / "._P000").write_bytes(b"\x00\x05\x16\x07")
    (root / "PIONEER" / "USBANLZ" / "P000" / "00000001" / "ANLZ0000.DAT").write_bytes(b"PMAI")
    return root


def test_removes_appledouble_sidecars(tmp_path) -> None:
    root = _stick(tmp_path)
    removed = strip_macos_metadata(root)
    assert removed == 5
    assert not list(root.rglob("._*"))
    assert not list(root.rglob(".DS_Store"))


def test_keeps_the_real_files(tmp_path) -> None:
    root = _stick(tmp_path)
    strip_macos_metadata(root)
    assert (root / "Contents" / "track.mp3").read_bytes() == b"audio"
    assert (root / "PIONEER" / "USBANLZ" / "P000" / "00000001" / "ANLZ0000.DAT").is_file()


def test_leaves_os_managed_directories_alone(tmp_path) -> None:
    """.Spotlight-V100 and friends belong to the OS, not to us."""
    root = _stick(tmp_path)
    for name in (".Spotlight-V100", ".Trashes", ".fseventsd"):
        (root / name).mkdir()
    strip_macos_metadata(root)
    for name in (".Spotlight-V100", ".Trashes", ".fseventsd"):
        assert (root / name).is_dir(), name


def test_a_track_legitimately_starting_with_underscore_survives(tmp_path) -> None:
    root = _stick(tmp_path)
    keep = root / "Contents" / "_intro.mp3"
    keep.write_bytes(b"audio")
    strip_macos_metadata(root)
    assert keep.is_file()


def test_missing_root_is_not_an_error(tmp_path) -> None:
    assert strip_macos_metadata(tmp_path / "nothing") == 0


def test_a_sidecar_that_vanishes_is_not_a_failure(tmp_path, monkeypatch) -> None:
    """Observed on a real export, on a filename with a combining accent::

        could not remove …/._Maōh - Thoughts Of Frenzy.mp3:
        [Errno 2] No such file or directory

    macOS drops the sidecar itself once the attributes it holds are gone, so
    it can disappear between listing the directory and unlinking it. Already
    gone is the outcome we wanted, not an error to log.
    """
    root = _stick(tmp_path)
    real_unlink = Path.unlink

    def _vanishing(self, *args, **kwargs):
        if self.name == "._track.mp3":
            real_unlink(self)
            raise FileNotFoundError(2, "No such file or directory", str(self))
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", _vanishing)
    caplog_free = strip_macos_metadata(root)
    assert caplog_free == 5, "a file that is already gone still counts"
    assert not list(root.rglob("._*"))


def test_copies_do_not_carry_metadata(tmp_path) -> None:
    """copy2 preserves xattrs, which is what makes macOS write the sidecar."""
    src = inspect.getsource(usb_export.run_export)
    calls = [
        line.strip()
        for line in src.splitlines()
        if "shutil." in line and not line.strip().startswith("#")
    ]
    assert calls, "no copy call found"
    assert all("copy2" not in c for c in calls), (
        f"use copyfile: copy2 carries extended attributes onto the stick — found {calls}"
    )


# ── Writing only what changed ───────────────────────────────────────────────
# Every write to a FAT stick costs a create (and macOS adds a ._ sidecar the
# sweep then has to delete); a re-export of the same playlists changes nothing.


def _aged(path: Path) -> int:
    os.utime(path, ns=(1_000_000_000, 1_000_000_000))
    return path.stat().st_mtime_ns


def test_the_same_bytes_are_not_written_again(tmp_path) -> None:
    target = tmp_path / "a.m3u8"
    assert usb_export.write_if_changed(target, b"one") is True
    before = _aged(target)
    assert usb_export.write_if_changed(target, b"one") is False
    assert target.stat().st_mtime_ns == before


def test_different_bytes_are_written(tmp_path) -> None:
    target = tmp_path / "a.m3u8"
    target.write_bytes(b"one")
    assert usb_export.write_if_changed(target, b"two") is True
    assert target.read_bytes() == b"two"


def test_what_the_caller_ignores_does_not_count_as_a_change(tmp_path) -> None:
    target = tmp_path / "a.nml"
    target.write_bytes(b"<P UUID='old'/>")
    before = _aged(target)
    same = usb_export.write_if_changed(
        target, b"<P UUID='new'/>", ignore=lambda b: b.replace(b"new", b"old")
    )
    assert same is False and target.stat().st_mtime_ns == before


def test_sidecars_deep_in_the_tree_are_found(tmp_path) -> None:
    root = _stick(tmp_path)
    deep = root / "PIONEER" / "USBANLZ" / "P001" / "0000ABCD"
    deep.mkdir(parents=True)
    (deep / "._ANLZ0000.DAT").write_bytes(b"x")
    (deep / "ANLZ0000.DAT").write_bytes(b"x")
    strip_macos_metadata(root)
    assert sorted(p.name for p in deep.iterdir()) == ["ANLZ0000.DAT"]


def test_os_managed_directories_are_skipped_at_any_depth(tmp_path) -> None:
    root = _stick(tmp_path)
    inner = root / "Contents" / ".Trashes"
    inner.mkdir(parents=True)
    (inner / "._kept").write_bytes(b"x")
    strip_macos_metadata(root)
    assert (inner / "._kept").is_file()
