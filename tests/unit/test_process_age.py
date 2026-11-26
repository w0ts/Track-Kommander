"""How long this process has run: inside Traktor, how long ago Traktor started.

Traktor puts the last session's tracks back on its decks about 15 s after it
starts (measured 2026-11-24), after TK has attached. TK took that restore for a
load and AutoMix started deck A on its own at launch.
"""

from __future__ import annotations

import os

import pytest

from core.process_age import parse_etime, process_age_s


@pytest.mark.parametrize(
    ("text", "seconds"),
    [
        ("03:24", 204.0),
        ("  00:07\n", 7.0),
        ("01:02:03", 3723.0),
        ("2-01:00:00", 2 * 86400.0 + 3600.0),
    ],
)
def test_ps_elapsed_time_is_read(text: str, seconds: float) -> None:
    assert parse_etime(text) == seconds


@pytest.mark.parametrize("text", ["", "soon", "1:2:3:4", "x-01:00"])
def test_anything_else_is_unknown(text: str) -> None:
    assert parse_etime(text) is None


def test_this_process_has_an_age() -> None:
    age = process_age_s(os.getpid())
    assert age is not None and 0.0 <= age < 86400.0


def test_a_missing_process_has_none() -> None:
    assert process_age_s(2**22 + 12345) is None
