"""Date conditions in smart playlist queries: absolute and "in the last".

Traktor's own query language has relative dates — ``DAYS_AGO(n)``,
``WEEKS_AGO(n)``, ``MONTHS_AGO(n)``, ``YEARS_AGO(n)``, read from its binary
next to the UI's "is in the last". The date smartlists this app writes use
them, so exporting one to a stick has to evaluate them.
"""

from __future__ import annotations

from datetime import date

import pytest

import core.smartlist as smartlist
from core.rekordbox import Track
from core.smartlist import UnsupportedQuery, evaluate


@pytest.fixture(autouse=True)
def today(monkeypatch):
    monkeypatch.setattr(smartlist, "_today", lambda: date(2026, 9, 26))


def _t(path, added="", released=""):
    return Track(path=path, date_added=added, release_date=released)


TRACKS = [
    _t("/new", added="2026-09-20", released="2026-09-01"),
    _t("/month", added="2026-08-27", released="2026-06-30"),
    _t("/old", added="2025-01-10", released="2024-01-01"),
    _t("/undated"),
]


def _paths(query):
    return [t.path for t in evaluate(query, TRACKS)]


def test_imported_in_the_last_month() -> None:
    assert _paths("$IMPORTDATE >= MONTHS_AGO(1)") == ["/new", "/month"]


def test_the_cutoff_day_itself_is_in() -> None:
    assert _paths("$IMPORTDATE >= DAYS_AGO(30)") == ["/new", "/month"]
    assert _paths("$IMPORTDATE >= DAYS_AGO(29)") == ["/new"]


def test_released_in_the_last_three_months() -> None:
    assert _paths("$RELEASEDATE >= MONTHS_AGO(3)") == ["/new", "/month"]


def test_years_and_weeks() -> None:
    assert _paths("$IMPORTDATE >= YEARS_AGO(1)") == ["/new", "/month"]
    assert _paths("$IMPORTDATE >= WEEKS_AGO(1)") == ["/new"]


def test_not_in_the_last_is_the_older_side() -> None:
    assert _paths("$IMPORTDATE < MONTHS_AGO(1)") == ["/old"]


def test_a_track_without_a_date_is_never_in_a_date_range() -> None:
    for q in ("$IMPORTDATE >= YEARS_AGO(50)", "$IMPORTDATE < DAYS_AGO(1)"):
        assert "/undated" not in _paths(q)


def test_absolute_dates_in_either_spelling() -> None:
    assert _paths('$IMPORTDATE > "2026-01-01"') == ["/new", "/month"]
    assert _paths('$IMPORTDATE > "2026/1/1"') == ["/new", "/month"]


def test_text_match_on_a_date_still_works() -> None:
    assert _paths('$RELEASEDATE % "2024"') == ["/old"]


def test_month_arithmetic_clamps_to_the_month_end(monkeypatch) -> None:
    monkeypatch.setattr(smartlist, "_today", lambda: date(2026, 3, 31))
    tracks = [_t("/feb28", added="2026-02-28"), _t("/feb27", added="2026-02-27")]
    got = [t.path for t in evaluate("$IMPORTDATE >= MONTHS_AGO(1)", tracks)]
    assert got == ["/feb28"]


def test_a_relative_date_on_a_non_date_field_is_refused() -> None:
    with pytest.raises(UnsupportedQuery):
        evaluate("$BPM >= MONTHS_AGO(1)", TRACKS)


def test_an_unreadable_date_is_refused() -> None:
    with pytest.raises(UnsupportedQuery):
        evaluate('$IMPORTDATE > "last tuesday"', TRACKS)


def test_traktors_own_saved_form_evaluates() -> None:
    """What Traktor 4.5.1 writes when it saves a date smartlist."""
    assert _paths("$IMPORTDATE > 2026-08-26 & $IMPORTDATE < 2026-10-01") == ["/new", "/month"]


def test_a_pipe_is_or() -> None:
    assert _paths("$IMPORTDATE < 2025-12-31 | $IMPORTDATE > 2026-09-01") == ["/new", "/old"]
