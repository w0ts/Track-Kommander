"""What the bassline does: how busy it is, how far it travels, and whether
it leans major or minor.

Measured on the beat grid, one note per sixteenth, on the sustained
(harmonic) part of the low end so the kick's transient is not a note.
"""

from __future__ import annotations

import numpy as np
import pytest

from core.bassline import BassLine, analyse_bassline, notes_from_cqt

SR = 22050


def _cqt(
    sequence: list[int | None], slots_per_note: int = 4, bins: int = 36, frames_per_slot: int = 5
) -> np.ndarray:
    """A fake CQT whose loudest bin follows *sequence* (None = silence)."""
    frames = len(sequence) * slots_per_note * frames_per_slot
    c = np.full((bins, frames), 0.001, dtype="float32")
    f = 0
    for note in sequence:
        for _ in range(slots_per_note * frames_per_slot):
            if note is not None:
                c[note, f] = 1.0
            f += 1
    return c


# ── Turning a CQT into notes ────────────────────────────────────────────────


def test_one_note_per_sixteenth() -> None:
    c = _cqt([12, 12, 14, 14])
    notes = notes_from_cqt(c, frames_per_slot=5 * 4)
    assert notes == [12, 12, 14, 14]


def test_silence_is_a_rest_not_a_note() -> None:
    c = _cqt([12, None, 12, None])
    notes = notes_from_cqt(c, frames_per_slot=5 * 4)
    assert notes[1] is None and notes[3] is None


def test_frame_jitter_does_not_become_notes() -> None:
    """The bug this quantising exists to avoid: argmax wobble inside one
    note counted as dozens of notes per bar."""
    c = _cqt([12, 12])
    c[13, 3] = 2.0  # one loud frame on a neighbouring semitone
    notes = notes_from_cqt(c, frames_per_slot=5 * 4)
    assert notes == [12, 12]


# ── The measures ────────────────────────────────────────────────────────────


def _line(notes: list[int | None]) -> BassLine:
    return analyse_bassline(notes, pitch_weight=np.ones(12))


def test_a_one_note_bass_has_no_range() -> None:
    line = _line([12] * 32)
    assert line.semitone_range == 0.0
    assert line.mean_jump == 0.0
    assert line.distinct == 1


def test_a_walking_line_travels() -> None:
    line = _line([12, 15, 17, 19] * 8)
    assert line.semitone_range >= 6
    assert line.mean_jump > 1


def test_octave_jumps_read_as_big_leaps() -> None:
    line = _line([12, 24] * 16)
    assert line.mean_jump == pytest.approx(12.0)


def test_notes_per_bar_counts_changes_not_frames() -> None:
    """Sixteen sixteenths to the bar: a note held for four of them is one
    note, not four."""
    line = _line([12, 12, 12, 12, 15, 15, 15, 15] * 4)
    assert line.notes_per_bar == pytest.approx(4.0, abs=0.5)


def test_a_rolling_line_is_busier_than_a_sustained_one() -> None:
    rolling = _line([12, 15, 12, 17] * 8)
    sustained = _line([12] * 16 + [15] * 16)
    assert rolling.notes_per_bar > sustained.notes_per_bar * 3


def test_rests_are_reported() -> None:
    line = _line([12, None, 12, None] * 8)
    assert line.rest_fraction == pytest.approx(0.5, abs=0.01)


def test_an_empty_line_is_not_an_error() -> None:
    line = _line([None] * 16)
    assert line.notes_per_bar == 0.0
    assert line.rest_fraction == 1.0
    assert line.is_major is None


# ── Major or minor ──────────────────────────────────────────────────────────


def test_a_minor_third_above_the_root_reads_minor() -> None:
    w = np.zeros(12)
    w[0] = 1.0
    w[3] = 0.6  # root + minor third
    assert analyse_bassline([0] * 16, pitch_weight=w).is_major is False


def test_a_major_third_above_the_root_reads_major() -> None:
    w = np.zeros(12)
    w[0] = 1.0
    w[4] = 0.6
    assert analyse_bassline([0] * 16, pitch_weight=w).is_major is True


def test_no_third_at_all_is_undecided() -> None:
    """A single-note bass says nothing about the mode — better silent than
    a coin toss."""
    w = np.zeros(12)
    w[0] = 1.0
    assert analyse_bassline([0] * 16, pitch_weight=w).is_major is None
