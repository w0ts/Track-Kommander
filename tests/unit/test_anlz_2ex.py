"""Tests for ``ANLZ0000.2EX`` — Rekordbox 7's third analysis file.

Rekordbox 7 writes a `.2EX` beside every `.DAT`/`.EXT`, and it was assumed
players did not need it. It holds newer colour waveforms:

======  ====================================================================
PWV7    colour detail, **3 bytes per column** at 150/s — the 8-bit-per-channel
        successor to ``PWV5``, whose channels are only 3 bits
PWV6    colour preview, 3 bytes x 1200 — the successor to ``PWV4``
PWVC    six bytes, three big-endian u16s, purpose unknown
======  ====================================================================

Everything here was measured on eight real `.2EX` files from a Rekordbox 7
device export. ``PWV6`` byte *i* correlates 0.73-0.97 with ``PWV4``'s channel
*i* and with ``PWV7``'s byte *i* across all eight, which is what establishes
the byte order as red, green, blue.
"""

from __future__ import annotations

import struct

import pytest

from core import anlz

COLOURS = [(7, 3, 1), (0, 0, 0), (4, 4, 4), (1, 6, 2)]


# ── PWV7, colour detail ─────────────────────────────────────────────────────


def test_colour_detail_rgb_header_matches_rekordbox() -> None:
    tag = anlz.colour_detail_rgb_tag(COLOURS)
    entry_bytes, entries, magic = struct.unpack_from(">III", tag.body, 12)
    assert entry_bytes == 3
    assert entries == len(COLOURS)
    assert magic == 0x00960000, "the same 150-per-second marker as PWV3"
    assert tag.len_header == 24
    assert len(tag.payload) == 3 * len(COLOURS)


def test_colour_detail_rgb_keeps_channel_order() -> None:
    tag = anlz.colour_detail_rgb_tag([(7, 0, 0), (0, 7, 0), (0, 0, 7)])
    p = tag.payload
    assert p[0] > 0 and p[1] == 0 and p[2] == 0, "red only"
    assert p[3] == 0 and p[4] > 0 and p[5] == 0, "green only"
    assert p[6] == 0 and p[7] == 0 and p[8] > 0, "blue only"


def test_colour_detail_rgb_spans_the_byte() -> None:
    """3-bit channels widen to 8-bit ones; full scale must reach near 255."""
    tag = anlz.colour_detail_rgb_tag([(7, 7, 7)])
    assert all(b > 200 for b in tag.payload)


def test_colour_detail_rgb_clamps() -> None:
    tag = anlz.colour_detail_rgb_tag([(-1, 99, 3)])
    assert tag.payload[0] == 0
    assert tag.payload[1] <= 255


# ── PWV6, colour preview ────────────────────────────────────────────────────


def test_colour_preview_rgb_header_matches_rekordbox() -> None:
    tag = anlz.colour_preview_rgb_tag([8] * 50, [(1, 2, 3)] * 50)
    entry_bytes, entries = struct.unpack_from(">II", tag.body, 12)
    assert entry_bytes == 3
    assert entries == anlz.COLOUR_PREVIEW_COLUMNS == 1200
    assert tag.len_header == 20, "two fields, not three — unlike PWV7"
    assert len(tag.payload) == 3600


def test_colour_preview_rgb_is_always_3600_bytes() -> None:
    for n in (1, 500, 1200, 40_000):
        tag = anlz.colour_preview_rgb_tag([8] * n, [(2, 2, 2)] * n)
        assert len(tag.payload) == 3600


def test_colour_preview_rgb_averages_rather_than_peaks() -> None:
    """Same saturation trap as PWV4; one loud column in 40 must not fill it."""
    n = 1200 * 40
    colours = [(0, 0, 0)] * n
    for i in range(0, n, 40):
        colours[i] = (7, 7, 7)
    tag = anlz.colour_preview_rgb_tag([0] * n, colours)
    assert max(tag.payload) < 64


def test_colour_preview_rgb_of_silence_is_flat() -> None:
    tag = anlz.colour_preview_rgb_tag([0] * 1200, [(0, 0, 0)] * 1200)
    assert set(tag.payload) == {0}


def test_colour_preview_rgb_survives_no_analysis() -> None:
    tag = anlz.colour_preview_rgb_tag([], [])
    assert len(tag.payload) == 3600


# ── PWVC ────────────────────────────────────────────────────────────────────


def test_wave_colour_tag_shape() -> None:
    tag = anlz.wave_colour_tag()
    assert tag.len_header == 14
    assert len(tag.payload) == 6
    assert len(struct.unpack(">HHH", tag.payload)) == 3


# ── The file ────────────────────────────────────────────────────────────────


def _wf(n: int = 300) -> anlz.Waveform:
    return anlz.Waveform(preview=(8,) * 400, detail=(8,) * n, colour=((3, 2, 1),) * n)


def test_2ex_tag_order_matches_rekordbox() -> None:
    a = anlz.parse(anlz.make_2ex("/Contents/x.mp3", _wf()))
    assert a.fourccs == ["PPTH", "PWV7", "PWV6", "PWVC"]


def test_2ex_path_matches_the_other_files() -> None:
    a = anlz.parse(anlz.make_2ex("/Contents/Some Track.mp3", _wf()))
    path = a.get("PPTH").payload.decode("utf-16-be").rstrip("\x00")
    assert path == "/Contents/Some Track.mp3"


def test_2ex_detail_length_follows_the_analysis() -> None:
    a = anlz.parse(anlz.make_2ex("/Contents/x.mp3", _wf(1234)))
    _eb, entries, _m = struct.unpack_from(">III", a.get("PWV7").body, 12)
    assert entries == 1234
    assert len(a.get("PWV7").payload) == 3 * 1234


def test_2ex_round_trips() -> None:
    raw = anlz.make_2ex("/Contents/x.mp3", _wf())
    assert anlz.build(anlz.parse(raw)) == raw


def test_2ex_without_colour_is_not_written() -> None:
    """Nothing useful to say without an analysis, and an empty PWV6 would
    claim a flat waveform rather than an absent one."""
    assert anlz.make_2ex("/Contents/x.mp3", None) is None
    empty = anlz.Waveform(preview=(), detail=(), colour=())
    assert anlz.make_2ex("/Contents/x.mp3", empty) is None


@pytest.mark.parametrize("n", [1, 150, 45_000])
def test_2ex_sizes_are_consistent(n: int) -> None:
    a = anlz.parse(anlz.make_2ex("/Contents/x.mp3", _wf(n)))
    assert len(a.get("PWV7").payload) == 3 * n
    assert len(a.get("PWV6").payload) == 3600
