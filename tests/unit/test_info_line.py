"""The line under the cover art: what the deck is actually doing.

Both numbers on it are *effective* values, not written ones. The BPM has
always been — it is the master clock, with the pitch fader in it, shown
beside the track's own. The key now matches: with Traktor's Key knob turned,
the written key is no longer the key you hear, and picking the next track off
a stale one is how you land a clash.
"""

from __future__ import annotations

from ui.now_playing import info_line_html, sounding_key

# ── Which key a turned knob leaves you in ──────────────────────────────────


def test_an_untouched_knob_sounds_as_written() -> None:
    assert sounding_key("8A", 0) is None


def test_a_turned_knob_moves_round_the_wheel() -> None:
    """Seven places to the semitone, as everywhere else: 8A + 2 is 10A."""
    assert sounding_key("8A", 2) == "10A"
    assert sounding_key("8A", -1) == "1A"


def test_no_key_sounds_like_nothing() -> None:
    assert sounding_key(None, 2) is None
    assert sounding_key("", 2) is None


def test_an_unreadable_key_is_not_invented() -> None:
    assert sounding_key("banana", 2) is None


# ── The line itself ────────────────────────────────────────────────────────


def test_the_unit_is_gone() -> None:
    """ "BPM" spelled out cost more width than it bought, and the line has
    two keys to fit now."""
    html = info_line_html(camelot="8A", db_bpm=128.0, master_bpm=130.0, semitones=0)
    assert "BPM" not in html


def test_the_written_and_playing_tempo_are_both_there() -> None:
    html = info_line_html(camelot="8A", db_bpm=128.0, master_bpm=130.0, semitones=0)
    assert "128" in html
    assert "130.00" in html
    assert "+1.56%" in html


def test_the_written_tempo_is_rounded() -> None:
    """Only the playing tempo needs hundredths — that is the number you
    beatmatch against. The track's own is a label, and a whole one leaves
    room for the two keys."""
    html = info_line_html(camelot=None, db_bpm=128.03, master_bpm=0.0, semitones=0)
    assert "128" in html
    assert "128.0" not in html


def test_a_stopped_deck_shows_the_tracks_own_tempo() -> None:
    html = info_line_html(camelot="8A", db_bpm=128.0, master_bpm=0.0, semitones=0)
    assert "128" in html
    assert "%" not in html


def test_an_untransposed_key_reads_once() -> None:
    html = info_line_html(camelot="8A", db_bpm=128.0, master_bpm=0.0, semitones=0)
    assert "1m" in html
    assert "+" not in html


def test_a_transposed_key_shows_both_and_the_move() -> None:
    """Written key, sounding key, and how far the knob is — the same shape
    as the tempo beside it."""
    html = info_line_html(camelot="8A", db_bpm=128.0, master_bpm=130.0, semitones=2)
    assert "1m" in html  # written
    assert "3m" in html  # sounding
    assert "+2" in html


def test_a_downward_shift_keeps_its_sign() -> None:
    html = info_line_html(camelot="8A", db_bpm=0.0, master_bpm=0.0, semitones=-1)
    assert "-1" in html


def test_the_sounding_key_is_coloured_as_itself() -> None:
    """Colour is how the wheel is read at a glance; painting the sounding
    key in the written key's colour would say the wrong neighbourhood."""
    from core.constants import CAMELOT_COLORS

    html = info_line_html(camelot="8A", db_bpm=0.0, master_bpm=0.0, semitones=2)
    assert CAMELOT_COLORS["10A"] in html


def test_a_track_with_no_key_still_shows_its_tempo() -> None:
    html = info_line_html(camelot=None, db_bpm=128.0, master_bpm=0.0, semitones=0)
    assert "128" in html


def test_nothing_at_all_is_an_empty_line() -> None:
    assert info_line_html(camelot=None, db_bpm=0.0, master_bpm=0.0, semitones=0) == ""
