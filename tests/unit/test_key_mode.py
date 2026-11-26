"""One Key axis with three states, not two axes.

Key · Key-T · off. Strict harmonic matching, matching that allows Traktor's
Key knob to move up to three semitones, or the dimension dropped — and one
column that shows the shift when there is one.
"""

from __future__ import annotations

from core.features import KeyMode, next_key_mode


def test_it_cycles_through_the_three_states() -> None:
    assert next_key_mode(KeyMode.STRICT) is KeyMode.TRANSPOSE
    assert next_key_mode(KeyMode.TRANSPOSE) is KeyMode.OFF
    assert next_key_mode(KeyMode.OFF) is KeyMode.STRICT


def test_each_state_has_a_label_for_the_card() -> None:
    assert KeyMode.STRICT.label == "Key"
    assert KeyMode.TRANSPOSE.label == "Key-T"
    assert KeyMode.OFF.label == "Key"


def test_only_the_transposing_state_allows_a_shift() -> None:
    assert KeyMode.STRICT.max_semitones == 0
    assert KeyMode.TRANSPOSE.max_semitones == 3
    assert KeyMode.OFF.max_semitones == 0


def test_off_is_the_only_state_that_stops_ranking() -> None:
    assert KeyMode.STRICT.active and KeyMode.TRANSPOSE.active
    assert not KeyMode.OFF.active


def test_a_mode_survives_a_round_trip_through_settings() -> None:
    for mode in KeyMode:
        assert KeyMode.from_name(mode.name) is mode


def test_an_unknown_name_falls_back_to_the_default() -> None:
    """A setting written by another build must not break the panel."""
    from core.features import DEFAULT_KEY_MODE

    assert KeyMode.from_name("nonsense") is DEFAULT_KEY_MODE
    assert KeyMode.from_name(None) is DEFAULT_KEY_MODE


def test_transposing_is_what_you_get_without_choosing() -> None:
    """Strict is a subset: a shift costs, so a track that already fits still
    ranks first. Defaulting to strict only hides matches that were there."""
    from core.features import DEFAULT_KEY_MODE

    assert DEFAULT_KEY_MODE is KeyMode.TRANSPOSE
    assert DEFAULT_KEY_MODE.active


# ── What the deck is told on load ───────────────────────────────────────────


def test_only_the_transposing_mode_asks_for_a_shift() -> None:
    """Strict and off both reset the Key knob to zero: a deck keeps the
    shift it was last left on, so a track mixed in key after a transposed
    one would otherwise play detuned."""
    assert KeyMode.STRICT.max_semitones == 0
    assert KeyMode.OFF.max_semitones == 0
    assert KeyMode.TRANSPOSE.max_semitones > 0


# ── How far it may transpose is the DJ's call ───────────────────────────────


def test_the_range_defaults_to_three(tmp_path, monkeypatch) -> None:
    from PySide6.QtCore import QCoreApplication, QSettings

    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path))
    QCoreApplication.setApplicationName("tk-key-range-test")
    QCoreApplication.setOrganizationDomain("keyrange.invalid")
    from core.features import transpose_range

    assert transpose_range() == 3


def test_the_range_is_read_from_settings(tmp_path) -> None:
    from PySide6.QtCore import QCoreApplication, QSettings

    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path))
    QCoreApplication.setApplicationName("tk-key-range-test2")
    QCoreApplication.setOrganizationDomain("keyrange2.invalid")
    from core.features import key_range_key, transpose_range

    QSettings().setValue(key_range_key(), 2)
    assert transpose_range() == 2


def test_an_absurd_range_is_clamped(tmp_path) -> None:
    """Past a few semitones a track audibly changes character, whatever the
    harmony says — and a wild value must not produce a silent no-op."""
    from PySide6.QtCore import QCoreApplication, QSettings

    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path))
    QCoreApplication.setApplicationName("tk-key-range-test3")
    QCoreApplication.setOrganizationDomain("keyrange3.invalid")
    from core.features import MAX_TRANSPOSE_RANGE, key_range_key, transpose_range

    QSettings().setValue(key_range_key(), 99)
    assert transpose_range() == MAX_TRANSPOSE_RANGE
    QSettings().setValue(key_range_key(), 0)
    assert transpose_range() == 1
