"""The player-settings files Rekordbox leaves in a key's ``PIONEER`` folder.

A key exported by Rekordbox carries four small files beside the database:

    PIONEER/MYSETTING.DAT      player settings (auto cue, jog mode, …)
    PIONEER/MYSETTING2.DAT     more player settings
    PIONEER/DJMMYSETTING.DAT   mixer settings
    PIONEER/DEVSETTING.DAT     device settings

Without them an XDJ-1000 stops at "MY SETTING DATA was not found. Push the
MENU button to LOAD" and goes no further: nothing on the key can be browsed.
The same key with Rekordbox's four files in place loads. So an export has to
leave them there too.

Format
------

Each file is::

    u32   header length (0x60)
    char  brand[32]       NUL-padded
    char  software[32]
    char  version[32]
    u32   payload length
    u8    payload[…]
    u16   CRC-16/XMODEM, little-endian
    u16   0

The checksum covers the payload, except in ``DJMMYSETTING.DAT`` where it
covers everything before it, header included. Both rules were checked
against the four files Rekordbox 7.2.18 wrote to a key on 2026-09-24.

The payloads are the values in those files — Rekordbox's settings at the
time, as the player the key was tested on accepted them. Each byte is one
setting; the meaning of most is not documented here, and none is needed:
the player only has to find a well-formed file.

A key that already has one of these files keeps it. It may be the DJ's own
settings, saved from Rekordbox.
"""

from __future__ import annotations

import logging
import struct
from dataclasses import dataclass
from pathlib import Path

from core.atomic_write import write_bytes_atomic

log = logging.getLogger(__name__)

HEADER_LEN = 0x60
_FIELD = 32


@dataclass(frozen=True)
class SettingsFile:
    name: str
    brand: str
    software: str
    version: str
    payload: bytes
    #: True when the checksum covers the header as well as the payload.
    checksum_covers_header: bool = False


SETTINGS_FILES: tuple[SettingsFile, ...] = (
    SettingsFile(
        "MYSETTING.DAT",
        "PIONEER",
        "rekordbox",
        "0.001",
        bytes.fromhex(
            "78563412 02000000 81838188 81018281 81010101"
            " 82808081 80818000 00810000 81818180 81800000"
        ),
    ),
    SettingsFile(
        "MYSETTING2.DAT",
        "PIONEER",
        "rekordbox",
        "0.001",
        bytes.fromhex(
            "81808383 81808000 00008081 85000000 00000000"
            " 00000000 00000000 00000000 00000000 00000000"
        ),
    ),
    SettingsFile(
        "DJMMYSETTING.DAT",
        "PioneerDJ",
        "rekordbox",
        "1.000",
        bytes.fromhex(
            "78563412 01000000 20000000 81828080 81818081"
            " 80808582 80808284 81810000 00000000 00000000"
            " 00000000 00000000 00000000"
        ),
        checksum_covers_header=True,
    ),
    SettingsFile(
        "DEVSETTING.DAT",
        "PIONEER DJ",
        "rekordbox",
        "7.2.18",
        bytes.fromhex("78563412 01000000 01010301 01010101 01000000 00000000 00000000 00000000"),
    ),
)


def crc16_xmodem(data: bytes) -> int:
    """CRC-16/XMODEM: polynomial 0x1021, initial value 0, no reflection."""
    crc = 0
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else (crc << 1)
            crc &= 0xFFFF
    return crc


def _field(text: str) -> bytes:
    raw = text.encode("ascii")
    if len(raw) >= _FIELD:
        raise ValueError(f"{text!r} does not fit a {_FIELD}-byte field")
    return raw.ljust(_FIELD, b"\0")


def encode(spec: SettingsFile) -> bytes:
    """The file's bytes, checksum included."""
    body = (
        struct.pack("<I", HEADER_LEN)
        + _field(spec.brand)
        + _field(spec.software)
        + _field(spec.version)
        + struct.pack("<I", len(spec.payload))
        + spec.payload
    )
    covered = body if spec.checksum_covers_header else spec.payload
    return body + struct.pack("<HH", crc16_xmodem(covered), 0)


def write_missing(pioneer_dir: Path) -> list[str]:
    """Write each settings file the key lacks; return the names written.

    Each goes to a sibling first and is renamed into place, so a key
    pulled mid-write never holds half a file the player would reject.
    """
    pioneer_dir.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    for spec in SETTINGS_FILES:
        dest = pioneer_dir / spec.name
        if dest.exists():
            continue
        write_bytes_atomic(dest, encode(spec))
        written.append(spec.name)
    if written:
        log.info("wrote player settings: %s", ", ".join(written))
    return written
