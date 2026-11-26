"""Which controllers are configured in Traktor's Controller Manager.

The bridge only works when a **Kontrol D2** device exists there: that is what
makes Traktor load the D2 QML our module is patched into. Without it the app
simply never connects, with nothing on screen to explain why — so it is worth
being able to say plainly whether the device is present.

Where it lives
--------------

``Traktor Settings.tsi`` is NIXML, but the controller list is a base64 blob in
``DeviceIO.Config.Controller``. Decoded, it is a chunked binary container —
four-character id, big-endian u32 length, then that many bytes::

    DIOM
      DEVS
        DEVI   <u32 character count><UTF-16BE name>…
        DEVI   …

Reading only
------------

Adding a device would mean writing that undocumented binary schema, and
Traktor rewrites the whole file when it quits, so anything written behind its
back is liable to be discarded. The app therefore reports and instructs
rather than editing.

Three-valued on purpose: ``None`` means "could not read it", which is not the
same as "not configured". One is a reason to stay quiet, the other a reason
to tell the user what to do.
"""

from __future__ import annotations

import base64
import binascii
import logging
import re
import struct
from pathlib import Path

log = logging.getLogger(__name__)

#: What Traktor calls the device in its own configuration.
D2_DEVICE = "Traktor.Kontrol D2"

_ENTRY = re.compile(r'<Entry\s+Name="DeviceIO\.Config\.Controller"[^>]*?Value="([^"]*)"')

#: Guards against a malformed length sending us to allocate nonsense.
_MAX_NAME_CHARS = 512


def settings_tsi(collection_nml: Path | None) -> Path | None:
    """Traktor's settings file, which sits beside the collection."""
    if collection_nml is None:
        return None
    candidate = Path(collection_nml).parent / "Traktor Settings.tsi"
    return candidate if candidate.is_file() else None


def configured_devices(tsi_path: Path) -> list[str] | None:
    """Every device in the Controller Manager, in order, or None.

    ``None`` means the file was absent, had no controller entry, or could not
    be decoded — never "there are none", which is an empty list.
    """
    try:
        raw = Path(tsi_path).read_text(errors="replace")
    except OSError:
        return None

    match = _ENTRY.search(raw)
    if match is None:
        return None
    try:
        blob = base64.b64decode(match.group(1), validate=True)
    except (ValueError, binascii.Error):
        log.debug("controller config is not valid base64", exc_info=True)
        return None
    if not blob.startswith(b"DIOM"):
        return None

    names: list[str] = []
    pos = 0
    while True:
        at = blob.find(b"DEVI", pos)
        if at < 0 or at + 12 > len(blob):
            break
        (size,) = struct.unpack_from(">I", blob, at + 4)
        body = blob[at + 8 : at + 8 + size]
        if len(body) < 4:
            break
        (n_chars,) = struct.unpack_from(">I", body, 0)
        if not 0 < n_chars <= _MAX_NAME_CHARS or 4 + n_chars * 2 > len(body):
            break
        names.append(body[4 : 4 + n_chars * 2].decode("utf-16-be", "replace"))
        pos = at + 8 + size
    return names


def _normalise(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def has_d2(tsi_path: Path) -> bool | None:
    """Whether a Kontrol D2 is configured. None if it could not be read.

    Matched on the normalised name so ``Traktor.Kontrol D2`` and
    ``Traktor Kontrol D2`` both count, while ``Kontrol D4`` does not.
    """
    devices = configured_devices(tsi_path)
    if devices is None:
        return None
    wanted = _normalise(D2_DEVICE)
    return any(_normalise(d).startswith(wanted) for d in devices)
