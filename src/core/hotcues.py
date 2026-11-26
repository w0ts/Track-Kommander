"""Automatic hotcue detection — two-pass bar-accurate approach.

Algorithm
---------
1. Load audio at 11 kHz mono.
2. Build an exact beat grid from Traktor's BPM + TYPE=4 grid-marker phase.
3. Average sub-bass (30–150 Hz) energy into **one value per bar**.
4. **Coarse pass** — smooth the bar-energy array over ``_SMOOTH_BARS`` bars
   and differentiate.  Heavy smoothing keeps false-positive rate low.
   ``find_peaks`` on the derivative identifies that a transition *exists* in
   a region, but the peak position is biased toward the middle of the slope.
5. **Refine pass** — for each coarse position, walk *backward* bar-by-bar in
   the unsmoothed bar-energy array to find the first bar where the new energy
   level is actually established.  This gives bar-accurate placement.
6. Return positions as milliseconds directly from the beat grid — no snap
   function needed because we already work at bar resolution.

Traktor CUE_V2 TYPE values
--------------------------
0 = regular hotcue  (jump point)
1 = fade-in         (automix: start fading in)
2 = fade-out        (automix: start fading out / mix-out point)
3 = load cue        (where the deck positions on load)
4 = grid marker     (beat-grid anchor — managed by Traktor)
5 = loop
"""

from __future__ import annotations

import logging

import numpy as np

log = logging.getLogger("hotcues")

_SR = 11025  # analysis sample rate
_HOP = 512  # STFT hop length
_MIN_GAP_BARS = 16  # minimum bars between cues
_SMOOTH_BARS = 4  # coarse smoothing window — ≤ shortest transition (4 bars)
_PROMINENCE = 0.15  # minimum prominence on the smoothed derivative


def _build_beat_grid(duration_s: float, bpm: float, beat_phase_ms: float) -> np.ndarray:
    """Return beat timestamps (seconds) aligned to Traktor's beat grid."""
    beat_period = 60.0 / bpm
    phase_s = beat_phase_ms / 1000.0
    n_back = int(phase_s / beat_period)
    start = phase_s - n_back * beat_period
    return np.arange(start, duration_s + beat_period, beat_period)


def _compute_bar_energy(sub_energy: np.ndarray, beats: np.ndarray) -> np.ndarray:
    """Average sub-bass energy into one value per bar using the beat grid."""
    n_bars = (len(beats) - 1) // 4
    if n_bars < 2:
        return np.array([])
    energies = np.empty(n_bars)
    for b in range(n_bars):
        f0 = int(beats[b * 4] * _SR / _HOP)
        f1 = int(beats[b * 4 + 4] * _SR / _HOP) if (b * 4 + 4) < len(beats) else len(sub_energy)
        f0 = max(0, min(f0, len(sub_energy) - 1))
        f1 = max(f0 + 1, min(f1, len(sub_energy)))
        energies[b] = float(np.mean(sub_energy[f0:f1]))
    return energies / (np.max(energies) + 1e-8)


def _refine_bar(coarse_bar: int, bar_nrg: np.ndarray, direction: int, search_bars: int) -> int:
    """Walk backward from *coarse_bar* to find where the energy change begins.

    *direction* = +1 for drops (energy rises), -1 for breaks (energy falls).

    The coarse derivative peak sits roughly in the middle of the slope.
    We walk backward until the energy is no longer clearly on the "new" side,
    i.e. until it falls below the midpoint between the before and after levels.
    """
    half = search_bars // 2
    after_start = coarse_bar
    after_end = min(len(bar_nrg), coarse_bar + half)
    before_start = max(0, coarse_bar - search_bars)
    before_end = coarse_bar

    if after_end <= after_start or before_end <= before_start:
        return coarse_bar

    after_mean = float(np.mean(bar_nrg[after_start:after_end]))
    before_mean = float(np.mean(bar_nrg[before_start:before_end]))
    threshold = (before_mean + after_mean) / 2.0

    # Walk backward from coarse_bar to find first bar at or above threshold
    for bar in range(coarse_bar, max(0, coarse_bar - search_bars) - 1, -1):
        val = bar_nrg[bar]
        if direction == 1 and val < threshold:
            return bar + 1  # first bar clearly on the "high" side
        if direction == -1 and val > threshold:
            return bar + 1  # first bar clearly on the "low" side
    return coarse_bar


def detect_drop_positions(
    path: str,
    bpm: float,
    beat_phase_ms: float = 0.0,
    max_cues: int = 7,
    why: list[str] | None = None,
) -> list[tuple[float, str, int]]:
    """Return up to *max_cues* significant positions as ``(ms, label, cue_type)``.

    ``cue_type`` Traktor TYPE integers:  0 = hotcue (Drop),  2 = fade-out (Break).

    When nothing is returned, the reason is appended to *why*, so a run can
    say whether tracks had no drops or could not be read — the second is a
    failure that would otherwise look like the first.

    Raises ``ValueError`` if *bpm* ≤ 0.
    """
    import librosa
    from scipy.ndimage import uniform_filter1d
    from scipy.signal import find_peaks

    if bpm <= 0:
        raise ValueError(f"BPM must be positive, got {bpm!r}")

    bar_sec = (60.0 / bpm) * 4

    # Through the shared decoder, not librosa.load: that reads via
    # libsndfile alone, which hands back a fraction of a damaged MP3 without
    # complaint — and a 0.4 s clip of a 7 min track has no drops in it.
    from core.audio_io import safe_sf_read

    data, sr = safe_sf_read(path, dtype="float32", always_2d=True, samplerate=_SR)
    y = data.mean(axis=1).astype(np.float32)
    duration = len(y) / sr
    if duration < 20:
        if why is not None:
            why.append(f"decoded under 20 s ({duration:.1f} s)")
        return []

    # ── Beat grid ────────────────────────────────────────────────────────
    beats = _build_beat_grid(duration, bpm, beat_phase_ms)

    # ── Sub-bass energy per frame ─────────────────────────────────────────
    D = np.abs(librosa.stft(y, n_fft=1024, hop_length=_HOP))
    freqs = librosa.fft_frequencies(sr=sr, n_fft=1024)
    sub_energy = np.mean(D[(freqs >= 30) & (freqs <= 150), :], axis=0)

    # ── Bar-level energy ──────────────────────────────────────────────────
    bar_nrg = _compute_bar_energy(sub_energy, beats)
    if len(bar_nrg) < _MIN_GAP_BARS * 2:
        if why is not None:
            why.append("too few bars on the grid")
        return []

    # ── Coarse pass: smooth + derivative → find transition regions ────────
    bar_smooth = uniform_filter1d(bar_nrg.astype(float), size=_SMOOTH_BARS)
    deriv = np.diff(bar_smooth)
    deriv_norm = deriv / (np.max(np.abs(deriv)) + 1e-8)

    drop_peaks, drop_props = find_peaks(deriv_norm, distance=_MIN_GAP_BARS, prominence=_PROMINENCE)
    break_peaks, break_props = find_peaks(
        -deriv_norm, distance=_MIN_GAP_BARS, prominence=_PROMINENCE
    )

    # ── Refine pass: walk back to find exact onset bar ────────────────────
    candidates: list[tuple[float, str, int, float]] = []

    for i, n in enumerate(drop_peaks):
        bar = _refine_bar(int(n) + 1, bar_nrg, direction=+1, search_bars=_SMOOTH_BARS)
        beat_idx = min(bar * 4, len(beats) - 1)
        ms = beats[beat_idx] * 1000.0
        if bar_sec * 2 < ms / 1000.0 < duration - 10.0:
            candidates.append((ms, "tk: Drop", 0, float(drop_props["prominences"][i])))

    for i, n in enumerate(break_peaks):
        bar = _refine_bar(int(n) + 1, bar_nrg, direction=-1, search_bars=_SMOOTH_BARS)
        beat_idx = min(bar * 4, len(beats) - 1)
        ms = beats[beat_idx] * 1000.0
        if bar_sec * 2 < ms / 1000.0 < duration - 10.0:
            score = float(break_props["prominences"][i]) * 0.8
            candidates.append((ms, "tk: Break", 2, score))

    if not candidates:
        if why is not None:
            why.append("no drop or break found")
        return []

    # ── Deduplicate within 4 bars, keep best score ────────────────────────
    candidates.sort(key=lambda c: c[0])
    gap_ms = bar_sec * 4 * 1000.0
    deduped: list[tuple[float, str, int, float]] = []
    for ms, label, cue_type, score in candidates:
        if not deduped or (ms - deduped[-1][0]) >= gap_ms:
            deduped.append((ms, label, cue_type, score))
        elif score > deduped[-1][3]:
            deduped[-1] = (ms, label, cue_type, score)

    if len(deduped) <= max_cues:
        deduped.sort(key=lambda c: c[0])
        log.debug("detected %d cues for %s (bpm=%.1f)", len(deduped), path, bpm)
        return [(ms, label, cue_type) for ms, label, cue_type, _ in deduped]

    # ── More candidates than slots: prioritise Break+Drop pairs ──────────
    # A pair (Break → Drop within 4–64 bars) scores by combined prominence.
    _MAX_PAIR_MS = bar_sec * 64 * 1000.0
    _MIN_PAIR_MS = bar_sec * 4 * 1000.0

    breaks = [(ms, label, ct, sc) for ms, label, ct, sc in deduped if label == "tk: Break"]
    drops = [(ms, label, ct, sc) for ms, label, ct, sc in deduped if label == "tk: Drop"]

    pairs: list[tuple[float, tuple, tuple]] = []  # (combined_score, break_cue, drop_cue)
    paired_ms: set[float] = set()

    for brk in breaks:
        for drp in drops:
            gap = drp[0] - brk[0]
            if _MIN_PAIR_MS <= gap <= _MAX_PAIR_MS:
                pairs.append((brk[3] + drp[3], brk, drp))
                break  # take the nearest qualifying drop for this break

    pairs.sort(key=lambda p: -p[0])  # strongest pair first

    selected: list[tuple[float, str, int, float]] = []
    for _, brk, drp in pairs:
        if len(selected) + 2 > max_cues:
            break
        if brk[0] not in paired_ms and drp[0] not in paired_ms:
            selected.extend([brk, drp])
            paired_ms.add(brk[0])
            paired_ms.add(drp[0])

    # Fill remaining slots with highest-scoring unpaired candidates
    remaining = max_cues - len(selected)
    if remaining > 0:
        unpaired = sorted(
            [c for c in deduped if c[0] not in paired_ms],
            key=lambda c: -c[3],
        )
        selected.extend(unpaired[:remaining])

    selected.sort(key=lambda c: c[0])
    log.debug("detected %d cues (%d pairs) for %s (bpm=%.1f)", len(selected), len(pairs), path, bpm)
    return [(ms, label, cue_type) for ms, label, cue_type, _ in selected]
