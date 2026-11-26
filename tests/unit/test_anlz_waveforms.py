"""Tests for the detail and colour waveform tags.

The byte layouts here were measured from Rekordbox's own ``.EXT`` files rather
than taken from a specification, so the tests assert the measured invariants:

* ``PWV3`` packs whiteness and height into one byte, exactly like ``PWAV``.
* ``PWV5`` packs r/g/b/height into a big-endian u16 and **always leaves the
  low two bits clear** — true for every entry of every real file examined.
* ``PWV4`` is 1200 entries of six bytes, with red, green and blue at offsets
  3, 4 and 5. That placement was established by correlating each offset
  against the independently-decoded ``PWV5`` channels of the same track.
"""

from __future__ import annotations

import math
import struct

import pytest

from core import anlz

# ── PWV3, monochrome detail ─────────────────────────────────────────────────


def test_detail_tag_header_matches_rekordbox() -> None:
    tag = anlz.waveform_detail_tag([5] * 300)
    entry_bytes, entries, magic = struct.unpack_from(">III", tag.body, 12)
    assert entry_bytes == 1
    assert entries == 300
    assert magic == 0x00960000, "0x96 = 150 entries per second"
    assert len(tag.payload) == 300


def test_detail_packs_height_in_the_low_five_bits() -> None:
    tag = anlz.waveform_detail_tag([0, 1, 17, 31])
    assert [b & 0x1F for b in tag.payload] == [0, 1, 17, 31]


def test_detail_clamps_out_of_range_heights() -> None:
    tag = anlz.waveform_detail_tag([-4, 99])
    assert [b & 0x1F for b in tag.payload] == [0, 31]


# ── PWV5, colour detail ─────────────────────────────────────────────────────


def test_colour_detail_header_matches_rekordbox() -> None:
    tag = anlz.waveform_colour_detail_tag([3, 3], [(1, 2, 3), (4, 5, 6)])
    entry_bytes, entries, magic = struct.unpack_from(">III", tag.body, 12)
    assert entry_bytes == 2
    assert entries == 2
    assert magic == 0x00960305
    assert len(tag.payload) == 4


def test_colour_detail_round_trips_every_channel() -> None:
    heights = [0, 15, 31]
    colours = [(0, 0, 0), (7, 3, 1), (2, 7, 7)]
    tag = anlz.waveform_colour_detail_tag(heights, colours)
    for i, (h, (r, g, b)) in enumerate(zip(heights, colours, strict=True)):
        w = struct.unpack_from(">H", tag.payload, i * 2)[0]
        assert (w >> 13) & 7 == r
        assert (w >> 10) & 7 == g
        assert (w >> 7) & 7 == b
        assert (w >> 2) & 0x1F == h


def test_colour_detail_leaves_the_low_two_bits_clear() -> None:
    """Measured across every entry of every real file: always zero."""
    tag = anlz.waveform_colour_detail_tag([31] * 50, [(7, 7, 7)] * 50)
    words = [struct.unpack_from(">H", tag.payload, i * 2)[0] for i in range(50)]
    assert all(w & 3 == 0 for w in words)


def test_colour_detail_stops_at_the_shorter_input() -> None:
    tag = anlz.waveform_colour_detail_tag([1, 2, 3], [(1, 1, 1)])
    assert len(tag.payload) == 2


# ── PWV4, colour preview ────────────────────────────────────────────────────


def test_colour_preview_is_always_1200_entries() -> None:
    for n in (10, 1199, 1200, 9000):
        tag = anlz.waveform_colour_preview_tag([8] * n, [(1, 2, 3)] * n)
        entry_bytes, entries, _ = struct.unpack_from(">III", tag.body, 12)
        assert entry_bytes == 6
        assert entries == 1200
        assert len(tag.payload) == 7200


def test_colour_preview_puts_rgb_at_offsets_three_four_five() -> None:
    """Established by correlation against PWV5, not from a spec."""
    tag = anlz.waveform_colour_preview_tag([31] * 1200, [(7, 0, 0)] * 1200)
    entry = tag.payload[600 * 6 : 600 * 6 + 6]
    assert entry[3] > 200, "red channel"
    assert entry[4] == 0, "green"
    assert entry[5] == 0, "blue"


def test_colour_preview_first_two_bytes_sum_to_full_scale() -> None:
    """byte[0] + byte[1] sits at 255 in real files; it reads as a split bar."""
    tag = anlz.waveform_colour_preview_tag([20] * 1200, [(3, 3, 3)] * 1200)
    for i in (0, 599, 1199):
        e = tag.payload[i * 6 : i * 6 + 6]
        assert e[0] + e[1] == 255


def test_colour_preview_averages_its_span_rather_than_peaking() -> None:
    """The same saturation trap as the detail waveform, one level up.

    A five-minute track is ~45,000 detail columns folded into 1200, so each
    output column covers ~37 of them. Taking the loudest of the 37 makes a
    track with any transient content read as full scale everywhere. Measured
    on Rekordbox's own files, byte[0] averages 28 of 255 — nowhere near the
    ceiling.
    """
    n = 1200 * 40
    heights = [0] * n
    colours = [(0, 0, 0)] * n
    # One loud column in every span of 40, as a transient would be.
    for i in range(0, n, 40):
        heights[i] = 31
        colours[i] = (7, 7, 7)

    tag = anlz.waveform_colour_preview_tag(heights, colours)
    amps = list(tag.payload[0::6])
    assert max(amps) < 64, f"one loud column in 40 drove the overview to {max(amps)} of 255"
    assert max(tag.payload[3::6]) < 64, "same for the colour channels"


def test_colour_preview_tracks_loudness_across_the_track() -> None:
    """Quiet first half, loud second half must still be visibly different."""
    n = 1200 * 10
    heights = [4] * (n // 2) + [28] * (n // 2)
    colours = [(1, 1, 1)] * (n // 2) + [(6, 6, 6)] * (n // 2)
    tag = anlz.waveform_colour_preview_tag(heights, colours)
    amps = list(tag.payload[0::6])
    assert amps[300] * 3 < amps[900], f"{amps[300]} vs {amps[900]}"


def test_colour_preview_of_silence_is_flat() -> None:
    tag = anlz.waveform_colour_preview_tag([0] * 1200, [(0, 0, 0)] * 1200)
    assert set(tag.payload[3::6]) == {0}
    assert set(tag.payload[4::6]) == {0}
    assert set(tag.payload[5::6]) == {0}


def test_colour_preview_survives_an_empty_analysis() -> None:
    tag = anlz.waveform_colour_preview_tag([], [])
    assert len(tag.payload) == 7200
    assert set(tag.payload) == {0, 255}


# ── The .EXT as a whole ─────────────────────────────────────────────────────


def _ext(**kwargs) -> anlz.Anlz:
    return anlz.parse(anlz.make_ext("/Contents/x.mp3", [], **kwargs))


def test_ext_without_analysis_is_unchanged() -> None:
    """No waveform and no grid: the cue-only file we wrote before."""
    assert _ext().fourccs == ["PPTH", "PCOB", "PCOB", "PCO2", "PCO2"]


def test_ext_does_not_carry_the_extended_grid() -> None:
    """Rekordbox 7 writes no PQT2, so neither do we.

    Eight real `.EXT` files from a Rekordbox 7 device export were checked and
    none contains the tag; their order is exactly ours without it. Ours was
    built from a template lifted from a different track, with an unknown field
    at +44 that may be a checksum and an entry encoding that cannot be
    verified against anything on this machine. Shipping a tag Rekordbox never
    writes, wrong, risks the reader discarding the whole file.
    """
    beats = anlz.beats_from_grid(128.0, 100.0, 10_000.0)
    exact = anlz.exact_beat_times(128.0, 100.0, 10_000.0)
    a = _ext(beats=beats, exact_ms=exact, duration_ms=10_000.0)
    assert "PQT2" not in a.fourccs


def _pqt2_header(bpm: float, first_ms: float, duration_ms: float):
    """The PQT2 header as an independent reader parses it."""
    beats = anlz.beats_from_grid(bpm, first_ms, duration_ms)
    exact = anlz.exact_beat_times(bpm, first_ms, duration_ms)
    tag = anlz.extended_grid_tag(beats, exact, duration_ms)
    b = tag.body
    return {
        "first": struct.unpack_from(">HHI", b, 24),  # beat, tempo, ms
        "last": struct.unpack_from(">HHI", b, 32),
        "entry_count": struct.unpack_from(">I", b, 40)[0],
        "beats": beats,
    }


def test_extended_grid_states_the_real_tempo() -> None:
    """The header used to keep the template's tempo.

    `_PQT2_HEADER_TEMPLATE` was lifted from one real 126 BPM file, and only
    the times and beat count were overwritten — so every track we exported
    declared 126.00 BPM in its extended grid whatever its actual tempo. A
    reader that trusts this field gets a grid that drifts against the audio.
    """
    h = _pqt2_header(127.0, 223.0, 323_000.0)
    assert h["first"][1] == 12700, f"got {h['first'][1]}, expected 127.00 BPM"
    assert h["last"][1] == 12700


def test_extended_grid_header_brackets_the_grid() -> None:
    h = _pqt2_header(140.0, 250.0, 60_000.0)
    beats = h["beats"]
    assert h["entry_count"] == len(beats)
    assert h["first"][0] == beats[0][0], "first beat number"
    assert h["first"][2] == beats[0][2], "first beat time"
    assert h["last"][0] == beats[-1][0], "last beat number"
    assert h["last"][2] == beats[-1][2], "last beat time"


def test_extended_grid_tempo_follows_the_track() -> None:
    for bpm in (98.5, 124.0, 174.0):
        h = _pqt2_header(bpm, 0.0, 30_000.0)
        assert h["first"][1] == round(bpm * 100), bpm


def test_ext_carries_the_colour_waveforms() -> None:
    wf = anlz.Waveform(preview=(8,) * 400, detail=(8,) * 300, colour=((3, 2, 1),) * 300)
    a = _ext(waveform=wf)
    assert "PWV3" in a.fourccs
    assert "PWV5" in a.fourccs
    assert "PWV4" in a.fourccs


def test_ext_tag_order_matches_rekordbox() -> None:
    """Measured on eight real Rekordbox 7 `.EXT` files, all identical."""
    wf = anlz.Waveform(preview=(8,) * 400, detail=(8,) * 300, colour=((3, 2, 1),) * 300)
    beats = anlz.beats_from_grid(128.0, 0.0, 4_000.0)
    a = _ext(
        beats=beats,
        exact_ms=anlz.exact_beat_times(128.0, 0.0, 4_000.0),
        duration_ms=4_000.0,
        waveform=wf,
    )
    assert a.fourccs == ["PPTH", "PWV3", "PCOB", "PCOB", "PCO2", "PCO2", "PWV5", "PWV4"]


# ── PVBR ────────────────────────────────────────────────────────────────────


def test_dat_carries_pvbr() -> None:
    """Rekordbox writes PVBR in every `.DAT`, even for uncompressed audio."""
    a = anlz.parse(anlz.make_dat("/Contents/x.wav", [], []))
    assert a.fourccs[:2] == ["PPTH", "PVBR"]


def test_pvbr_matches_the_shape_rekordbox_writes() -> None:
    """1620 bytes total, 16-byte header, 1604 bytes of payload, all zero.

    Measured identical across every reference `.DAT`. Rekordbox fills it for
    VBR MP3s; for everything else it writes zeros, which is what a seek table
    we have not computed should say.
    """
    tag = anlz.vbr_tag()
    assert tag.len_header == 16
    assert len(tag.body) == 1620
    assert len(tag.payload) == 1604
    assert set(tag.body[12:]) == {0}


def test_dat_tag_order_matches_rekordbox() -> None:
    beats = anlz.beats_from_grid(130.0, 0.0, 4_000.0)
    a = anlz.parse(anlz.make_dat("/Contents/x.wav", beats, [], [8] * 400))
    assert a.fourccs == ["PPTH", "PVBR", "PQTZ", "PWAV", "PWV2", "PCOB", "PCOB"]


# ── Column count ────────────────────────────────────────────────────────────


def test_detail_column_count_rounds_up(tmp_path) -> None:
    """Rekordbox's counts were one higher than ours on every reference file.

    Measured: 265/289/2058 against our 264/288/2057. A partial final column
    is still a column.
    """
    sf = pytest.importorskip("soundfile")
    np = pytest.importorskip("numpy")
    rate = 44100
    # 1.77 s -> 265.5 columns at 150/s, so floor and ceil disagree.
    p = tmp_path / "partial.wav"
    sf.write(p, np.zeros(int(rate * 1.77), dtype="float32"), rate)

    wf = anlz.analyse_waveform(p)
    assert wf is not None
    assert len(wf.detail) == math.ceil(1.77 * anlz.DETAIL_RATE)


def test_ext_stays_parseable(tmp_path) -> None:
    wf = anlz.Waveform(preview=(8,) * 400, detail=(8,) * 900, colour=((3, 2, 1),) * 900)
    raw = anlz.make_ext("/Contents/x.mp3", [anlz.Cue(time_ms=1000, hot_cue=1)], waveform=wf)
    again = anlz.build(anlz.parse(raw))
    assert again == raw, "parse -> build must be lossless"


# ── The analysis itself ─────────────────────────────────────────────────────


@pytest.fixture
def tone(tmp_path):
    """A file with bass in the first half and treble in the second."""
    sf = pytest.importorskip("soundfile")
    np = pytest.importorskip("numpy")
    rate = 44100
    t = np.arange(rate * 2) / rate
    sig = np.where(t < 1.0, np.sin(2 * np.pi * 60 * t), np.sin(2 * np.pi * 8000 * t)).astype(
        "float32"
    )
    p = tmp_path / "tone.wav"
    sf.write(p, sig, rate)
    return p


def test_analysis_separates_bass_from_treble(tone) -> None:
    """Bass is RED and treble is BLUE, which is the way round Rekordbox has it.

    Established against eight reference files by correlating each of our
    bands against the decoded PWV5 channels of the same audio: red<-low,
    green<-mid, blue<-high, unanimous 8/8. This was originally implemented
    the other way round, and the blue channel came out at -0.64 against
    Rekordbox's.
    """
    wf = anlz.analyse_waveform(tone)
    assert wf is not None
    n = len(wf.colour)
    assert n > 100, "150 entries per second of a two-second file"

    first = wf.colour[n // 8]  # inside the 60 Hz half
    second = wf.colour[-n // 8]  # inside the 8 kHz half
    assert first[0] > first[2], "bass reads red, not blue"
    assert second[2] > second[0], "treble reads blue, not red"


def test_analysis_rate_is_150_entries_per_second(tone) -> None:
    wf = anlz.analyse_waveform(tone)
    assert wf is not None
    assert 285 <= len(wf.detail) <= 305, f"got {len(wf.detail)} for 2 s"
    assert len(wf.detail) == len(wf.colour)


def test_analysis_preview_is_400_columns(tone) -> None:
    wf = anlz.analyse_waveform(tone)
    assert wf is not None and len(wf.preview) == 400
    assert max(wf.preview) == 31, "normalised to full height"


def test_analysis_channels_stay_in_range(tone) -> None:
    wf = anlz.analyse_waveform(tone)
    assert wf is not None
    assert all(0 <= h <= 31 for h in wf.detail)
    assert all(0 <= c <= 7 for rgb in wf.colour for c in rgb)


@pytest.fixture
def quiet_then_loud(tmp_path):
    """A sparse-transient intro, then a sustained loud section.

    Both halves carry full-scale clicks four times a second, so a
    peak-per-column measure reads them as equally loud — which is exactly how
    a mastered track defeats it. Only the second half is sustained, so an RMS
    measure separates them.
    """
    sf = pytest.importorskip("soundfile")
    np = pytest.importorskip("numpy")
    rate = 44100
    t = np.arange(rate * 2) / rate
    sig = np.where(t < 1.0, 0.0, np.sin(2 * np.pi * 440 * t) * 0.9)
    for click in range(8):
        at = int(click * rate / 4)
        sig[at : at + 8] = 1.0
    p = tmp_path / "dynamics.wav"
    sf.write(p, sig.astype("float32"), rate)
    return p


def test_analysis_keeps_the_dynamics(quiet_then_loud) -> None:
    """The detail waveform must not saturate.

    At 150 columns a second each column is under 7 ms, and almost every such
    window of a mastered track touches the track peak — so a peak-per-column
    measure renders as a solid block. Measured on real tracks, peak+sqrt put
    the 10th percentile at height 14 of 31; RMS keeps it near 2.
    """
    wf = anlz.analyse_waveform(quiet_then_loud)
    assert wf is not None
    n = len(wf.detail)
    quiet = wf.detail[n // 8]
    loud = wf.detail[-n // 8]
    assert loud > 3 * quiet, f"quiet={quiet} loud={loud}: no dynamic range"


def test_analysis_does_not_saturate(quiet_then_loud) -> None:
    wf = anlz.analyse_waveform(quiet_then_loud)
    assert wf is not None
    at_ceiling = sum(1 for h in wf.detail if h > 30) / len(wf.detail)
    assert at_ceiling < 0.6, f"{at_ceiling:.0%} of columns at full height"


def test_analysis_of_an_unreadable_file_is_none(tmp_path) -> None:
    bad = tmp_path / "not-audio.mp3"
    bad.write_bytes(b"this is not audio")
    assert anlz.analyse_waveform(bad) is None


def test_waveform_heights_still_works(tone) -> None:
    """The old entry point is still used by tests and callers."""
    h = anlz.waveform_heights(tone)
    assert len(h) == 400
    assert all(0 <= v <= 31 for v in h)
