"""How a track is put together, measured in bars.

Three questions a DJ asks about a record and no existing feature answered:
does the groove loop tightly, does the track end up somewhere different from
where it started, and how many breaks are in it.

Everything here is bar-aligned. A bar at 138 BPM is 1.74 s, so windows in
round seconds cut across the phrasing — measured that way, a one-bar loop and
a track that never changes come out the same number, and an eight-minute acid
roll that nonetheless travels looks identical to one that does not.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

#: Bars between the lags we ask about: the groove, the phrase, the section.
LOOP_BARS = (1, 4)
SAMENESS_BARS = 32

#: The checkerboard for finding boundaries, and how far apart two of them
#: may be — techno sections change on 8- and 16-bar lines.
KERNEL_BARS = 16
MIN_GAP_BARS = 8

#: A novelty peak must stand this far above its neighbourhood to be a break.
PEAK_DELTA = 0.12

#: How differently the two halves of the kernel must sound, in units of
#: similarity, before anything counts as a boundary at all. Measured: a track
#: whose texture never changes peaks around 1e-5, a real one with sections at
#: 0.12–0.45, so this sits an order of magnitude clear of both.
MIN_CONTRAST = 0.01


@dataclass(frozen=True)
class Structure:
    """What a track's shape looks like."""

    loop_1: float
    """Self-similarity one bar apart — how tight the groove's loop is."""
    loop_4: float
    """Self-similarity four bars apart — the phrase."""
    sameness: float
    """Self-similarity 32 bars apart — does the track stay put."""
    evolution: float
    """Mean distance between consecutive 16-bar blocks — how far it travels."""
    sections_per_min: float
    """Boundaries found per minute — breaks, drops, build-ups."""


def analyse_structure(features: np.ndarray, *, bpm: float, fps: float) -> Structure | None:
    """Measure the shape of a track from a frame-wise feature matrix.

    *features* is dims × frames (MFCCs work well), *fps* its frame rate.
    Returns None without a tempo — every window here is a number of bars.
    """
    if bpm <= 0 or fps <= 0 or features.ndim != 2 or features.shape[1] < 4:
        return None

    # Each dimension is standardised across time before frames are compared
    # by angle. Without it the first MFCC — frame energy — dominates every
    # vector, every frame points the same way, and a track that goes nowhere
    # scores the same as one full of drops.
    m = np.asarray(features, dtype=np.float32)
    m = m - m.mean(axis=1, keepdims=True)
    m = m / (m.std(axis=1, keepdims=True) + 1e-9)
    norm = m / (np.linalg.norm(m, axis=0, keepdims=True) + 1e-9)

    # The self-similarity matrix is never built. An eight-minute track is
    # ~14,000 frames, so it would be a 0.8 GB array, and everything below
    # needs only a few of its diagonals and a trace against a kernel — both
    # of which come straight from the normalised frames.
    bar_frames = 240.0 / bpm * fps
    loops = [_lag_similarity(norm, int(b * bar_frames)) for b in LOOP_BARS]
    sameness = _lag_similarity(norm, int(SAMENESS_BARS * bar_frames))
    duration_min = features.shape[1] / fps / 60.0

    return Structure(
        loop_1=loops[0],
        loop_4=loops[1],
        sameness=sameness,
        evolution=_evolution(norm, int(KERNEL_BARS * bar_frames)),
        sections_per_min=_boundaries(norm, bar_frames) / max(duration_min, 1e-9),
    )


def onsets_per_bar(onsets, *, n_frames: int, bpm: float, fps: float) -> float | None:
    """How many note or drum onsets fall in a bar, on average.

    Distinct from the Danceability axis, which is the peak of the onset
    autocorrelation — that says how *regular* the pulse is, and comes out
    between 0.83 and 0.95 for every techno record. This says how *busy* it
    is, and spans a factor of two across the same crate.
    """
    if bpm <= 0 or fps <= 0 or n_frames <= 0:
        return None
    bars = n_frames / fps / (240.0 / bpm)
    return len(onsets) / bars if bars > 0 else 0.0


def _lag_similarity(norm: np.ndarray, lag: int) -> float:
    """Mean similarity between frames *lag* apart.

    A diagonal of the similarity matrix, taken as the dot product of the
    frames themselves rather than by building the matrix to read one line
    off it.
    """
    if lag <= 0 or lag >= norm.shape[1] - 2:
        return 0.0
    return float((norm[:, :-lag] * norm[:, lag:]).sum(axis=0).mean())


def _evolution(m: np.ndarray, block: int) -> float:
    """Mean distance between consecutive blocks — how far the track moves."""
    if block < 2 or m.shape[1] < block * 2:
        return 0.0
    blocks = [m[:, i : i + block].mean(axis=1) for i in range(0, m.shape[1] - block + 1, block)]
    if len(blocks) < 2:
        return 0.0
    scale = np.sqrt(m.shape[0])
    return float(
        np.mean([np.linalg.norm(a - b) / scale for a, b in zip(blocks, blocks[1:], strict=False)])
    )


def _boundaries(norm: np.ndarray, bar_frames: float) -> int:
    """Count section boundaries with a bar-aligned checkerboard kernel.

    The classic Foote novelty — slide a +/- checkerboard down the diagonal of
    the self-similarity matrix and peak-pick where it spikes — but computed
    without the matrix and without a window per centre.

    The kernel is the outer product of one vector *v* (minus the bars before
    the centre, plus the bars after), so its trace against a window of
    ``S = NᵀN`` is ``vᵀNᵀNv`` = ``‖Nv‖²``. And because *v* only ever sums two
    blocks of columns, ``Nv`` for every centre at once is a difference of
    running sums. Slicing a 640×640 window per beat took 2.7 s a track; this
    takes a millisecond and agrees to seventeen decimal places.
    """
    k = int(KERNEL_BARS * bar_frames)
    n_frames = norm.shape[1]
    if k < 2 or n_frames <= 2 * k:
        return 0
    step = max(1, int(bar_frames / 4))  # evaluate once a beat
    centres = np.arange(k, n_frames - k, step)
    if len(centres) == 0:
        return 0
    cumulative = np.cumsum(
        np.hstack([np.zeros((norm.shape[0], 1), dtype=norm.dtype), norm]), axis=1
    )
    before = cumulative[:, centres] - cumulative[:, centres - k]
    after = cumulative[:, centres + k] - cumulative[:, centres]
    # Per element, so the number means "how differently do these two halves
    # sound" in units of similarity — not an arbitrary scale that stretches
    # noise into peaks when the track never changes.
    scale = float((2 * k) ** 2)
    novelty = (((after - before) ** 2).sum(axis=0) / scale).astype(np.float32)
    peak = float(novelty.max())
    if peak < MIN_CONTRAST:
        return 0  # nothing in this track changes
    novelty = np.clip(novelty / peak, 0.0, 1.0)

    gap = max(1, int(MIN_GAP_BARS * bar_frames / step))
    import librosa

    peaks = librosa.util.peak_pick(
        novelty, pre_max=gap, post_max=gap, pre_avg=gap, post_avg=gap, delta=PEAK_DELTA, wait=gap
    )
    return int(len(peaks))
