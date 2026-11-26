"""The spread list explains *why* a feature has no bar.

Three reasons, and they need different words: the feature is relational and
has no distribution of its own, nothing has been analysed for it yet, or the
library genuinely does not vary on it.
"""

from __future__ import annotations

from ui.stats import spread_note


def test_a_relational_feature_says_so() -> None:
    assert spread_note(None, relational=True) == "relational — no spread"


def test_a_scalar_with_no_data_says_it_needs_a_scan() -> None:
    """A new feature reads NULL for every track until a scan backfills it —
    calling that "relational" is simply wrong."""
    assert spread_note(None, relational=False) == "not analysed yet — run a scan"


def test_a_measured_feature_has_no_note() -> None:
    assert spread_note(42.0, relational=False) is None
