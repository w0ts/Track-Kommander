"""The player-settings files an export leaves in ``PIONEER/``.

An XDJ-1000 given a key without them stops at "MY SETTING DATA was not
found" and lets nothing be browsed; the same key with Rekordbox's four files
loads. These pin the format those files were checked against, and that an
export leaves them — without trampling a DJ's own.
"""

from __future__ import annotations

import struct

from core.my_settings import (
    HEADER_LEN,
    SETTINGS_FILES,
    crc16_xmodem,
    encode,
    write_missing,
)


def test_the_checksum_is_xmodem() -> None:
    """The standard check value for CRC-16/XMODEM."""
    assert crc16_xmodem(b"123456789") == 0x31C3


def test_all_four_files_are_written() -> None:
    names = {s.name for s in SETTINGS_FILES}
    assert names == {"MYSETTING.DAT", "MYSETTING2.DAT", "DJMMYSETTING.DAT", "DEVSETTING.DAT"}


def test_each_file_has_the_layout_rekordbox_writes() -> None:
    """Sizes as Rekordbox 7.2.18 wrote them: 148, 148, 160, 140 bytes."""
    sizes = {s.name: len(encode(s)) for s in SETTINGS_FILES}
    assert sizes == {
        "MYSETTING.DAT": 148,
        "MYSETTING2.DAT": 148,
        "DJMMYSETTING.DAT": 160,
        "DEVSETTING.DAT": 140,
    }
    for spec in SETTINGS_FILES:
        raw = encode(spec)
        assert struct.unpack_from("<I", raw, 0)[0] == HEADER_LEN
        assert raw[4:36].rstrip(b"\0").decode() == spec.brand
        assert raw[36:68].rstrip(b"\0").decode() == "rekordbox"
        assert struct.unpack_from("<I", raw, HEADER_LEN + 4)[0] == len(spec.payload)
        assert raw[-2:] == b"\0\0"


def test_each_checksum_holds() -> None:
    """A player that checks the sum rejects a file whose sum is off, which
    is the "not found" we set out to fix."""
    for spec in SETTINGS_FILES:
        raw = encode(spec)
        stored = struct.unpack_from("<H", raw, len(raw) - 4)[0]
        covered = raw[:-4] if spec.checksum_covers_header else spec.payload
        assert stored == crc16_xmodem(covered), spec.name


def test_the_checksums_match_rekordboxs() -> None:
    """The sums in the files Rekordbox wrote to the key that loaded."""
    sums = {
        s.name: struct.unpack_from("<H", encode(s), len(encode(s)) - 4)[0] for s in SETTINGS_FILES
    }
    assert sums == {
        "MYSETTING.DAT": 0x7A48,
        "MYSETTING2.DAT": 0x9C7A,
        "DJMMYSETTING.DAT": 0x07B0,
        "DEVSETTING.DAT": 0x4351,
    }


def test_a_bare_key_gets_all_four(tmp_path) -> None:
    written = write_missing(tmp_path / "PIONEER")
    assert sorted(written) == sorted(s.name for s in SETTINGS_FILES)
    for spec in SETTINGS_FILES:
        assert (tmp_path / "PIONEER" / spec.name).read_bytes() == encode(spec)
    assert not list((tmp_path / "PIONEER").glob("*.part"))


def test_a_djs_own_settings_are_kept(tmp_path) -> None:
    pioneer = tmp_path / "PIONEER"
    pioneer.mkdir()
    own = b"saved from rekordbox"
    (pioneer / "MYSETTING.DAT").write_bytes(own)
    written = write_missing(pioneer)
    assert "MYSETTING.DAT" not in written
    assert (pioneer / "MYSETTING.DAT").read_bytes() == own
    assert len(written) == 3


def test_a_second_export_writes_nothing(tmp_path) -> None:
    write_missing(tmp_path / "PIONEER")
    assert write_missing(tmp_path / "PIONEER") == []
