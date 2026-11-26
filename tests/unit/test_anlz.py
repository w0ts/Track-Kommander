"""Tests for the Rekordbox ANLZ reader/writer.

These files carry the beat grid, hotcues and waveforms a CDJ needs; the
``export.pdb`` database alone only lists what is on the stick. The format is
reverse engineered, so the load-bearing test is the round trip: every real
Rekordbox file on this machine must survive ``parse`` → ``build`` byte for
byte. If that holds, the reader understands the container completely, and a
file we generate is structurally the same kind of thing.

That test skips where no Rekordbox analysis data exists, so the synthetic
tests below carry the weight on CI.
"""

from __future__ import annotations

import glob
import struct
from pathlib import Path

import pytest

from core import anlz
from core.anlz import (
    Anlz,
    Cue,
    beat_grid_tag,
    beats_from_grid,
    build,
    cue2_tag,
    cue_tag,
    exact_beat_times,
    make_dat,
    make_ext,
    parse,
    parse_file,
    path_tag,
    waveform_preview_tag,
)

REKORDBOX_ANLZ = Path.home() / "Library/Pioneer/rekordbox/share/PIONEER/USBANLZ"


def _real_files() -> list[str]:
    if not REKORDBOX_ANLZ.is_dir():
        return []
    return sorted(
        glob.glob(str(REKORDBOX_ANLZ / "**" / "ANLZ0000.DAT"), recursive=True)
        + glob.glob(str(REKORDBOX_ANLZ / "**" / "ANLZ0000.EXT"), recursive=True)
    )


# ── Against real Rekordbox output ───────────────────────────────────────────


@pytest.mark.skipif(not _real_files(), reason="no Rekordbox analysis files here")
def test_real_files_round_trip_byte_for_byte() -> None:
    """The proof that the container is fully understood."""
    for f in _real_files():
        raw = Path(f).read_bytes()
        assert build(parse(raw)) == raw, f"{f} did not round-trip"


@pytest.mark.skipif(not _real_files(), reason="no Rekordbox analysis files here")
def test_real_beat_grid_is_sane() -> None:
    """Beat spacing must match the tempo it claims."""
    for f in _real_files():
        anlz = parse_file(Path(f))
        tag = anlz.get("PQTZ")
        if tag is None or len(tag.payload) < 16:
            continue
        beats = [
            struct.unpack_from(">HHI", tag.payload, i * 8) for i in range(len(tag.payload) // 8)
        ]
        _n, tempo, t0 = beats[0]
        _n, _tempo, t1 = beats[1]
        expected = 60_000.0 / (tempo / 100.0)
        assert abs((t1 - t0) - expected) < 2.0, f"{f}: spacing != tempo"
        return
    pytest.skip("no beat grids found")


# ── Container ───────────────────────────────────────────────────────────────


def test_rejects_a_non_anlz_file() -> None:
    with pytest.raises(ValueError, match="PMAI"):
        parse(b"nope" + bytes(40))


def test_rejects_a_truncated_file() -> None:
    good = make_dat("/x.mp3", [], [])
    with pytest.raises(ValueError, match="declared length"):
        parse(good[:-4])


def test_rejects_a_tag_running_past_the_end() -> None:
    raw = bytearray(make_dat("/x.mp3", [], []))
    # Inflate the first tag's length beyond the file.
    struct.pack_into(">I", raw, 28 + 8, 10_000)
    struct.pack_into(">I", raw, 4 + 4, len(raw))
    with pytest.raises(ValueError):
        parse(bytes(raw))


def test_build_fixes_up_the_file_length() -> None:
    data = make_dat("/music/track.mp3", [], [])
    _magic, len_header, len_file = struct.unpack_from(">4sII", data, 0)
    assert len_file == len(data)
    assert len_header == 28


def test_unknown_tags_survive_a_rebuild() -> None:
    """Colour waveforms we can't generate must not be dropped when editing."""
    original = make_dat("/x.mp3", [], [])
    anlz = parse(original)
    fake = Anlz(header=anlz.header, tags=list(anlz.tags))
    rebuilt = parse(build(fake))
    assert rebuilt.fourccs == anlz.fourccs


# ── Individual tags ─────────────────────────────────────────────────────────


def test_path_is_utf16_be_and_nul_terminated() -> None:
    tag = path_tag("/Contents/Añejo.mp3")
    declared = struct.unpack_from(">I", tag.body, 12)[0]
    text = tag.payload[:declared].decode("utf-16-be")
    assert text.rstrip("\x00") == "/Contents/Añejo.mp3"
    assert text.endswith("\x00")


def test_beat_grid_encodes_tempo_times_100() -> None:
    tag = beat_grid_tag([(1, 126.0, 60), (2, 126.0, 536)])
    number, tempo, ms = struct.unpack_from(">HHI", tag.payload, 0)
    assert (number, tempo, ms) == (1, 12600, 60)


def test_beat_grid_count_matches_the_payload() -> None:
    beats = [(i % 4 + 1, 128.0, i * 469) for i in range(50)]
    tag = beat_grid_tag(beats)
    declared = struct.unpack_from(">I", tag.body, 20)[0]
    assert declared == 50
    assert len(tag.payload) == 50 * 8


def test_empty_cue_list_matches_rekordbox() -> None:
    """Rekordbox writes 0xFFFFFFFF as the memory count on an empty list."""
    tag = cue_tag([], hot=False)
    memory_count = struct.unpack_from(">I", tag.body, 20)[0]
    assert memory_count == 0xFFFFFFFF
    assert tag.payload == b""


def test_cue_entries_are_counted_and_ordered() -> None:
    cues = [Cue(time_ms=1000, hot_cue=1), Cue(time_ms=2000, hot_cue=2)]
    tag = cue_tag(cues, hot=True)
    kind, _unk, count = struct.unpack_from(">IHH", tag.body, 12)
    assert kind == 1
    assert count == 2
    assert tag.payload[:4] == b"PCPT"


def test_a_loop_is_marked_as_a_loop() -> None:
    tag = cue_tag([Cue(time_ms=1000, hot_cue=1, loop_end_ms=3000)], hot=True)
    assert tag.payload[28] == 2, "type byte at entry offset 28: 2 = loop"
    assert struct.unpack_from(">I", tag.payload, 36)[0] == 3000, "loop end"


def test_waveform_preview_is_always_400_columns() -> None:
    assert len(waveform_preview_tag([10] * 20).payload) == 400
    assert len(waveform_preview_tag([10] * 900).payload) == 400


def test_waveform_heights_are_clamped() -> None:
    data = waveform_preview_tag([0, 31, 99, -5]).payload
    assert data[0] & 0x1F == 0
    assert data[1] & 0x1F == 31
    assert data[2] & 0x1F == 31, "over-range must clamp, not wrap"
    assert data[3] & 0x1F == 0


# ── Whole files ─────────────────────────────────────────────────────────────


def test_dat_contains_what_a_player_needs() -> None:
    beats = beats_from_grid(128.0, 20.0, 10_000)
    cues = [Cue(time_ms=5000, hot_cue=1)]
    anlz = parse(make_dat("/Contents/a.mp3", beats, cues, [8] * 400))
    # Same tag order a real device .DAT uses — measured on eight reference
    # files from a Rekordbox 7 export, which all carry PVBR after PPTH.
    assert anlz.fourccs == ["PPTH", "PVBR", "PQTZ", "PWAV", "PWV2", "PCOB", "PCOB"]


def test_dat_separates_memory_and_hot_cues() -> None:
    """Hot list first, then memory — the order Rekordbox writes."""
    cues = [Cue(time_ms=1000), Cue(time_ms=2000, hot_cue=1)]
    anlz = parse(make_dat("/a.mp3", [], cues))
    hot, memory = anlz.getall("PCOB")
    assert struct.unpack_from(">I", hot.body, 12)[0] == 1
    assert struct.unpack_from(">I", memory.body, 12)[0] == 0
    assert struct.unpack_from(">H", hot.body, 18)[0] == 1
    assert struct.unpack_from(">H", memory.body, 18)[0] == 1


def test_ext_carries_the_extended_cue_lists() -> None:
    anlz = parse(make_ext("/a.mp3", [Cue(time_ms=1000, hot_cue=1)]))
    assert "PCO2" in anlz.fourccs
    assert len(anlz.getall("PCO2")) == 2, "one memory list, one hot list"


def test_generated_files_reparse() -> None:
    for data in (
        make_dat("/a.mp3", beats_from_grid(120, 0, 5000), [Cue(time_ms=100, hot_cue=1)], [4] * 400),
        make_ext("/a.mp3", [Cue(time_ms=100, hot_cue=1)]),
    ):
        assert build(parse(data)) == data


# ── Grid generation from Traktor's single anchor ────────────────────────────


def test_grid_spacing_follows_the_tempo() -> None:
    beats = beats_from_grid(120.0, 0.0, 3000)
    times = [ms for _n, _b, ms in beats]
    assert times[:4] == [0, 500, 1000, 1500]


def test_grid_starts_at_or_after_zero() -> None:
    """Traktor's anchor can be well into the track; the grid still starts at
    the first beat of the file, not at the anchor."""
    beats = beats_from_grid(120.0, 10_000.0, 12_000)
    assert beats[0][2] < 500
    assert all(ms >= 0 for _n, _b, ms in beats)


def test_grid_beat_numbers_cycle_one_to_four() -> None:
    numbers = [n for n, _b, _ms in beats_from_grid(120.0, 0.0, 5000)]
    assert numbers[:8] == [1, 2, 3, 4, 1, 2, 3, 4]


# Real failure: the numbering started at 1 on the first beat of the file
# rather than on the anchor, so an anchor three beats in was labelled beat 4
# and the player drew every bar line three beats early.


@pytest.mark.parametrize("anchor_ms", [0.0, 500.0, 1000.0, 1500.0, 2000.0, 10_500.0])
def test_the_anchor_is_a_downbeat_wherever_it_is(anchor_ms: float) -> None:
    beats = beats_from_grid(120.0, anchor_ms, 12_000)
    at_anchor = [n for n, _b, ms in beats if ms == int(anchor_ms)]
    assert at_anchor == [1]


# Real failure: an anchor a few milliseconds before the start of the file —
# which Traktor stores, and which the export's offset can produce — gave a
# first beat at a negative time. It is packed unsigned, so the whole export
# stopped on a struct.error.


def test_an_anchor_before_zero_starts_the_grid_at_the_next_beat() -> None:
    beats = beats_from_grid(120.0, -20.0, 3000)
    assert [ms for _n, _b, ms in beats][:3] == [480, 980, 1480]
    # The anchor itself, at -20 ms, is beat 1; 480 ms is the beat after it.
    assert [n for n, _b, _ms in beats][:4] == [2, 3, 4, 1]


def test_exact_times_match_the_grid_before_zero() -> None:
    exact = exact_beat_times(120.0, -20.0, 3000)
    assert exact[:3] == [480.0, 980.0, 1480.0]
    assert len(exact) == len(beats_from_grid(120.0, -20.0, 3000))


def test_a_grid_before_zero_packs() -> None:
    beat_grid_tag(beats_from_grid(128.0, -20.0, 60_000))


@pytest.mark.parametrize("hot", [0, 1, 5])
def test_a_cue_before_zero_lands_on_the_start(hot: int) -> None:
    """Shifted there by the export's offset. The start of the file is the
    nearest place a player can put it."""
    cue = Cue(time_ms=-5, hot_cue=hot)
    assert (
        cue_tag([cue], hot=bool(hot)).body
        == cue_tag([Cue(time_ms=0, hot_cue=hot)], hot=bool(hot)).body
    )
    assert (
        cue2_tag([cue], hot=bool(hot)).body
        == cue2_tag([Cue(time_ms=0, hot_cue=hot)], hot=bool(hot)).body
    )


def test_grid_covers_the_whole_track() -> None:
    beats = beats_from_grid(128.0, 0.0, 60_000)
    assert abs(len(beats) - 128) <= 1, "128 BPM for 60 s is ~128 beats"


@pytest.mark.parametrize(("bpm", "duration"), [(0, 1000), (-5, 1000), (120, 0)])
def test_degenerate_grids_are_empty_not_crashes(bpm: float, duration: float) -> None:
    assert beats_from_grid(bpm, 0.0, duration) == []


# ── Byte-identical to Rekordbox ─────────────────────────────────────────────
# Captured from a Rekordbox 7 "Export to device" of a track with all eight
# hotcues set. These are the strongest tests here — not "does it parse" but
# "is it the same bytes Rekordbox writes".

REAL_HOTCUE_TIMES = {1: 61, 2: 5775, 3: 9585, 4: 12918, 5: 16251, 6: 20061, 7: 24346, 8: 27680}

#: The .DAT hot-cue tag for that track: hotcues C, B, A, descending.
REAL_DAT_PCOB = bytes.fromhex(
    "50434f42000000180000010800000001000000030000000050435054"
    "0000001c000000380000000300000000000100 00ffffffff010003e8"
    "00002571ffffffff000002ce000000000002332400000000"
    "504350540000001c0000003800000002000000000001"
    "0000ffffffff010003e800001 68fffffffff000001b100000000"
    "000150940000000050435054 0000001c000000380000000100000000"
    "00010000ffffffff010003e80000003dffffffff00000004"
    "0000000000000000 00000000".replace(" ", "")
)


def _real_hotcues() -> list[Cue]:
    return [Cue(time_ms=t, hot_cue=h) for h, t in REAL_HOTCUE_TIMES.items()]


#: One real PCPT entry, captured from a Rekordbox 7 device export: hotcue C at
#: 17347 ms, with the derived CD-frame field zero (Rekordbox leaves it zero on
#: some tracks, which is why the writer may fill it and still be valid).
REAL_PCPT_HEX = (
    "504350540000001c00000038000000030000000000010000ffffffff"
    "010003e8000043c3ffffffff00000000000000000000000000000000"
)

#: An empty memory-cue list, byte for byte.
REAL_PCOB_EMPTY_HEX = "50434f4200000018000000180000000000000000ffffffff"


def test_a_real_cue_entry_is_reproduced_byte_for_byte() -> None:
    """Captured bytes, not a fixture reading a stick whose contents change."""
    from core.anlz import _cue_entry

    mine = bytearray(_cue_entry(Cue(time_ms=17347, hot_cue=3)))
    # Blank the CD-frame field: Rekordbox left it zero on the captured track.
    mine[40:44] = b"\x00\x00\x00\x00"
    assert bytes(mine) == bytes.fromhex(REAL_PCPT_HEX)


def test_an_empty_cue_list_is_reproduced_byte_for_byte() -> None:
    assert cue_tag([], hot=False).body == bytes.fromhex(REAL_PCOB_EMPTY_HEX)


def test_only_the_first_three_hotcues_go_in_the_dat() -> None:
    """Original CDJs read three hotcues and only look at the .DAT."""
    anlz = parse(make_dat("/a.mp3", [], _real_hotcues()))
    hot = anlz.getall("PCOB")[0]
    assert struct.unpack_from(">H", hot.body, 18)[0] == 3


def test_the_remaining_hotcues_go_in_the_ext() -> None:
    anlz = parse(make_ext("/a.mp3", _real_hotcues()))
    hot_pcob = anlz.getall("PCOB")[0]
    hot_pco2 = anlz.getall("PCO2")[0]
    assert struct.unpack_from(">H", hot_pcob.body, 18)[0] == 5, "D-H"
    assert struct.unpack_from(">H", hot_pco2.body, 16)[0] == 8, "all of them"


def test_cues_are_written_highest_slot_first() -> None:
    tag = cue_tag(_real_hotcues()[:3], hot=True)
    order = [struct.unpack_from(">I", tag.payload, i * 56 + 12)[0] for i in range(3)]
    assert order == [3, 2, 1], "Rekordbox writes them descending"


def test_position_is_also_stored_in_cd_frames() -> None:
    """Entry offset 40 is the same position in 1/75 s units, truncated."""
    tag = cue_tag([Cue(time_ms=9585, hot_cue=1)], hot=True)
    assert struct.unpack_from(">I", tag.payload, 40)[0] == 718


def test_cue_entry_length_and_header() -> None:
    """56 bytes, 28 of header — measured from a real export, not the spec."""
    from core.anlz import _cue_entry

    entry = _cue_entry(Cue(time_ms=1000, hot_cue=1))
    assert struct.unpack_from(">4sII", entry, 0) == (b"PCPT", 28, 56)


def test_extended_cue_entry_length_and_header() -> None:
    from core.anlz import _cue2_entry

    entry = _cue2_entry(Cue(time_ms=1000, hot_cue=1))
    assert struct.unpack_from(">4sII", entry, 0) == (b"PCP2", 16, 88)


def test_hotcue_colour_lands_at_the_right_offset() -> None:
    """Bytes 45–47 of a PCP2 entry are the cue's RGB."""
    from core.anlz import _cue2_entry

    entry = _cue2_entry(Cue(time_ms=1, hot_cue=1, rgb=(0x12, 0x34, 0x56)))
    assert entry[45:48] == bytes((0x12, 0x34, 0x56))


def test_stock_colours_are_the_real_ones() -> None:
    """Read out of a real export of a track with all eight hotcues set."""
    from core.anlz import HOTCUE_COLOURS, cue_colour

    assert HOTCUE_COLOURS[0] == (0xFF, 0x00, 0x17)
    assert HOTCUE_COLOURS[7] == (0xFF, 0xE8, 0x00)
    assert cue_colour(Cue(time_ms=0, hot_cue=3)) == (0x33, 0xFF, 0x00)
    assert cue_colour(Cue(time_ms=0, hot_cue=0)) == (0, 0, 0)


def test_the_trailing_word_is_not_an_entry_count() -> None:
    """Named "memory_count" in the published spec, but Rekordbox writes
    0xFFFFFFFF even on a populated list — verified against a real export."""
    tag = cue_tag(_real_hotcues()[:3], hot=True)
    assert struct.unpack_from(">I", tag.body, 20)[0] == 0xFFFFFFFF
    assert struct.unpack_from(">H", tag.body, 18)[0] == 3, "count lives here"


# ── Extended beat grid (PQT2) — what Rekordbox itself reads ─────────────────


def test_extended_grid_is_two_bytes_per_beat() -> None:
    from core.anlz import exact_beat_times, extended_grid_tag

    exact = exact_beat_times(126.0, 60.226, 60_000)
    beats = beats_from_grid(126.0, 60.226, 60_000)
    tag = extended_grid_tag(beats, exact, 60_000)
    assert len(tag.payload) == 2 * len(beats)
    assert tag.len_header == 56, "real files use a 56-byte header"


def test_extended_grid_stores_the_fractional_millisecond() -> None:
    """PQTZ rounds beats to whole ms; PQT2 restores the precision."""
    from core.anlz import extended_grid_tag

    exact = [100.226, 200.417, 300.607]
    beats = [(1, 120.0, 100), (2, 120.0, 200), (3, 120.0, 300)]
    tag = extended_grid_tag(beats, exact, 1000)
    got = [struct.unpack_from(">H", tag.payload, i * 2)[0] for i in range(3)]
    assert got == [226, 417, 607]


def test_extended_grid_records_count_and_length() -> None:
    """Offsets 24..40 are two PQTZ-shaped entries, not a time and a length.

    This originally asserted that +36 held the track duration, which was a
    guess. pyrekordbox's struct — from Deep Symmetry's reverse engineering —
    reads 24..32 and 32..40 as ``(beat, tempo, time)`` pairs bracketing the
    grid, and the template's own values fit that: beat 1 and beat 4, with
    times matching the first and last beat rather than the track length.
    """
    from core.anlz import exact_beat_times, extended_grid_tag

    exact = exact_beat_times(128.0, 0.0, 30_000)
    beats = beats_from_grid(128.0, 0.0, 30_000)
    tag = extended_grid_tag(beats, exact, 30_000)
    assert struct.unpack_from(">I", tag.body, 40)[0] == len(beats)
    assert struct.unpack_from(">HHI", tag.body, 24) == (beats[0][0], 12800, beats[0][2])
    assert struct.unpack_from(">HHI", tag.body, 32) == (beats[-1][0], 12800, beats[-1][2])


def test_exact_beat_times_match_the_rounded_grid() -> None:
    from core.anlz import exact_beat_times

    exact = exact_beat_times(120.0, 0.0, 3000)
    rounded = [ms for _n, _b, ms in beats_from_grid(120.0, 0.0, 3000)]
    assert len(exact) == len(rounded)
    assert all(abs(e - r) < 1.0 for e, r in zip(exact, rounded, strict=True))


def test_degenerate_exact_grid_is_empty() -> None:
    from core.anlz import exact_beat_times

    assert exact_beat_times(0, 0, 1000) == []
    assert exact_beat_times(120, 0, 0) == []


# ── Waveform computation ────────────────────────────────────────────────────


def test_waveform_uses_the_resilient_loader(monkeypatch, tmp_path) -> None:
    """libsndfile returns "Unspecified internal error" on some real-world
    MP3s; core.audio_io already falls back to PyAV, so this must go through
    it rather than calling soundfile directly."""
    from core import anlz as mod

    called = {}

    def _fake(path, **kw):
        import numpy as np

        called["path"] = path
        return np.linspace(-1, 1, 4410, dtype="float32").reshape(-1, 1), 44100

    monkeypatch.setattr("core.audio_io.safe_sf_read", _fake)
    heights = mod.waveform_heights(tmp_path / "x.mp3")
    assert called, "must route through safe_sf_read"
    assert len(heights) == 400


def test_waveform_of_an_unreadable_file_is_empty_not_fatal(tmp_path) -> None:
    """A missing waveform is cosmetic; it must never abort an export."""
    from core.anlz import waveform_heights

    junk = tmp_path / "notaudio.mp3"
    junk.write_text("nope")
    assert waveform_heights(junk) == []
    assert waveform_heights(tmp_path / "absent.mp3") == []


def test_waveform_of_silence_is_flat(monkeypatch, tmp_path) -> None:
    from core import anlz as mod

    def _silent(path, **kw):
        import numpy as np

        return np.zeros((4410, 1), dtype="float32"), 44100

    monkeypatch.setattr("core.audio_io.safe_sf_read", _silent)
    assert mod.waveform_heights(tmp_path / "x.mp3") == [0] * 400


# ── Matching what an XDJ-1000 accepts ──────────────────────────────────────
#
# Our export showed neither waveform nor hot cues on an XDJ-1000, though both
# were in the files. Rekordbox 7.2.18's files for the same track load. These
# pin the bytes that differed.

#: The 28-byte header of all three files Rekordbox wrote, lengths zeroed.
RB_HEADER_TAIL = bytes.fromhex("00000001000100000001000000000000")


def test_the_file_header_is_rekordboxs() -> None:
    for raw in (anlz.make_dat("/Contents/a.mp3", [], []), anlz.make_ext("/Contents/a.mp3", [])):
        assert raw[:4] == b"PMAI"
        assert raw[12:28] == RB_HEADER_TAIL


def test_the_tiny_preview_is_one_column_per_byte_low_nibble() -> None:
    """Rekordbox writes 0x0e, 0x0f…: height 0–15 in the low four bits and
    nothing above. Ours wrote the height into both nibbles (0xff)."""
    tag = anlz.waveform_tiny_tag([31] * 400)
    data = tag.payload
    assert len(data) == 100
    assert all(b <= 0x0F for b in data)
    assert data[0] == 15
