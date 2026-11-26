"""How a track is put together: does the groove loop, does the track travel,
and how many breaks are in it.

Everything is measured in bars, not seconds. A bar at 138 BPM is 1.74 s, so
a "four second" window is two and a third bars — musically meaningless, and
it was what stopped an earlier attempt from matching how a DJ hears a track.
"""

from __future__ import annotations

import numpy as np
import pytest

from core.structure import Structure, analyse_structure

FPS = 10.0  # analysis frames per second, in these fakes
BPM = 120.0  # a bar is exactly 2 s → 20 frames


PER_BAR = int(FPS * 240.0 / BPM)  # 4 beats: 2 s at 120 BPM


def _frames(pattern: list[str], bars_each: int = 1, dims: int = 8) -> np.ndarray:
    """A feature matrix where each entry of *pattern* is a distinct section,
    held for *bars_each* bars.

    Each section is a one-bar figure repeated, not a flat texture: real audio
    always moves within the bar (kick, hats), and a measure built for flat
    input would say nothing about a real track.
    """
    rng = np.random.default_rng(0)
    # dict.fromkeys, not set: a set of strings iterates in an order that
    # depends on per-process hash randomisation, so the figures came off the
    # seeded RNG in a different order each run and "deterministic" fixture
    # data was nothing of the sort. It failed about one run in eight —
    # PYTHONHASHSEED=3 reproduces the old behaviour exactly.
    figures = {p: rng.standard_normal((dims, PER_BAR)) for p in dict.fromkeys(pattern)}
    cols = []
    for p in pattern:
        block = np.tile(figures[p], bars_each)
        cols.append(block + rng.standard_normal(block.shape) * 0.05)
    return np.hstack(cols)


def _analyse(m: np.ndarray) -> Structure:
    return analyse_structure(m, bpm=BPM, fps=FPS)


# ── Looping ─────────────────────────────────────────────────────────────────


def test_a_one_bar_loop_repeats_at_one_bar() -> None:
    s = _analyse(_frames(["a"] * 64))
    assert s.loop_1 > 0.9
    assert s.loop_4 > 0.9


def test_alternating_bars_repeat_at_two_not_one() -> None:
    s = _analyse(_frames(["a", "b"] * 32))
    assert s.loop_4 > s.loop_1


# ── Sameness across the whole track ─────────────────────────────────────────


def test_a_track_that_never_changes_is_the_same_at_32_bars() -> None:
    s = _analyse(_frames(["a"] * 128))
    assert s.sameness > 0.9


def test_a_track_that_goes_somewhere_is_not() -> None:
    """Four different sections of 32 bars each — nothing at the end sounds
    like the beginning."""
    s = _analyse(_frames(["a", "b", "c", "d"], bars_each=32))
    assert s.sameness < 0.5


def test_evolution_grows_with_the_number_of_sections() -> None:
    still = _analyse(_frames(["a"] * 128))
    moving = _analyse(_frames(["a", "b", "c", "d"], bars_each=32))
    assert moving.evolution > still.evolution * 3


# ── Breaks and drops ────────────────────────────────────────────────────────


def test_a_flat_track_has_no_boundaries() -> None:
    assert _analyse(_frames(["a"] * 128)).sections_per_min == pytest.approx(0.0, abs=0.3)


def test_each_section_change_is_found() -> None:
    """Six 16-bar sections in a row: five boundaries, over 96 bars — at 120
    BPM that is 3.2 minutes, so about 1.5 a minute."""
    s = _analyse(_frames(["a", "b", "c", "d", "e", "f"], bars_each=16))
    assert 1.0 <= s.sections_per_min <= 2.5


def test_a_short_clip_is_not_an_error() -> None:
    s = _analyse(_frames(["a"], bars_each=1))
    assert s.sections_per_min == 0.0
    assert 0.0 <= s.sameness <= 1.0


def test_no_bpm_means_no_answer() -> None:
    assert analyse_structure(_frames(["a"] * 32), bpm=0.0, fps=FPS) is None


def test_silence_is_not_an_error() -> None:
    s = analyse_structure(np.zeros((8, 200)), bpm=BPM, fps=FPS)
    assert s is not None


# ── How busy the percussion is ──────────────────────────────────────────────


def test_onset_density_counts_events_per_bar() -> None:
    """Danceability measures how *periodic* the onsets are — four kicks a bar
    and sixteen hats a bar both score high. This counts them."""
    from core.structure import onsets_per_bar

    # one onset every quarter of a bar, over 8 bars, at 10 fps and 2 s/bar
    onsets = [int(i * 0.25 * 20) for i in range(32)]
    assert onsets_per_bar(onsets, n_frames=8 * 20, bpm=BPM, fps=FPS) == pytest.approx(4.0)


def test_a_sparse_track_scores_lower_than_a_busy_one() -> None:
    from core.structure import onsets_per_bar

    sparse = onsets_per_bar([0, 20, 40, 60], n_frames=160, bpm=BPM, fps=FPS)
    busy = onsets_per_bar(list(range(0, 160, 5)), n_frames=160, bpm=BPM, fps=FPS)
    assert busy > sparse * 3


def test_no_onsets_is_zero_not_an_error() -> None:
    from core.structure import onsets_per_bar

    assert onsets_per_bar([], n_frames=100, bpm=BPM, fps=FPS) == 0.0


def test_no_tempo_means_no_answer() -> None:
    from core.structure import onsets_per_bar

    assert onsets_per_bar([1, 2], n_frames=100, bpm=0.0, fps=FPS) is None
