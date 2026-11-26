"""Audio I/O helpers.

soundfile (libsndfile) is the fastest decoder available to us and releases
the GIL during read, so worker threads truly run in parallel. The one
thread-safety hazard is libsndfile's bundled mpg123 backend: it selects its
decoder routines into a global function-pointer table during one-time init,
and a second thread opening an MP3 while the first is still filling that
table reads a pointer that is not there yet.

That is not theoretical. A thread pool added to the device export killed the
app inside a minute::

    Thread 12 CRASHED:  sf_open -> mpeg_open -> mpeg_init -> ??? 0x1
    Threads 10,11,13..17:  sf_readf_float -> mpeg_dec_read_f

Eight threads in the MP3 decoder, one jumping to address ``0x1``.

**The guarantee lives in :func:`safe_sf_read`**: the *open* is serialised
behind a lock, the decode is not. Opening is cheap and decoding is the
expensive part, so the parallelism is kept. It is done here rather than left
to callers because the pool that crashed was written by someone who did not
know :func:`warmup` existed — and the next one will be too.

:func:`warmup` remains for callers that want the one-time cost paid up front,
outside a timed section.
"""

from __future__ import annotations

import logging
import threading

import numpy as np

_log = logging.getLogger(__name__)

_warmed_up = False

#: A libsndfile read shorter than this fraction of the frames it announced is
#: treated as a failed read. VBR length estimates are off by a percent or so,
#: never by a factor.
_SHORT_READ = 0.9

#: Held only while a file is being *opened*. See the module docstring: the
#: race is in mpg123's one-time global init, which happens on the first open.
_OPEN_LOCK = threading.Lock()


def warmup(sample_path: str | None = None) -> bool:
    """Trigger libsndfile/mpg123 one-time init from the calling thread.

    Pass an MP3 path from the user's library if you have one; we'll open
    and read a couple of frames to drive ``mpeg_init`` to completion.
    Returns True if the warm-up succeeded.

    Idempotent — safe to call repeatedly; only the first call does work.
    """
    global _warmed_up
    if _warmed_up:
        return True
    if not sample_path:
        return False
    try:
        import soundfile as _sf

        with _sf.SoundFile(sample_path) as f:
            f.read(frames=1024, dtype="float32", always_2d=True)
        _warmed_up = True
        _log.info("audio_io: libsndfile/mpg123 warmed up via %s", sample_path)
        return True
    except Exception as exc:
        _log.warning("audio_io: warm-up failed for %s: %s", sample_path, exc)
        return False


def safe_sf_read(
    path: str,
    *,
    dtype: str = "float32",
    always_2d: bool = True,
    samplerate: int | None = None,
) -> tuple[np.ndarray, int]:
    """Decode an audio file.

    Fast path: soundfile (libsndfile) — releases the GIL during read so
    worker threads parallelise. On any libsndfile error (typically
    malformed MP3 frames, "Unspecified internal error", or unsupported
    codec containers) we transparently retry through PyAV/ffmpeg, which
    is much more lenient with broken streams.

    Returns ``(samples, sample_rate)``. If *samplerate* is given and
    differs from the file's native rate, the audio is mixed down to mono and
    resampled with librosa.
    """
    audio, sr = _read_native(path, dtype=dtype, always_2d=always_2d)
    if samplerate is not None and sr != samplerate:
        mono = audio.mean(axis=1) if audio.ndim == 2 else audio
        mono = resample_mono(mono, sr, samplerate)
        audio = mono.reshape(-1, 1) if always_2d else mono
        sr = samplerate
    return audio, sr


def decode_mono(path: str) -> tuple[np.ndarray, int]:
    """Decode once at the file's own rate and mix down to mono float32.

    For callers that need the same track at several rates: resample the
    result with :func:`resample_mono` per rate instead of decoding again.
    The mix-down is the first step :func:`safe_sf_read` takes before it
    resamples, and the one callers take on its unresampled 2-D output, so
    every buffer derived from this one is identical to what a separate
    ``safe_sf_read(path, samplerate=rate)[0].mean(axis=1)`` returned.

    Only the mono buffer is kept: a 35-minute stereo mix is 740 MB at
    44.1 kHz, and the stereo copy is not needed past this point.
    """
    audio, sr = _read_native(path, dtype="float32", always_2d=True)
    return audio.mean(axis=1), sr


def resample_mono(mono: np.ndarray, sr: int, target: int) -> np.ndarray:
    """Resample a mono buffer with librosa's default resampler.

    Returns *mono* itself when it is already at *target*.
    """
    if sr == target:
        return mono
    import librosa

    return librosa.resample(mono, orig_sr=sr, target_sr=target)


def _read_native(path: str, *, dtype: str, always_2d: bool) -> tuple[np.ndarray, int]:
    """Decode at the native rate: soundfile, falling back to PyAV."""
    import soundfile as _sf

    try:
        # Open under the lock, decode outside it. Concurrent opens race in
        # mpg123's global init; concurrent reads on separate handles do not.
        with _OPEN_LOCK:
            handle = _sf.SoundFile(path)
        with handle:
            audio = handle.read(dtype=dtype, always_2d=always_2d)
            sr = handle.samplerate
            claimed = handle.frames
    except _sf.LibsndfileError as exc:
        _log.debug("soundfile failed on %s (%s) — retrying via PyAV", path, exc)
        audio, sr = _pyav_read(path, always_2d=always_2d)
    else:
        # libsndfile does not always report a bad frame: on one real file it
        # returned 0.39 s of a 445 s track and no error. It does know the
        # length it expected, so a read far short of that is a failed read.
        if claimed > 0 and len(audio) < _SHORT_READ * claimed:
            _log.debug(
                "soundfile read %d of %d frames from %s — retrying via PyAV",
                len(audio),
                claimed,
                path,
            )
            audio, sr = _pyav_read(path, always_2d=always_2d)
    return audio, sr


def _pyav_read(path: str, *, always_2d: bool) -> tuple[np.ndarray, int]:
    """Fallback decoder via PyAV — used only when soundfile errors out."""
    import av
    from av.audio.resampler import AudioResampler

    # Mangled tags (Traktor recordings write non-UTF-8 bytes) must not stop
    # the decode — see core/audio_meta.py.
    container = av.open(path, metadata_errors="replace")
    try:
        stream = container.streams.audio[0]
        sr = stream.rate
        # Decode → mono float32 at the native sample rate (resampling, if any,
        # is done by the caller via librosa to match the soundfile path).
        resampler = AudioResampler(
            format="flt",
            layout="mono",
            rate=sr,
        )
        chunks: list[np.ndarray] = []
        # Tolerate corrupt frames mid-stream: ffmpeg raises InvalidDataError
        # on a bad packet, and aborting the whole read there loses an
        # otherwise-fine track. Each packet is decoded on its own — pulling
        # from one container.decode() generator does not work, because a
        # generator that raised is finished, and the first bad frame ended
        # the read. A consecutive-error cap guards against spinning on a
        # stream that is broken end-to-end.
        skipped = 0
        consecutive = 0
        _MAX_CONSECUTIVE_ERRORS = 256
        for packet in container.demux(stream):
            try:
                frames = packet.decode()
            except Exception:
                skipped += 1
                consecutive += 1
                if consecutive > _MAX_CONSECUTIVE_ERRORS:
                    _log.warning(
                        "PyAV: giving up on %s after %d consecutive bad frames",
                        path,
                        consecutive,
                    )
                    break
                continue
            consecutive = 0
            for frame in frames:
                for out in resampler.resample(frame):
                    chunks.append(out.to_ndarray()[0])
        for out in resampler.resample(None):
            chunks.append(out.to_ndarray()[0])
        if skipped:
            _log.debug("PyAV: recovered %s skipping %d corrupt frame(s)", path, skipped)
    finally:
        container.close()

    if not chunks:
        # Nothing decodable — let the caller treat it as an unreadable file.
        raise RuntimeError(f"PyAV decoded no audio frames from {path}")
    audio = np.concatenate(chunks).astype(np.float32, copy=False)
    if always_2d:
        audio = audio.reshape(-1, 1)
    return audio, sr
