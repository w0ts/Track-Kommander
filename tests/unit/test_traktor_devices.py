"""Reading the controllers configured in Traktor's Controller Manager.

The bridge only works if a **Kontrol D2** device exists in Traktor's
Controller Manager — that is what makes Traktor load the D2 QML our module is
patched into. Getting that wrong shows up as "connected: no" with nothing to
explain it, so the app should be able to say plainly whether the device is
there.

Where it lives: `Traktor Settings.tsi` is NIXML, but the controller list is a
base64 `DIOM` binary blob inside `DeviceIO.Config.Controller`. That is a
chunked container — four-character id, big-endian u32 length, body — holding
`DEVS`, which holds one `DEVI` per device, each starting with a UTF-16BE name.

Reading only. Writing a new device into that blob would mean inventing an
undocumented binary schema, and Traktor rewrites the file when it quits, so
the app reports and instructs rather than editing.
"""

from __future__ import annotations

import base64
import struct
from pathlib import Path

import pytest

from core.traktor_devices import (
    D2_DEVICE,
    configured_devices,
    has_d2,
    settings_tsi,
)


def _devi(name: str, payload: bytes = b"\x00" * 8) -> bytes:
    encoded = name.encode("utf-16-be")
    body = struct.pack(">I", len(name)) + encoded + payload
    return b"DEVI" + struct.pack(">I", len(body)) + body


def _tsi(names: list[str]) -> str:
    devs_body = b"".join(_devi(n) for n in names)
    devs = b"DEVS" + struct.pack(">I", len(devs_body)) + devs_body
    diom = b"DIOM" + struct.pack(">I", len(devs)) + devs
    value = base64.b64encode(diom).decode()
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="no" ?>\n'
        "<NIXML><TraktorSettings>"
        '<Entry Name="Audio.DeviceName" Type="3" Value="x"/>'
        f'<Entry Name="DeviceIO.Config.Controller" Type="3" '
        f'Value="{value}"/>'
        "</TraktorSettings></NIXML>"
    )


def _write(tmp_path, names):
    path = tmp_path / "Traktor Settings.tsi"
    path.write_text(_tsi(names))
    return path


# ── Reading the list ────────────────────────────────────────────────────────


def test_lists_every_configured_device(tmp_path) -> None:
    path = _write(tmp_path, ["Generic MIDI", "Traktor.Kontrol D2"])
    assert configured_devices(path) == ["Generic MIDI", "Traktor.Kontrol D2"]


def test_keeps_duplicates(tmp_path) -> None:
    """A real collection had three separate Generic MIDI entries."""
    path = _write(tmp_path, ["Generic MIDI"] * 3)
    assert len(configured_devices(path)) == 3


def test_no_devices_configured(tmp_path) -> None:
    assert configured_devices(_write(tmp_path, [])) == []


# ── The question the UI asks ────────────────────────────────────────────────


def test_finds_the_d2(tmp_path) -> None:
    path = _write(tmp_path, ["Generic MIDI", D2_DEVICE])
    assert has_d2(path) is True


def test_reports_a_missing_d2(tmp_path) -> None:
    path = _write(tmp_path, ["Generic MIDI", "Traktor.Kontrol S4MK2.Default"])
    assert has_d2(path) is False


def test_the_match_is_not_fooled_by_another_kontrol(tmp_path) -> None:
    path = _write(tmp_path, ["Traktor.Kontrol S2MK3", "Traktor.Kontrol D4"])
    assert has_d2(path) is False


def test_the_match_ignores_case_and_spacing(tmp_path) -> None:
    for name in ("Traktor.Kontrol D2", "TRAKTOR.KONTROL D2", "Traktor Kontrol D2"):
        assert has_d2(_write(tmp_path, [name])) is True, name


# ── Nothing readable ────────────────────────────────────────────────────────


def test_a_missing_file_reads_as_unknown(tmp_path) -> None:
    assert configured_devices(tmp_path / "nope.tsi") is None
    assert has_d2(tmp_path / "nope.tsi") is None


def test_a_file_without_the_entry_reads_as_unknown(tmp_path) -> None:
    path = tmp_path / "Traktor Settings.tsi"
    path.write_text("<NIXML><TraktorSettings/></NIXML>")
    assert configured_devices(path) is None


def test_corrupt_base64_reads_as_unknown(tmp_path) -> None:
    path = tmp_path / "Traktor Settings.tsi"
    path.write_text(
        '<NIXML><Entry Name="DeviceIO.Config.Controller" '
        'Type="3" Value="!!!not base64!!!"/></NIXML>'
    )
    assert configured_devices(path) is None


def test_a_truncated_blob_does_not_raise(tmp_path) -> None:
    """Half a device entry must not take the settings screen down with it."""
    devs_body = _devi("Traktor.Kontrol D2")[:-4]
    devs = b"DEVS" + struct.pack(">I", len(devs_body)) + devs_body
    diom = b"DIOM" + struct.pack(">I", len(devs)) + devs
    path = tmp_path / "Traktor Settings.tsi"
    path.write_text(
        '<NIXML><Entry Name="DeviceIO.Config.Controller" '
        f'Type="3" Value="{base64.b64encode(diom).decode()}"/>'
        "</NIXML>"
    )
    got = configured_devices(path)
    assert got is None or isinstance(got, list)


def test_unknown_is_distinct_from_absent(tmp_path) -> None:
    """ "We could not read it" and "it is not configured" must not look the
    same — one is a reason to say nothing, the other a reason to instruct."""
    assert has_d2(tmp_path / "nope.tsi") is None
    assert has_d2(_write(tmp_path, ["Generic MIDI"])) is False


# ── Locating the file ───────────────────────────────────────────────────────


def test_settings_tsi_sits_beside_the_collection(tmp_path) -> None:
    folder = tmp_path / "Traktor 4.5.1"
    folder.mkdir()
    nml = folder / "collection.nml"
    nml.write_text("<NML/>")
    tsi = folder / "Traktor Settings.tsi"
    tsi.write_text("x")
    assert settings_tsi(nml) == tsi


def test_settings_tsi_returns_none_when_absent(tmp_path) -> None:
    nml = tmp_path / "collection.nml"
    nml.write_text("<NML/>")
    assert settings_tsi(nml) is None


def test_settings_tsi_tolerates_no_collection() -> None:
    assert settings_tsi(None) is None


# ── Against the real file, when there is one ────────────────────────────────


def test_the_real_settings_parse_if_present() -> None:
    real = Path.home() / ("Documents/Native Instruments/Traktor 4.5.1/Traktor Settings.tsi")
    if not real.is_file():
        pytest.skip("no Traktor installation on this machine")
    devices = configured_devices(real)
    assert devices is not None and devices, "should list something"
    assert all(isinstance(d, str) for d in devices)
