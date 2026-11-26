"""Remember a track's computed waveform so an export never decodes it twice.

Decoding audio is the largest single cost of a device export — about 0.75 s a
track, against 5.4 s to copy a whole 220 MB of it (see
:mod:`workers.waveforms`). Until now the only cache was the ANLZ files already
written to *a* key: :func:`core.device_export.export` reads them back when
only a track's cues changed. That helps on the stick that has them and nowhere
else, so a second stick, or a rebuild rather than a merge, re-analysed the
whole library.

This keeps the result in our own database instead, next to the CLAP embeddings
and the rest of the per-track analysis, so it holds across sticks and across
rebuilds.

Measured over a real 872-track collection, not synthetic files — every one
analysed and stored, none failed:

======================  ==========================
Analyse from audio      105 ms/track wall, 8 workers
Read back from here     2.2 ms — **~48× cheaper**
Store                   52 ms
Row                     52 KiB
======================  ==========================

In bulk, which is what an export does: the whole library analysed in 91 s and
read back in 1.9 s, for 46 MB of blobs — the database went from 4.3 MB to
50 MB. A 2,000-track library should therefore expect to pay ~105 MB to turn
three and a half minutes of decoding into four seconds of reading.

Earlier figures here came from synthetic audio and were optimistic: rows are
~1.7× larger and a store ~4× slower against real files. The saving is still
the point of the module, but the disk cost is not small — a library much
past a few thousand tracks is worth measuring again before assuming it fits.

The audio files are not touched. Embedding this in a track's own tags would
make the cache travel between machines, which is the one thing this cannot do
— but it would mean rewriting every file in the library, changing every mtime
(which is exactly what :func:`core.device_export.audio_fingerprint` watches),
and trusting a tag writer not to lose the data Traktor keeps in those same
files. Not worth it for a cache.

Is the cached waveform still the right one?
-------------------------------------------

A row is keyed by path and carries the file's ``size:mtime_ns`` — the same
fingerprint the export already trusts to decide two files are the same one —
so an edited or replaced file misses. It also carries
:data:`core.anlz.WAVEFORM_VERSION`, so improving the analysis invalidates every
row at a stroke.

What it does not catch: a file replaced with different audio of exactly the
same size whose mtime was preserved. That is what the force-re-analyse option
in the export dialog is for.

Rows follow the tracks table — :func:`core.db.remove_track` and friends prune
them alongside the embeddings — so a library that shrinks does not leave the
cache growing behind it.
"""

from __future__ import annotations

import logging
import zlib
from pathlib import Path

from core import anlz, db

log = logging.getLogger(__name__)


def fingerprint(source: str | Path) -> str:
    """Identifies the audio file itself — size and modification time.

    Deliberately the same shape as
    :func:`core.device_export.audio_fingerprint`, and for the same reason:
    hashing hundreds of megabytes of audio costs more than the decode this is
    meant to avoid.
    """
    try:
        st = Path(source).stat()
    except OSError:
        return "missing"
    return f"{st.st_size}:{st.st_mtime_ns}"


def encode(waveform: anlz.Waveform) -> bytes:
    """A waveform as one compressed blob.

    Byte-aligned rather than bit-packed, even though heights need five bits
    and colour channels three: deflate finds far more in a stream of repeated
    whole bytes than in one where every value straddles a boundary, so the
    packed-tighter version is not the smaller one.
    """
    preview = bytes(min(255, max(0, h)) for h in waveform.preview)
    detail = bytes(min(255, max(0, h)) for h in waveform.detail)
    colour = bytes(min(255, max(0, c)) for triple in waveform.colour for c in triple)
    return zlib.compress(preview + detail + colour, 6)


def decode(blob: bytes, n_preview: int, n_detail: int) -> anlz.Waveform | None:
    """The waveform back out of :func:`encode`, or ``None`` if the row is not
    usable.

    Never raises: a corrupt row is a cache miss, not an error the user should
    have to see.
    """
    try:
        body = zlib.decompress(blob)
    except zlib.error:
        log.debug("waveform cache: corrupt row", exc_info=True)
        return None
    if len(body) != n_preview + n_detail * 4:
        return None

    flat = body[n_preview + n_detail :]
    return anlz.Waveform(
        preview=tuple(body[:n_preview]),
        detail=tuple(body[n_preview : n_preview + n_detail]),
        # Three strided slices zipped, not a loop indexing one buffer: the
        # whole of this runs in C rather than the interpreter, which is 3×
        # faster on the 45,000 triples a five-minute track has. That matters
        # because an export decodes the whole selection at once, on the UI
        # thread.
        colour=tuple(zip(flat[0::3], flat[1::3], flat[2::3], strict=True)),
    )


def load(source: str | Path) -> anlz.Waveform | None:
    """The cached waveform for one track, or ``None`` for any miss."""
    return load_many([source]).get(str(source))


def load_many(sources: list[str | Path]) -> dict[str, anlz.Waveform]:
    """Cached waveforms for many tracks at once, by path as given.

    One query rather than one per track: an export looks up the whole
    selection, and a round trip each would undo the point of the cache.
    Paths whose row is absent, stale or unreadable are simply not in the
    result — every miss means the same thing to the caller.
    """
    if not sources:
        return {}
    wanted = {str(s): fingerprint(s) for s in sources}
    try:
        rows = db.get_waveforms(list(wanted), anlz.WAVEFORM_VERSION)
    except Exception:
        # A locked or missing database costs a decode, not a correct stick.
        log.warning("waveform cache: cannot read", exc_info=True)
        return {}

    out: dict[str, anlz.Waveform] = {}
    for path, (stored, n_preview, n_detail, blob) in rows.items():
        if stored != wanted.get(path):
            continue  # the file changed under the row
        waveform = decode(blob, n_preview, n_detail)
        if waveform is not None:
            out[path] = waveform
    return out


def store(source: str | Path, waveform: anlz.Waveform) -> None:
    """Remember *waveform* for *source*, replacing any earlier row.

    Failure is logged and swallowed: a cache that cannot be written is slow,
    not broken, and must never sink an export that otherwise worked.
    """
    path = str(source)
    try:
        db.upsert_waveform(
            path,
            fingerprint(path),
            anlz.WAVEFORM_VERSION,
            len(waveform.preview),
            len(waveform.detail),
            encode(waveform),
        )
    except Exception:
        log.warning("waveform cache: cannot store %s", path, exc_info=True)
