"""The two Traktor-key -> Camelot conversions must never disagree.

``core.rekordbox.camelot`` is what the export writes; ``core.analysis``'s
``traktor_key_to_fields`` fills the database columns the matcher reads. A track
whose two answers differ would show one key in the library and export another.
"""

from __future__ import annotations

import pytest

from core.analysis import traktor_key_to_fields
from core.constants import CAMELOT_MAP
from core.rekordbox import camelot, key_parts


@pytest.mark.parametrize("value", range(24))
def test_every_traktor_key_gets_the_same_camelot_from_both(value: int) -> None:
    key_num, is_major, cam = traktor_key_to_fields(value)
    assert cam == camelot(value)
    assert (key_num, is_major) == key_parts(value)
    assert CAMELOT_MAP[(key_num, is_major)] == cam


@pytest.mark.parametrize("value", [-1, 24, 99])
def test_out_of_range_is_unknown_in_both(value: int) -> None:
    assert traktor_key_to_fields(value) is None
    assert camelot(value) == ""
