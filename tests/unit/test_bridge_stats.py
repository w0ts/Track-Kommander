"""Tests for the D2 bridge traffic readout in Settings.

"Traktor: Connected" only means a ping arrived at some point — it stays green
off a 5 s watchdog and says nothing about whether messages are still flowing.
``bridge_stats_text`` is the readout that does, and it is the first thing to
look at when the bridge goes quiet.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6.QtWidgets")

from ui.settings import bridge_stats_text


def test_no_traffic_yet() -> None:
    assert bridge_stats_text(0, None) == "No data received from Traktor yet"
    assert bridge_stats_text(0, 12.0) == "No data received from Traktor yet"


def test_negative_count_is_treated_as_none() -> None:
    assert bridge_stats_text(-1, None) == "No data received from Traktor yet"


def test_singular_message() -> None:
    assert bridge_stats_text(1, 0.0).startswith("1 message from Traktor")


def test_thousands_separator() -> None:
    assert bridge_stats_text(12345, 0.0).startswith("12,345 messages")


@pytest.mark.parametrize(
    ("age", "expected"),
    [
        (0.0, "just now"),
        (1.9, "just now"),
        (2.0, "2s ago"),
        (45.0, "45s ago"),
        (89.9, "90s ago"),
        (90.0, "2m ago"),
        (600.0, "10m ago"),
    ],
)
def test_age_wording(age: float, expected: str) -> None:
    assert bridge_stats_text(5, age).endswith(f"last {expected}")


def test_unknown_age() -> None:
    assert bridge_stats_text(5, None).endswith("last —")
