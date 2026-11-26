"""Track metadata read without decoding the audio: duration and cover art.

Both come from FFmpeg's demuxer via PyAV, which is shipped anyway for
:mod:`core.audio_io`'s fallback decoder. This replaced mutagen, whose
GPL-2.0-or-later licence was the only thing binding the distributed binary to
the GPL (see THIRD_PARTY.md). It also reads more than the old code did: the
cover of an M4A (``covr``) or an Ogg Vorbis file (``METADATA_BLOCK_PICTURE``)
and the length of a WAV, all of which the mutagen path missed.

FFmpeg exposes an embedded cover — ID3 ``APIC``, a FLAC ``PICTURE`` block,
MP4 ``covr``, Vorbis comments — as a video stream flagged ``attached_pic``,
whose single packet is the image file's bytes.

Tags are decoded with ``metadata_errors="replace"``: PyAV raises on the
first tag that is not valid UTF-8, which would lose the duration and cover
of a file over a mangled title.
"""

from __future__ import annotations

import logging

_log = logging.getLogger(__name__)


def _open(path: str):
    import av

    return av.open(path, metadata_errors="replace")


def read_duration(path: str) -> float:
    """Length in seconds from the container header, or 0.0 if unknown.

    0.0 is the caller's cue to take the length from the decoded buffer.
    """
    try:
        import av

        with _open(path) as container:
            if container.duration:
                return container.duration / av.time_base
            stream = container.streams.audio[0] if container.streams.audio else None
            if stream is not None and stream.duration and stream.time_base:
                return float(stream.duration * stream.time_base)
    except Exception as exc:
        _log.debug("duration unreadable for %s: %s", path, exc)
    return 0.0


def read_cover_art(path: str) -> bytes | None:
    """The embedded cover image's file bytes (JPEG/PNG), or None."""
    try:
        from av.stream import Disposition

        with _open(path) as container:
            for stream in container.streams.video:
                if not stream.disposition & Disposition.attached_pic:
                    continue
                for packet in container.demux(stream):
                    data = bytes(packet)
                    if data:
                        return data
    except Exception as exc:
        _log.debug("cover art unreadable for %s: %s", path, exc)
    return None
