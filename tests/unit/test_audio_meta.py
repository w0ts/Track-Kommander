"""Duration and cover art read through PyAV — the replacement for mutagen.

mutagen is GPL-2.0-or-later and was the only dependency forcing the whole
binary under the GPL. Both of its jobs (a duration without a decode, the
embedded cover) are metadata FFmpeg already parses, so PyAV — shipped anyway
for decoding — takes them over.

The fixtures are real encoded audio with the tag blocks written by hand, so
the tests prove what FFmpeg sees in the bytes a tagger would write, without
depending on a tagging library.
"""

from __future__ import annotations

import struct
from pathlib import Path

import numpy as np
import pytest

from core.audio_meta import read_cover_art, read_duration

soundfile = pytest.importorskip("soundfile")
pytest.importorskip("av")

SR = 44100
SECONDS = 3.0

#: A valid 1×1 PNG. Cover art is compared byte for byte, so any image will do.
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d49444154789c6360f8cfc0f01f0005000201e7a9a2d20000000049454e"
    "44ae426082"
)


def _tone() -> np.ndarray:
    t = np.arange(int(SR * SECONDS)) / SR
    return (0.2 * np.sin(2 * np.pi * 440 * t)).astype("float32")


def _syncsafe(n: int) -> bytes:
    return bytes([(n >> 21) & 0x7F, (n >> 14) & 0x7F, (n >> 7) & 0x7F, n & 0x7F])


def _id3v23(frames: list[tuple[bytes, bytes]]) -> bytes:
    body = b"".join(fid + struct.pack(">I", len(data)) + b"\x00\x00" + data for fid, data in frames)
    return b"ID3\x03\x00\x00" + _syncsafe(len(body)) + body


def _apic(image: bytes) -> tuple[bytes, bytes]:
    # encoding latin-1, MIME, picture type 3 (front cover), empty description
    return b"APIC", b"\x00image/png\x00\x03\x00" + image


def _flac_picture_block(image: bytes, *, last: bool) -> bytes:
    mime = b"image/png"
    body = (
        struct.pack(">II", 3, len(mime))
        + mime
        + struct.pack(">I", 0)
        + struct.pack(">IIIII", 1, 1, 32, 0, len(image))
        + image
    )
    return bytes([(0x80 if last else 0) | 6]) + len(body).to_bytes(3, "big") + body


def _add_flac_picture(flac: bytes, image: bytes) -> bytes:
    """Insert a PICTURE block as the new last metadata block."""
    assert flac[:4] == b"fLaC"
    pos, blocks = 4, []
    while True:
        header = flac[pos]
        size = int.from_bytes(flac[pos + 1 : pos + 4], "big")
        blocks.append(bytes([header & 0x7F]) + flac[pos + 1 : pos + 4 + size])
        pos += 4 + size
        if header & 0x80:
            break
    return b"fLaC" + b"".join(blocks) + _flac_picture_block(image, last=True) + flac[pos:]


@pytest.fixture(scope="module")
def audio_dir(tmp_path_factory) -> Path:
    return tmp_path_factory.mktemp("meta")


@pytest.fixture(scope="module")
def plain_mp3(audio_dir) -> Path:
    if "MP3" not in soundfile.available_formats():
        pytest.skip("libsndfile has no MP3 support")
    p = audio_dir / "plain.mp3"
    soundfile.write(p, _tone(), SR, format="MP3")
    return p


@pytest.fixture(scope="module")
def mp3_with_art(audio_dir, plain_mp3) -> Path:
    p = audio_dir / "art.mp3"
    p.write_bytes(_id3v23([_apic(PNG)]) + plain_mp3.read_bytes())
    return p


@pytest.fixture(scope="module")
def mp3_bad_tag_encoding(audio_dir, plain_mp3) -> Path:
    """A title declared UTF-8 but holding Latin-1 bytes — as some taggers
    and Traktor's own recordings write. PyAV raises on these by default."""
    p = audio_dir / "badtag.mp3"
    title = (b"TIT2", b"\x03Caf\xe9 \xbb Night")
    p.write_bytes(_id3v23([title, _apic(PNG)]) + plain_mp3.read_bytes())
    return p


@pytest.fixture(scope="module")
def flac_with_art(audio_dir) -> Path:
    plain = audio_dir / "plain.flac"
    soundfile.write(plain, _tone(), SR, format="FLAC")
    p = audio_dir / "art.flac"
    p.write_bytes(_add_flac_picture(plain.read_bytes(), PNG))
    return p


@pytest.fixture(scope="module")
def wav(audio_dir) -> Path:
    p = audio_dir / "plain.wav"
    soundfile.write(p, _tone(), SR, format="WAV")
    return p


# ── duration ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("name", ["plain_mp3", "flac_with_art", "wav"])
def test_duration_matches_the_audio(name, request) -> None:
    path = request.getfixturevalue(name)
    assert read_duration(str(path)) == pytest.approx(SECONDS, abs=0.1)


def test_duration_survives_a_tag_that_is_not_valid_utf8(mp3_bad_tag_encoding) -> None:
    assert read_duration(str(mp3_bad_tag_encoding)) == pytest.approx(SECONDS, abs=0.1)


def test_duration_of_an_unreadable_file_is_zero(tmp_path) -> None:
    """0.0 is the caller's cue to take the length from the decoded buffer."""
    junk = tmp_path / "junk.mp3"
    junk.write_bytes(b"not audio at all" * 64)
    assert read_duration(str(junk)) == 0.0
    assert read_duration(str(tmp_path / "missing.flac")) == 0.0


# ── cover art ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("name", ["mp3_with_art", "flac_with_art", "mp3_bad_tag_encoding"])
def test_cover_art_is_the_embedded_image(name, request) -> None:
    path = request.getfixturevalue(name)
    assert read_cover_art(str(path)) == PNG


@pytest.mark.parametrize("name", ["plain_mp3", "wav"])
def test_no_cover_art_is_none(name, request) -> None:
    assert read_cover_art(str(request.getfixturevalue(name))) is None


def test_cover_art_of_an_unreadable_file_is_none(tmp_path) -> None:
    assert read_cover_art(str(tmp_path / "missing.m4a")) is None


def test_the_pyav_fallback_decoder_survives_the_same_tag(mp3_bad_tag_encoding) -> None:
    """core.audio_io._pyav_read opened files with strict tag decoding and
    lost the whole track to a mangled title (seen on Traktor WAV recordings)."""
    from core.audio_io import _pyav_read

    audio, sr = _pyav_read(str(mp3_bad_tag_encoding), always_2d=False)
    assert len(audio) / sr == pytest.approx(SECONDS, abs=0.1)
