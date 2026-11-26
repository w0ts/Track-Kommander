"""Read and write Rekordbox ANLZ analysis files (``ANLZ0000.DAT`` / ``.EXT``).

These are the files that make a USB stick actually useful on a CDJ. The
``export.pdb`` database lists what is on the stick; the ANLZ files carry the
**beat grid, hotcues and waveforms** for each track. Without them a player may
list a track and even play it, but with no waveform, no grid and no cues —
which is most of the point.

Format
------

A file is a ``PMAI`` header followed by a flat sequence of tags::

    PMAI  len_header(u32)  len_file(u32)  <padding to len_header>
      PPTH  len_header(u32)  len_tag(u32)  <fields…>  <payload…>
      PQTZ  …
      …

Every integer is big-endian. Each tag declares its *own* header length, and
the variable payload starts at that offset — the fixed fields live inside the
header, not the payload. Tag lengths tile the file exactly, which is what
:func:`parse` asserts and what makes a byte-for-byte rebuild possible.

Tags this module understands
----------------------------

=======  ============================================================
``PPTH`` file path, UTF-16BE
``PQTZ`` beat grid — (beat number, tempo×100, milliseconds) per beat
``PCOB`` cues, original format (one tag for memory cues, one for hot cues)
``PCO2`` cues, extended format (adds colour and comments) — ``.EXT`` only
``PWAV`` 400-byte monochrome waveform preview
``PWV2`` 100-byte tiny preview
``PWV3`` scrolling monochrome waveform, 150 columns per second
``PWV4`` 1200-column colour overview — what Rekordbox's browser draws
``PWV5`` scrolling colour waveform, 150 columns per second
``PQT2`` extended beat grid — the one Rekordbox itself reads
=======  ============================================================

The colour waveform byte layouts were **measured, not taken from a
specification**: red, green and blue sit at offsets 3, 4 and 5 of a ``PWV4``
entry, established by correlating each offset against the independently
decoded ``PWV5`` channels of the same track across four real files.

``PSSI`` phrase analysis is carried through verbatim when rebuilding a file
that already had it, but is not generated.

``ANLZ0000.2EX``
----------------

Rekordbox 7 writes a third file beside the two above, holding newer colour
waveforms — and **Rekordbox's own browser draws its Preview column from it**,
so without it that column stays blank:

=======  ============================================================
``PWV7`` colour detail, 3 bytes per column at 150/s — the 8-bit-per-channel
         successor to ``PWV5``, whose channels are only 3 bits
``PWV6`` colour preview, 3 bytes × 1200 — successor to ``PWV4``
``PWVC`` six bytes, three big-endian u16s, purpose unknown
=======  ============================================================

Verified against 21 real Rekordbox 7 exports: every one round-trips through
:func:`parse` and :func:`build` byte for byte. Against a reference export of
the same audio, our ``PQTZ``, ``PCOB`` and ``PCO2`` are byte-for-byte
identical and every tag header matches; the waveform payloads necessarily
differ, and correlate 0.80 (``PWV3``) and 0.84/0.66/0.67 (``PWV6`` r/g/b).

Rekordbox 7 also writes an encrypted ``ANLZ0000.3EX``. Players do not need it,
and it is not produced here.
"""

from __future__ import annotations

import logging
import math
import struct
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

FILE_MAGIC = b"PMAI"
FILE_HEADER_LEN = 28

# Cue list kinds — one PCOB/PCO2 tag each.
CUE_LIST_MEMORY = 0
CUE_LIST_HOT = 1

# Cue entry status: 0 = disabled, 4 = the value Rekordbox writes for an
# active cue.
CUE_STATUS_ACTIVE = 4

#: Rekordbox's stock hotcue colours for slots A–H, read out of a real export
#: of a track with all eight set — not guessed.
HOTCUE_COLOURS: tuple[tuple[int, int, int], ...] = (
    (0xFF, 0x00, 0x17),  # A — red
    (0x00, 0xC4, 0xFF),  # B — cyan
    (0x33, 0xFF, 0x00),  # C — green
    (0x4D, 0x00, 0xFF),  # D — violet
    (0x00, 0xFF, 0x30),  # E — bright green
    (0xFF, 0x5E, 0x00),  # F — orange
    (0x00, 0x00, 0xFF),  # G — blue
    (0xFF, 0xE8, 0x00),  # H — yellow
)

#: Only hotcues A–C go in the ``.DAT``; D–H live in the ``.EXT``. Original
#: CDJs read three hotcues and only look at the ``.DAT``, so putting more
#: there would be writing data an old player would have to ignore — and
#: Rekordbox doesn't. Measured from a real export of a track with all eight.
DAT_MAX_HOTCUE = 3


@dataclass
class Tag:
    """One ANLZ tag, kept as raw bytes so unknown tags survive a rebuild."""

    fourcc: str
    len_header: int
    body: bytes
    """The complete tag: fourcc, lengths, fields and payload."""

    @property
    def payload(self) -> bytes:
        return self.body[self.len_header :]


@dataclass
class Anlz:
    """A parsed ANLZ file."""

    tags: list[Tag] = field(default_factory=list)
    header: bytes = b""

    def get(self, fourcc: str) -> Tag | None:
        return next((t for t in self.tags if t.fourcc == fourcc), None)

    def getall(self, fourcc: str) -> list[Tag]:
        return [t for t in self.tags if t.fourcc == fourcc]

    @property
    def fourccs(self) -> list[str]:
        return [t.fourcc for t in self.tags]


# ── Reading ─────────────────────────────────────────────────────────────────


def parse(raw: bytes) -> Anlz:
    """Parse an ANLZ file. Raises ValueError on anything unexpected."""
    if len(raw) < FILE_HEADER_LEN or raw[:4] != FILE_MAGIC:
        raise ValueError("not an ANLZ file (bad PMAI magic)")
    len_header, len_file = struct.unpack_from(">II", raw, 4)
    if len_file != len(raw):
        raise ValueError(f"declared length {len_file} != actual {len(raw)}")

    out = Anlz(header=raw[:len_header])
    off = len_header
    while off + 12 <= len(raw):
        fourcc, tag_header, tag_len = struct.unpack_from(">4sII", raw, off)
        if tag_len < 12 or off + tag_len > len(raw):
            raise ValueError(f"tag {fourcc!r} at {off} has bad length {tag_len}")
        out.tags.append(
            Tag(fourcc.decode("ascii", "replace"), tag_header, raw[off : off + tag_len])
        )
        off += tag_len
    if off != len(raw):
        raise ValueError(f"tags end at {off}, file is {len(raw)}")
    return out


def parse_file(path: Path) -> Anlz:
    return parse(Path(path).read_bytes())


# ── Writing ─────────────────────────────────────────────────────────────────


def build(anlz: Anlz) -> bytes:
    """Serialise back to bytes, fixing up the file length."""
    header = bytearray(anlz.header or _default_header())
    body = b"".join(t.body for t in anlz.tags)
    struct.pack_into(">II", header, 4, len(header), len(header) + len(body))
    return bytes(header) + body


def _default_header() -> bytes:
    # Matches what Rekordbox writes: 28 bytes, the two lengths, then a fixed
    # pattern whose meaning is undocumented. Rekordbox 7.2.18 writes
    # 1, 0x10000, 0x10000, 0 in all three files. This used to write 1 in the
    # third place, and an XDJ-1000 then showed neither the waveform nor the
    # hot cues of a track whose files held both.
    h = bytearray(FILE_HEADER_LEN)
    h[0:4] = FILE_MAGIC
    struct.pack_into(">II", h, 4, FILE_HEADER_LEN, FILE_HEADER_LEN)
    struct.pack_into(">IIII", h, 12, 1, 0x00010000, 0x00010000, 0)
    return bytes(h)


def _tag(fourcc: bytes, fields: bytes, payload: bytes = b"") -> Tag:
    """Assemble a tag from its fixed fields and variable payload."""
    len_header = 12 + len(fields)
    len_tag = len_header + len(payload)
    body = struct.pack(">4sII", fourcc, len_header, len_tag) + fields + payload
    return Tag(fourcc.decode(), len_header, body)


def path_tag(file_path: str) -> Tag:
    """``PPTH`` — the track's path as the player should see it.

    Rekordbox stores a NUL-terminated UTF-16BE string and counts the
    terminator in the length.
    """
    encoded = file_path.encode("utf-16-be") + b"\x00\x00"
    return _tag(b"PPTH", struct.pack(">I", len(encoded)), encoded)


def beat_grid_tag(beats: list[tuple[int, float, int]]) -> Tag:
    """``PQTZ`` — the beat grid.

    *beats* is (beat number within the bar 1–4, BPM, milliseconds). Tempo is
    stored as BPM×100, so 126.0 becomes 12600.
    """
    payload = b"".join(
        struct.pack(">HHI", number & 0xFFFF, round(bpm * 100) & 0xFFFF, int(ms))
        for number, bpm, ms in beats
    )
    fields = struct.pack(">III", 0, 0x00080000, len(beats))
    return _tag(b"PQTZ", fields, payload)


@dataclass
class Cue:
    """One cue point. *hot_cue* is 0 for a memory cue, 1–8 for hotcues A–H."""

    time_ms: int
    hot_cue: int = 0
    loop_end_ms: int | None = None
    rgb: tuple[int, int, int] | None = None
    """Explicit colour; None uses Rekordbox's stock colour for the slot."""
    comment: str = ""

    @property
    def is_loop(self) -> bool:
        return self.loop_end_ms is not None and self.loop_end_ms > self.time_ms


def _position(ms: float) -> int:
    """A time as the tags store it: whole milliseconds, never before zero.

    The field is unsigned. A cue shifted before the start of the file by the
    export's offset goes on the start, the nearest place a player can put it.
    """
    return max(0, int(ms))


def _loop_end(cue: Cue) -> int:
    """A loop's end position, or the all-ones marker for a plain cue."""
    if cue.loop_end_ms is None or not cue.is_loop:
        return 0xFFFFFFFF
    return _position(cue.loop_end_ms)


def _cue_entry(cue: Cue) -> bytes:
    """One ``PCPT`` entry inside a PCOB tag.

    Layout taken byte-for-byte from a real Rekordbox 7 device export rather
    than from the spec — 56 bytes total, 28 of them header. The constants
    (``0x00010000``, the ``0xFFFF`` order fields and the ``0x0003E8`` after
    the type) are reproduced exactly as Rekordbox writes them; their meaning
    is not documented and guessing differently is not worth the risk.
    """
    kind = 2 if cue.is_loop else 1
    start = _position(cue.time_ms)
    loop_end = _loop_end(cue)
    body = struct.pack(
        ">IIIHHB3sII",
        cue.hot_cue,
        0,  # status
        0x00010000,
        0xFFFF,
        0xFFFF,  # order_first / order_last
        kind,
        b"\x00\x03\xe8",
        start,
        loop_end,
    )
    # The last 16 bytes are two derived u32s, not padding:
    #   +0  the same position in CD frames (1/75 s), truncated — verified
    #       against all eight cues of a real export.
    #   +8  a byte offset into the audio file, used to seek VBR files
    #       directly. Computing it needs an MP3 frame index we don't build, so
    #       it is left zero — which Rekordbox itself does for some tracks, so
    #       zero is demonstrably valid rather than a guess.
    tail = bytearray(16)
    struct.pack_into(">I", tail, 0, start * 75 // 1000)
    body += bytes(tail)
    return struct.pack(">4sII", b"PCPT", 28, 12 + len(body)) + body


def _ordered(cues: list[Cue]) -> list[Cue]:
    """Highest hotcue first — the order Rekordbox writes, and what a
    byte-for-byte match requires."""
    return sorted(cues, key=lambda c: (-c.hot_cue, c.time_ms))


def cue_tag(cues: list[Cue], hot: bool) -> Tag:
    """``PCOB`` — the original cue list. One tag for memory, one for hotcues."""
    entries = b"".join(_cue_entry(c) for c in _ordered(cues))
    kind = CUE_LIST_HOT if hot else CUE_LIST_MEMORY
    # The trailing u32 is 0xFFFFFFFF in every real file inspected — empty
    # memory lists, empty hot lists, and a populated hot list of three. It is
    # *not* an entry count, which is what the name "memory_count" in the
    # published spec suggests. No sample of a populated *memory* list was
    # available, so this may need revisiting if one ever disagrees.
    fields = struct.pack(">IHHI", kind, 0, len(cues), 0xFFFFFFFF)
    return _tag(b"PCOB", fields, entries)


def _cue2_entry(cue: Cue) -> bytes:
    """One ``PCP2`` entry inside a PCO2 tag — 88 bytes, 16 of header.

    Also copied from a real export. Bytes 45–47 of the entry are the hotcue's
    RGB colour, which is how Rekordbox shows A–H in different colours.
    """
    kind = 2 if cue.is_loop else 1
    loop_end = _loop_end(cue)
    body = bytearray(88 - 12)
    struct.pack_into(">I", body, 0, cue.hot_cue)  # entry offset 12
    body[4] = kind  # offset 16
    struct.pack_into(">H", body, 6, 1000)  # offset 18: 0x03E8
    struct.pack_into(">I", body, 8, _position(cue.time_ms))  # offset 20
    struct.pack_into(">I", body, 12, loop_end)  # offset 24
    struct.pack_into(">H", body, 16, 1)  # offset 28
    r, g, b = cue_colour(cue)
    body[33], body[34], body[35] = r, g, b  # offsets 45–47
    return struct.pack(">4sII", b"PCP2", 16, 88) + bytes(body)


def cue_colour(cue: Cue) -> tuple[int, int, int]:
    """RGB for a cue: its own if set, else Rekordbox's stock colour for the slot."""
    if cue.rgb is not None:
        return cue.rgb
    if 1 <= cue.hot_cue <= len(HOTCUE_COLOURS):
        return HOTCUE_COLOURS[cue.hot_cue - 1]
    return (0, 0, 0)


def cue2_tag(cues: list[Cue], hot: bool) -> Tag:
    """``PCO2`` — the extended cue list (colours). ``.EXT`` only."""
    entries = b"".join(_cue2_entry(c) for c in _ordered(cues))
    kind = CUE_LIST_HOT if hot else CUE_LIST_MEMORY
    fields = struct.pack(">IHH", kind, len(cues), 0)
    return _tag(b"PCO2", fields, entries)


def waveform_preview_tag(heights: list[int]) -> Tag:
    """``PWAV`` — the 400-column monochrome overview.

    Each byte is a column: the low 5 bits are the height (0–31), the upper 3
    a whiteness value. Rekordbox always writes exactly 400.
    """
    data = bytes(
        ((min(7, max(0, h // 4)) & 0x07) << 5) | (min(31, max(0, h)) & 0x1F) for h in heights[:400]
    ).ljust(400, b"\x00")
    return _tag(b"PWAV", struct.pack(">II", len(data), 0x00010000), data)


def waveform_tiny_tag(heights: list[int]) -> Tag:
    """``PWV2`` — the 100-column tiny preview, one byte per column.

    The height, 0–15, sits in the low four bits and nothing is above it:
    Rekordbox 7.2.18 writes 0x0e, 0x0f, … This used to copy the height into
    the high nibble as well (0xff).
    """
    packed = bytearray(100)
    for i in range(100):
        packed[i] = min(15, max(0, heights[i] // 2)) if i < len(heights) else 0
    return _tag(b"PWV2", struct.pack(">II", len(packed), 0x00010000), bytes(packed))


#: Analysis entries per second of audio for the *detail* waveforms, which is
#: what the 0x96 in their tag headers says.
DETAIL_RATE = 150

#: Bump whenever :func:`analyse_waveform` would produce different numbers for
#: the same audio — a new band split, a different scaling curve, a fix to the
#: column count. Every cached waveform carries this, so raising it invalidates
#: all of them and the next export re-analyses. See
#: :mod:`core.waveform_cache`.
WAVEFORM_VERSION = 1

#: The colour preview is a fixed width whatever the track length.
COLOUR_PREVIEW_COLUMNS = 1200

#: Band edges in Hz for the colour split: below/between/above. Rekordbox's
#: exact crossovers are not published; these reproduce the balance seen in
#: real files, where bass dominates the blue channel.
BAND_EDGES = (200.0, 2000.0)


def waveform_detail_tag(heights: list[int]) -> Tag:
    """``PWV3`` — the scrolling monochrome waveform, 150 columns per second.

    Same byte packing as :func:`waveform_preview_tag`: height in the low five
    bits, whiteness in the top three.
    """
    data = bytes(
        ((min(7, max(0, h // 4)) & 0x07) << 5) | (min(31, max(0, h)) & 0x1F) for h in heights
    )
    fields = struct.pack(">III", 1, len(data), 0x00960000)
    return _tag(b"PWV3", fields, data)


def waveform_colour_detail_tag(heights: list[int], colours: list[tuple[int, int, int]]) -> Tag:
    """``PWV5`` — the scrolling colour waveform, 150 columns per second.

    Each column is a big-endian u16::

        rrrg ggbb bhhh hh00

    Red, green and blue are 0–7 and the height 0–31. The low two bits are
    clear in every entry of every real file examined, so they are left clear
    here too rather than used for anything.
    """
    packed = bytearray()
    for h, (r, g, b) in zip(heights, colours, strict=False):
        word = (
            ((min(7, max(0, r))) << 13)
            | ((min(7, max(0, g))) << 10)
            | ((min(7, max(0, b))) << 7)
            | ((min(31, max(0, h))) << 2)
        )
        packed += struct.pack(">H", word)
    fields = struct.pack(">III", 2, len(packed) // 2, 0x00960305)
    return _tag(b"PWV5", fields, bytes(packed))


def waveform_colour_preview_tag(heights: list[int], colours: list[tuple[int, int, int]]) -> Tag:
    """``PWV4`` — the fixed-width colour overview the browser draws.

    1200 entries of six bytes. Red, green and blue sit at offsets 3, 4 and 5;
    that placement was measured by correlating each offset against the
    independently-decoded ``PWV5`` channels of the same track, not taken from
    a specification. Bytes 0 and 1 sum to full scale in real files and read as
    a bar split about a centre line; byte 2 tracks the strongest channel.
    """
    n = min(len(heights), len(colours))
    out = bytearray(COLOUR_PREVIEW_COLUMNS * 6)
    for i in range(COLOUR_PREVIEW_COLUMNS):
        if n == 0:
            r = g = b = h = 0
        else:
            # Mean, not max, over the detail columns this one covers. A
            # five-minute track folds ~45,000 columns into 1200, so taking
            # the loudest of each ~37 makes anything with transients read as
            # full scale everywhere — the same trap as peak-per-column in
            # _column_levels, one level up.
            lo = i * n // COLOUR_PREVIEW_COLUMNS
            hi = max(lo + 1, (i + 1) * n // COLOUR_PREVIEW_COLUMNS)
            span = colours[lo:hi]
            width = len(span)
            r = sum(c[0] for c in span) / width
            g = sum(c[1] for c in span) / width
            b = sum(c[2] for c in span) / width
            h = sum(heights[lo:hi]) / len(heights[lo:hi])
        # 0–7 and 0–31 scale up to the 0–255 the preview uses.
        r8, g8, b8 = min(252, int(r * 36)), min(252, int(g * 36)), min(252, int(b * 36))
        amp = min(255, int(h * 255 / 31))
        base = i * 6
        out[base : base + 6] = bytes((amp, 255 - amp, max(r8, g8, b8), r8, g8, b8))
    fields = struct.pack(">III", 6, COLOUR_PREVIEW_COLUMNS, 0)
    return _tag(b"PWV4", fields, bytes(out))


#: Widens a 3-bit channel (0–7) to a byte. 36 puts full scale at 252, matching
#: what :func:`waveform_colour_preview_tag` already does for ``PWV4``.
#:
#: Rekordbox's own ``PWV7`` values are lower than this — but its reference
#: files are quiet two-second clips, and its ``PWV5`` and ``PWV7`` do not even
#: agree with each other on channel balance (the byte-per-channel ratio is
#: ~5.7 for red and ~0.6 for blue). Brightness here is **not calibrated**; the
#: shape is what a renderer needs, and this errs bright rather than invisible.
CHANNEL_SCALE = 36


def colour_detail_rgb_tag(colours: list[tuple[int, int, int]]) -> Tag:
    """``PWV7`` — colour detail at 150 columns per second, a byte per channel.

    Rekordbox 7's successor to :func:`waveform_colour_detail_tag`, which packs
    the same three channels into 3 bits each. Lives in the ``.2EX``.
    """
    payload = bytes(
        max(0, min(7, channel)) * CHANNEL_SCALE  # 7 * 36 = 252
        for rgb in colours
        for channel in rgb[:3]
    )
    fields = struct.pack(">III", 3, len(colours), 0x00960000)
    return _tag(b"PWV7", fields, payload)


def colour_preview_rgb_tag(heights: list[int], colours: list[tuple[int, int, int]]) -> Tag:
    """``PWV6`` — the 1200-column colour overview, a byte per channel.

    This is what Rekordbox 7's browser draws in its Preview column; ``PWV4``
    alone left that column empty. Two header fields, not three — unlike every
    other waveform tag, there is no 150-per-second marker, because a preview
    is a fixed width whatever the track length.
    """
    n = min(len(heights), len(colours)) if heights else len(colours)
    out = bytearray(COLOUR_PREVIEW_COLUMNS * 3)
    for i in range(COLOUR_PREVIEW_COLUMNS):
        if n == 0:
            continue
        # Mean over the span, for the reason in waveform_colour_preview_tag.
        lo = i * n // COLOUR_PREVIEW_COLUMNS
        hi = max(lo + 1, (i + 1) * n // COLOUR_PREVIEW_COLUMNS)
        span = colours[lo:hi]
        width = len(span)
        for ch in range(3):
            mean = sum(max(0, min(7, c[ch])) for c in span) / width
            out[i * 3 + ch] = min(252, int(mean * CHANNEL_SCALE))
    return _tag(b"PWV6", struct.pack(">II", 3, COLOUR_PREVIEW_COLUMNS), bytes(out))


#: ``PWVC``'s three u16s vary per track — (81, 228, 130), (153, 248, 80),
#: (197, 292, 139) across the references — and correlate with nothing
#: derivable from the audio, the grid or the other waveforms that was tried.
#: These are the medians. **Unverified**; a renderer that scales by them would
#: change brightness, not whether anything appears.
WAVE_COLOUR_VALUES = (104, 231, 118)


def wave_colour_tag() -> Tag:
    """``PWVC`` — six bytes of unknown meaning, present in every ``.2EX``."""
    return _tag(b"PWVC", b"\x00\x00", struct.pack(">HHH", *WAVE_COLOUR_VALUES))


def make_2ex(file_path: str, waveform: Waveform | None) -> bytes | None:
    """Build ``ANLZ0000.2EX``, or None when there is no analysis to put in it.

    Rekordbox 7 writes this beside the ``.DAT`` and ``.EXT``. It was assumed
    players did not need it, which is true of players — but Rekordbox's own
    browser reads its ``PWV6`` for the Preview column, and without the file
    that column stays blank.
    """
    if waveform is None or not waveform.colour:
        return None
    colours = list(waveform.colour)
    anlz = Anlz(header=_default_header())
    anlz.tags.append(path_tag(file_path))
    anlz.tags.append(colour_detail_rgb_tag(colours))
    anlz.tags.append(colour_preview_rgb_tag(list(waveform.detail), colours))
    anlz.tags.append(wave_colour_tag())
    return build(anlz)


#: A ``PVBR`` is a fixed 1620 bytes: 12 of tag header, one 4-byte field, then
#: 1604 bytes of seek table. Rekordbox writes one in every ``.DAT`` — even for
#: uncompressed audio, where every byte of it is zero. Measured identical
#: across every reference file.
PVBR_PAYLOAD_LEN = 1604


def vbr_tag() -> Tag:
    """``PVBR`` — the VBR seek table, written empty.

    Rekordbox fills this for variable-bitrate MP3s so a player can seek
    accurately. We do not compute one, and an invented table would send a
    player to the wrong place; all zeros is what Rekordbox itself writes when
    there is nothing to say, so that is what goes here.
    """
    return _tag(b"PVBR", b"\x00" * 4, b"\x00" * PVBR_PAYLOAD_LEN)


def make_dat(
    file_path: str,
    beats: list[tuple[int, float, int]],
    cues: list[Cue],
    heights: list[int] | None = None,
) -> bytes:
    """Build a complete ``ANLZ0000.DAT``.

    Tag order matches what Rekordbox writes; players are not known to care,
    but matching it keeps diffs against a real export readable.
    """
    anlz = Anlz(header=_default_header())
    anlz.tags.append(path_tag(file_path))
    anlz.tags.append(vbr_tag())
    if beats:
        anlz.tags.append(beat_grid_tag(beats))
    if heights:
        anlz.tags.append(waveform_preview_tag(heights))
        anlz.tags.append(waveform_tiny_tag(heights))
    # Only A–C belong in the .DAT (see DAT_MAX_HOTCUE).
    hot = [c for c in cues if 0 < c.hot_cue <= DAT_MAX_HOTCUE]
    memory = [c for c in cues if not c.hot_cue]
    anlz.tags.append(cue_tag(hot, hot=True))
    anlz.tags.append(cue_tag(memory, hot=False))
    return build(anlz)


def make_ext(
    file_path: str,
    cues: list[Cue],
    beats: list[tuple[int, float, int]] | None = None,
    exact_ms: list[float] | None = None,
    duration_ms: float = 0.0,
    waveform: Waveform | None = None,
) -> bytes:
    """Build a complete ``ANLZ0000.EXT``.

    Tag order follows Rekordbox's own files: ``PPTH, PWV3, PCOB, PCOB, PCO2,
    PCO2, PWV5, PWV4``. Rekordbox reads the grid from ``PQT2`` here rather
    than the ``PQTZ`` in the ``.DAT``, and draws the browser's preview from
    ``PWV4``, so a ``.EXT`` with only cues shows a track with neither.
    """
    anlz = Anlz(header=_default_header())
    anlz.tags.append(path_tag(file_path))
    if waveform and waveform.detail:
        anlz.tags.append(waveform_detail_tag(list(waveform.detail)))

    hot = [c for c in cues if c.hot_cue]
    memory = [c for c in cues if not c.hot_cue]
    # The .EXT's PCOB carries the hotcues the .DAT could not (D–H); PCO2
    # carries all of them, with colours.
    anlz.tags.append(cue_tag([c for c in hot if c.hot_cue > DAT_MAX_HOTCUE], hot=True))
    anlz.tags.append(cue_tag(memory, hot=False))
    anlz.tags.append(cue2_tag(hot, hot=True))
    anlz.tags.append(cue2_tag(memory, hot=False))

    if waveform and waveform.colour:
        heights = list(waveform.detail)
        colours = list(waveform.colour)
        anlz.tags.append(waveform_colour_detail_tag(heights, colours))
        anlz.tags.append(waveform_colour_preview_tag(heights, colours))
    # No PQT2. Rekordbox 7 writes none — checked on eight real `.EXT` files
    # from a device export, whose tag order is exactly the above. See
    # :func:`extended_grid_tag` for why writing ours anyway is a bad trade.
    return build(anlz)


def beats_from_grid(
    bpm: float, first_beat_ms: float, duration_ms: float
) -> list[tuple[int, float, int]]:
    """A constant-tempo beat grid, the shape Traktor stores.

    Traktor keeps one anchor plus a fixed BPM, so the grid is generated from
    those rather than read beat by beat. Beat numbers cycle 1–4 with the
    anchor as beat 1 (a downbeat), which is what Traktor's grid marker means.
    """
    if bpm <= 0 or duration_ms <= 0:
        return []
    period = 60_000.0 / bpm
    t, from_anchor = _grid_start(first_beat_ms, period)
    beats: list[tuple[int, float, int]] = []
    while t < duration_ms and len(beats) < _MAX_BEATS:
        beats.append(((from_anchor % 4) + 1, bpm, int(round(t))))
        t += period
        from_anchor += 1
    return beats


#: A grid longer than this is a tempo or a duration that cannot be right.
_MAX_BEATS = 100_000


def _grid_start(anchor_ms: float, period: float) -> tuple[float, int]:
    """The first beat at or after zero, and how many beats from the anchor
    it is — negative when the anchor comes later.

    The count is what keeps the bar lines where Traktor has them: the anchor
    is beat 1, so a beat *k* away from it is beat ``k % 4 + 1`` whichever
    side it is on. Counting from the first beat of the file instead labels
    the anchor with whatever it happens to land on.

    An anchor before zero is stepped forward. Traktor stores those, and the
    export's offset can push one there; a time before the start of the file
    is one a player has no way to hold.
    """
    start, from_anchor = anchor_ms, 0
    while start - period >= 0:
        start -= period
        from_anchor -= 1
    while start < 0:
        start += period
        from_anchor += 1
    return start, from_anchor


@dataclass(frozen=True)
class Waveform:
    """Everything the waveform tags need, from one decode of the audio.

    *preview* is 400 columns whatever the length; *detail* and *colour* are
    :data:`DETAIL_RATE` columns per second and always the same length as each
    other. Heights are 0–31 and colour channels 0–7, the ranges the tags
    store.
    """

    preview: tuple[int, ...]
    detail: tuple[int, ...]
    colour: tuple[tuple[int, int, int], ...]


def _column_levels(signal, columns: int):
    """RMS of each of *columns* equal slices of *signal*.

    RMS rather than peak. At 150 columns a second a column is under 7 ms, and
    almost every such window of a mastered track contains a transient at or
    near the track peak — so peak-per-column renders as a solid block. On real
    tracks the two differ sharply: peak puts the 10th percentile column at
    height 14 of 31, RMS at 2, which is the difference between a filled
    rectangle and a readable waveform.
    """
    import numpy as np

    if columns <= 0 or len(signal) == 0:
        return np.zeros(max(0, columns))
    # Cumulative sum of squares, so each column is two lookups rather than a
    # slice — a five-minute track is 45,000 columns.
    energy = np.concatenate(([0.0], np.cumsum(signal.astype("float64") ** 2)))
    edges = np.linspace(0, len(signal), columns + 1).astype(np.int64)
    widths = np.maximum(1, edges[1:] - edges[:-1])
    return np.sqrt((energy[edges[1:]] - energy[edges[:-1]]) / widths)


def _scale_heights(levels) -> tuple[int, ...]:
    """Column levels to 0–31, normalised to the loudest column.

    Linear, not square-root: RMS already sits well below the peak, and a
    further curve pushes the quiet parts back up into the loud ones.
    """
    loudest = levels.max() if len(levels) else 0.0
    if loudest <= 0:
        return tuple([0] * len(levels))
    return tuple(int(round(v)) for v in (levels / loudest) * 31.0)


def _band_energies(mono, rate: int, columns: int):
    """Low/mid/high energy per column, as three arrays.

    One short FFT per column, summed over three bands. Done in blocks so a
    long track does not need the whole spectrogram in memory at once.
    """
    import numpy as np

    size = max(16, len(mono) // max(1, columns))
    usable = size * columns
    if usable > len(mono):
        columns = len(mono) // size
        usable = size * columns
    if columns <= 0:
        return (np.zeros(0), np.zeros(0), np.zeros(0))

    freqs = np.fft.rfftfreq(size, 1.0 / rate)
    masks = (
        freqs < BAND_EDGES[0],
        (freqs >= BAND_EDGES[0]) & (freqs < BAND_EDGES[1]),
        freqs >= BAND_EDGES[1],
    )
    window = np.hanning(size).astype("float32")
    bands = [np.zeros(columns, dtype="float32") for _ in masks]

    chunk = max(1, 4_000_000 // max(1, size))  # bound peak memory
    for start in range(0, columns, chunk):
        stop = min(columns, start + chunk)
        frames = mono[start * size : stop * size].reshape(stop - start, size)
        spec = np.abs(np.fft.rfft(frames * window, axis=1))
        for band, mask in zip(bands, masks, strict=True):
            band[start:stop] = spec[:, mask].sum(axis=1)
    return tuple(bands)


def analyse_waveform(path: str | Path) -> Waveform | None:
    """Decode *path* once and derive every waveform the ANLZ files need.

    Returns ``None`` if the audio cannot be read — a missing waveform is a
    cosmetic loss, not a reason to abandon an export.
    """
    try:
        from core.audio_io import safe_sf_read
    except ImportError:  # pragma: no cover — both are hard dependencies
        return None

    try:
        # Not soundfile directly: libsndfile gives "Unspecified internal
        # error" on some of the malformed MP3s a real library contains, and
        # safe_sf_read already falls back to PyAV/ffmpeg for exactly those.
        data, rate = safe_sf_read(str(path), dtype="float32", always_2d=True)
    except Exception:
        log.debug("waveform: cannot read %s", path, exc_info=True)
        return None
    if data.size == 0 or not rate:
        return None

    signal = data.mean(axis=1).astype("float32")
    # Round up: a partial final column is still a column. Rekordbox's counts
    # were consistently one higher than ours before this — 265/289/2058
    # against 264/288/2057 on the reference files.
    columns = max(1, math.ceil(len(signal) / rate * DETAIL_RATE))

    preview = _scale_heights(_column_levels(signal, 400))
    detail = _scale_heights(_column_levels(signal, columns))

    low, mid, high = _band_energies(signal, int(rate), columns)
    n = min(len(low), len(detail))
    if n == 0:
        return Waveform(preview=preview, detail=detail, colour=())

    # One shared normaliser across the three bands, so the ratio between them
    # — which is what the colour means — survives. Square root again, for the
    # same reason as the heights.
    loudest = max(low.max(), mid.max(), high.max())
    if loudest <= 0:
        colour = tuple([(0, 0, 0)] * n)
    else:
        colour = colour_columns(low[:n], mid[:n], high[:n], float(loudest))
    return Waveform(preview=preview, detail=detail[:n], colour=colour)


def colour_columns(low, mid, high, loudest: float) -> tuple[tuple[int, int, int], ...]:
    """One (red, green, blue) per column, each 0–7, from the band energies.

    red<-low, green<-mid, blue<-high. Measured against eight reference
    files, unanimous: Rekordbox draws bass orange/red and treble blue. This
    was the other way round and put our blue at -0.64 against its.

    Done as arrays. A five-minute track is 45,000 columns, and building them
    one at a time in Python was a tenth of the cost of a waveform.
    """
    import numpy as np

    scaled = np.stack([np.sqrt(band / loudest) * 7.0 for band in (low, mid, high)], axis=1)
    # rint rounds a half to the even neighbour, as round() does.
    return tuple(map(tuple, np.rint(scaled).astype(np.int64).tolist()))


def waveform_from_files(dat_path: str | Path) -> Waveform | None:
    """Read a :class:`Waveform` back out of analysis already on a device.

    Decoding audio is the expensive part of an export, and a change to a
    track's cues does not alter a single sample of it — so when only the cues
    changed, the waveform can come from the files already written rather than
    from the audio again.

    Lossless for our purposes: what is stored is exactly the quantised form
    that would be written again — heights 0–31, colour channels 0–7 — so a
    rewrite round-trips byte for byte. Returns None if the files are absent or
    carry no waveform.
    """
    dat = Path(dat_path)
    ext = dat.with_suffix(".EXT")
    try:
        a = parse(dat.read_bytes())
        e = parse(ext.read_bytes()) if ext.is_file() else Anlz()
    except (OSError, ValueError):
        return None

    # Vectorised: a 35-minute track is ~319,000 columns, and unpacking those
    # one at a time took 2.7 s — longer than decoding the audio this exists to
    # avoid.
    import numpy as np

    preview_tag = a.get("PWAV")
    preview = (
        tuple((np.frombuffer(preview_tag.payload, dtype=np.uint8) & 0x1F).tolist())
        if preview_tag
        else ()
    )

    detail_tag = e.get("PWV3")
    detail = (
        tuple((np.frombuffer(detail_tag.payload, dtype=np.uint8) & 0x1F).tolist())
        if detail_tag
        else ()
    )

    colour: tuple[tuple[int, int, int], ...] = ()
    colour_tag = e.get("PWV5")
    if colour_tag:
        words = np.frombuffer(colour_tag.payload, dtype=">u2").astype(np.int32)
        rgb = np.stack([(words >> 13) & 7, (words >> 10) & 7, (words >> 7) & 7], axis=1)
        colour = tuple(map(tuple, rgb.tolist()))

    if not (preview or detail or colour):
        return None
    return Waveform(preview=preview, detail=detail, colour=colour)


def waveform_heights(path: str | Path, columns: int = 400) -> list[int]:
    """Column heights (0–31) for the monochrome waveform preview.

    Kept for callers that only need the overview; :func:`analyse_waveform`
    produces this and the rest from the same decode.
    """
    if columns == 400:
        wf = analyse_waveform(path)
        return list(wf.preview) if wf else []

    try:
        from core.audio_io import safe_sf_read
    except ImportError:  # pragma: no cover — a hard dependency
        return []
    try:
        data, _rate = safe_sf_read(str(path), dtype="float32", always_2d=True)
    except Exception:
        log.debug("waveform: cannot read %s", path, exc_info=True)
        return []
    if data.size == 0:
        return []
    return list(_scale_heights(_column_levels(data.mean(axis=1), columns)))


#: Header bytes 12..55 of a PQT2 tag, taken verbatim from a real Rekordbox 7
#: file. Five of the eleven words are understood and overwritten below; the
#: rest are copied because their meaning is not known and a wrong guess is
#: worse than an unchanged copy. ``+44`` in particular differs wildly between
#: files and may be a checksum.
_PQT2_HEADER_TEMPLATE = bytes.fromhex(
    "000000000100000200000000000131380000003c000431380006f0a6000003bc0dad9b330000000000000000"
)


def extended_grid_tag(
    beats: list[tuple[int, float, int]], exact_ms: list[float], duration_ms: float
) -> Tag:
    """``PQT2`` — the beat grid Rekordbox itself reads.

    ``PQTZ`` rounds every beat to a whole millisecond; this restores the lost
    precision as **one u16 per beat, the fractional millisecond × 1000**.
    Verified against a real constant-tempo export: 956 of 956 beats matched.
    (It does not hold on variable-tempo grids, where no single period
    describes the beats — Traktor stores one anchor plus a constant BPM, so
    ours are always the computable case.)
    """
    payload = b"".join(struct.pack(">H", int(round((t - int(t)) * 1000)) % 1000) for t in exact_ms)
    fields = bytearray(_PQT2_HEADER_TEMPLATE)
    if beats:
        # Two PQTZ-shaped entries bracketing the grid: (beat number, BPM×100,
        # milliseconds) for the first beat and the last. These were previously
        # left at the template's values, so every track declared the 126.00
        # BPM of the file the template came from.
        for offset, (number, bpm, ms) in ((12, beats[0]), (20, beats[-1])):
            struct.pack_into(
                ">HHI", fields, offset, number & 0xFFFF, round(bpm * 100) & 0xFFFF, int(ms)
            )
    struct.pack_into(">I", fields, 28, len(beats))
    return _tag(b"PQT2", bytes(fields), payload)


def exact_beat_times(bpm: float, first_beat_ms: float, duration_ms: float) -> list[float]:
    """The same beats :func:`beats_from_grid` produces, unrounded."""
    if bpm <= 0 or duration_ms <= 0:
        return []
    period = 60_000.0 / bpm
    t, _from_anchor = _grid_start(first_beat_ms, period)
    out: list[float] = []
    while t < duration_ms and len(out) < _MAX_BEATS:
        out.append(t)
        t += period
    return out
