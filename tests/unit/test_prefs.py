"""One place that says what a setting is called and what it is when unset.

Each of these was spelled out wherever it was read — the key as a string and
the default beside it, in up to four files. They agreed, because someone had
kept them agreeing by hand; changing a default meant finding every copy.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings

from core import prefs
from core.prefs import (
    AUTOMATION_MODE,
    BPM_WINDOW_PCT,
    KEEP_DISPLAY_AWAKE,
    RAMP_BARS,
    VERBOSE_LOG,
    Pref,
)

SRC = Path(__file__).resolve().parents[2] / "src"
ALL = [AUTOMATION_MODE, BPM_WINDOW_PCT, KEEP_DISPLAY_AWAKE, RAMP_BARS, VERBOSE_LOG]


def test_the_defaults_are_the_ones_the_app_always_had() -> None:
    assert AUTOMATION_MODE.get() == "play_cue"
    assert RAMP_BARS.get() == 4
    assert BPM_WINDOW_PCT.get() == 10
    assert KEEP_DISPLAY_AWAKE.get() is True
    assert VERBOSE_LOG.get() is False


@pytest.mark.parametrize(
    ("pref", "value"),
    [
        (AUTOMATION_MODE, "automix"),
        (RAMP_BARS, 16),
        (BPM_WINDOW_PCT, 0),
        (KEEP_DISPLAY_AWAKE, False),
        (VERBOSE_LOG, True),
    ],
)
def test_what_is_set_is_what_is_read(pref: Pref, value) -> None:
    pref.set(value)
    assert pref.get() == value
    assert type(pref.get()) is type(value)


def test_a_value_survives_being_written_to_disk() -> None:
    """An ini file holds text. ``false`` has to come back as False, not as a
    string that is true because it is not empty."""
    KEEP_DISPLAY_AWAKE.set(False)
    RAMP_BARS.set(8)
    QSettings().sync()

    fresh = QSettings()
    assert KEEP_DISPLAY_AWAKE.get(fresh) is False
    assert RAMP_BARS.get(fresh) == 8


def test_a_value_that_cannot_be_read_falls_back_to_the_default() -> None:
    QSettings().setValue(RAMP_BARS.key, "several")
    assert RAMP_BARS.get() == 4


def test_a_given_store_is_the_one_used() -> None:
    store = QSettings()
    BPM_WINDOW_PCT.set(25, store)
    assert int(store.value(BPM_WINDOW_PCT.key)) == 25


def test_no_two_settings_share_a_key() -> None:
    keys = [p.key for p in ALL]
    assert len(set(keys)) == len(keys)
    assert set(ALL) <= set(prefs.ALL)


@pytest.mark.parametrize("pref", ALL, ids=lambda p: p.key)
def test_the_key_is_written_out_in_one_place_only(pref: Pref) -> None:
    quoted = re.compile(rf"""["']{re.escape(pref.key)}["']""")
    elsewhere = [
        str(path.relative_to(SRC))
        for path in sorted(SRC.rglob("*.py"))
        if path.name != "prefs.py" and quoted.search(path.read_text(encoding="utf-8"))
    ]
    assert elsewhere == [], f"read it through core.prefs instead of spelling out {pref.key!r}"
