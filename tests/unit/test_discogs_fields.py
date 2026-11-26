"""Which Discogs value fills which Traktor field, in Traktor's format, and only
where the field is empty."""

from __future__ import annotations

import pytest

from core.discogs_fields import (
    DEFAULT_MAPPING,
    DiscogsRecord,
    is_placeholder_date,
    original_date,
    planned_changes,
    read_mapping,
    sources_for,
    traktor_date,
)

RECORD = DiscogsRecord(
    release_id=1,
    genres=("Electronic", "Pop"),
    styles=("Deep House", "Minimal"),
    label="Planet Rhythm Records",
    catno="PRRUK 108",
    country="UK",
    released="2024-11-15",
    year=2024,
    original_year=2024,
)


@pytest.mark.parametrize(
    ("released", "year", "want"),
    [
        ("2024-11-15", None, "2024/11/15"),
        ("1987-07-00", None, "1987/7/1"),  # unknown day
        ("1998-00-00", None, "1998/1/1"),  # unknown month and day
        ("1998", None, "1998/1/1"),
        ("", 2001, "2001/1/1"),  # only the year field
        ("", None, ""),
        ("garbage", None, ""),
    ],
)
def test_discogs_dates_become_traktor_dates(released: str, year, want: str) -> None:
    assert traktor_date(released, year) == want


def test_the_original_year_wins_over_a_reissue() -> None:
    reissue = DiscogsRecord(1, released="2016-03-04", year=2016, original_year=1994)
    assert original_date(reissue) == "1994/1/1"
    first_pressing = DiscogsRecord(1, released="1994-05-02", year=1994, original_year=1994)
    assert original_date(first_pressing) == "1994/5/2"


def test_placeholder_dates() -> None:
    assert is_placeholder_date("2024/1/1")
    assert not is_placeholder_date("2024/1/22")
    assert not is_placeholder_date("")


def test_only_empty_fields_are_filled() -> None:
    current = {"GENRE": "Techno", "LABEL": "", "CATALOG_NO": "", "RELEASE_DATE": ""}
    changes = planned_changes(RECORD, current, DEFAULT_MAPPING)
    assert {c.field.attr: c.new for c in changes} == {
        "LABEL": "Planet Rhythm Records",
        "CATALOG_NO": "PRRUK 108",
        "RELEASE_DATE": "2024/11/15",
    }


def test_unmapped_fields_are_left_alone() -> None:
    mapping = {**DEFAULT_MAPPING, "label": "", "catalog_no": "", "release_date": ""}
    changes = planned_changes(RECORD, {}, mapping)
    assert [(c.field.key, c.new) for c in changes] == [("genre", "Deep House")]


def test_styles_can_go_into_comment_2() -> None:
    mapping = {**{k: "" for k in DEFAULT_MAPPING}, "comment2": "styles"}
    (change,) = planned_changes(RECORD, {"RATING": ""}, mapping)
    assert change.field.attr == "RATING"  # Traktor's name for Comment 2
    assert change.new == "Deep House, Minimal"


def test_a_placeholder_date_is_replaced_only_when_asked() -> None:
    mapping = {**{k: "" for k in DEFAULT_MAPPING}, "release_date": "release_date"}
    current = {"RELEASE_DATE": "2024/1/1"}
    assert planned_changes(RECORD, current, mapping) == []
    (change,) = planned_changes(RECORD, current, mapping, replace_placeholder_dates=True)
    assert (change.old, change.new) == ("2024/1/1", "2024/11/15")


def test_a_real_date_is_never_replaced() -> None:
    mapping = {**{k: "" for k in DEFAULT_MAPPING}, "release_date": "release_date"}
    current = {"RELEASE_DATE": "2023/6/2"}
    assert planned_changes(RECORD, current, mapping, replace_placeholder_dates=True) == []


def test_a_date_source_never_fills_a_text_field() -> None:
    mapping = {**{k: "" for k in DEFAULT_MAPPING}, "genre": "release_date"}
    assert planned_changes(RECORD, {}, mapping) == []
    assert all(s.is_date for s in sources_for("release_date"))
    assert not any(s.is_date for s in sources_for("genre"))


def test_mapping_falls_back_to_defaults_and_drops_unknown_sources() -> None:
    class Settings:
        def value(self, key, default):
            return {"discogs/map/genre": "nonsense", "discogs/map/comment": "styles"}.get(
                key, default
            )

    mapping = read_mapping(Settings())
    assert mapping["genre"] == ""
    assert mapping["comment"] == "styles"
    assert mapping["label"] == DEFAULT_MAPPING["label"]


def test_a_placeholder_is_not_replaced_by_another_guess() -> None:
    """The master's year with an unknown day is itself a January 1st."""
    reissue = DiscogsRecord(1, released="2016", year=2016, original_year=1994)
    mapping = {**{k: "" for k in DEFAULT_MAPPING}, "release_date": "original_date"}
    current = {"RELEASE_DATE": "2016/1/1"}
    assert planned_changes(reissue, current, mapping, replace_placeholder_dates=True) == []


def test_an_impossible_day_is_dropped() -> None:
    assert traktor_date("2019-02-31") == "2019/2/1"
