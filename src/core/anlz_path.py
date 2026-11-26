"""Where a player looks for a track's analysis files.

An XDJ-1000 ignores the ``analyze_path`` column of ``export.pdb``. It works
the ``PIONEER/USBANLZ/Pxxx/xxxxxxxx/`` folder out from the track's own path
on the stick, and a track whose analysis sits anywhere else plays with no
waveform, no beat grid and no hot cues. Measured on 2026-09-24: our export
put a track's files in ``P000/00000001``; the XDJ showed none of it and wrote
its own empty ``ANLZ0000.DAT`` into ``P03B/0002E2B7`` — the folder Rekordbox
had used for the same path.

The function is a multiply-add hash over the path's UTF-16 code units,
reduced modulo 200003 — the size of the table the player writes into
``USBANLZ/USBMNG.DAT``. The ``P`` folder is seven bits picked out of the
result. Recovered from the Rekordbox binary by morizkraemer/fourfour, and
ported from the MIT-licensed baken project
(``crates/baken-export/src/anlz/hash.rs``), which checked it on 586 of 587
tracks of a real export.

Two paths can land in the same folder. The player cannot tell them apart,
so the export renames a new file whose path would collide — see
:func:`core.usb_export.plan_export`.
"""

from __future__ import annotations

#: The table size in the player's ``USBMNG.DAT``, and the hash's modulus.
TABLE_SIZE = 200003

ANLZ_ROOT = "PIONEER/USBANLZ"


def anlz_hash(device_path: str) -> tuple[int, int]:
    """``(P, bucket)`` for a stick path such as ``/Contents/a/b/x.mp3``."""
    raw = device_path.encode("utf-16-le")
    h = 0
    for i in range(0, len(raw), 2):
        unit = raw[i] | (raw[i + 1] << 8)
        h = (h * 0x5BC9 + unit) & 0xFFFFFFFF
        h = (h * 0x93B5 + unit) & 0xFFFFFFFF
    r = h % TABLE_SIZE
    p = (
        (r & 1)
        | ((r >> 1) & 2)
        | ((r >> 4) & 4)
        | ((r >> 4) & 8)
        | ((r >> 5) & 0x10)
        | ((r >> 8) & 0x20)
        | ((r >> 10) & 0x40)
    )
    return p, r


def anlz_dir(device_path: str) -> str:
    """Stick-relative folder, no leading slash, for *device_path*'s analysis."""
    p, r = anlz_hash(device_path)
    return f"{ANLZ_ROOT}/P{p:03X}/{r:08X}"
