"""Unit tests for the matching feature registry."""

from __future__ import annotations

import core.features as feat


def test_every_feature_has_a_known_compute_group():
    for f in feat.FEATURES:
        assert f.group in feat.COMPUTE_GROUPS, f"{f.key} has unknown group {f.group}"


def test_keys_are_unique():
    keys = [f.key for f in feat.FEATURES]
    assert len(keys) == len(set(keys))


def test_core_axes_are_locked_and_relational():
    for key in ("bpm", "key"):
        spec = feat.BY_KEY[key]
        assert spec.locked is True
        assert spec.relational is True


def test_relational_axes_have_no_normalizer():
    for f in feat.FEATURES:
        if f.relational:
            assert f.normalize is None, f"{f.key} is relational but has a normalizer"
        else:
            assert f.normalize is not None, f"{f.key} is scalar but lacks a normalizer"


def test_similarity_rides_with_ml_group():
    assert feat.BY_KEY["similarity"].group == "ml"
    assert feat.BY_KEY["similarity"].column == "genres_vector"


def test_clamp01():
    assert feat.clamp01(-0.5) == 0.0
    assert feat.clamp01(1.5) == 1.0
    assert feat.clamp01(0.3) == 0.3


def test_deam_normalizer():
    spec = feat.BY_KEY["valence"]
    assert spec.normalize(1.0) == 0.0
    assert spec.normalize(9.0) == 1.0
    assert abs(spec.normalize(5.0) - 0.5) < 1e-9


def test_settings_key_conventions():
    assert feat.compute_group_key("ml") == "compute/ml"
    assert feat.axis_enabled_key("party") == "axis/party/enabled"
    assert feat.axis_value_key("party") == "axis/party/value"
    assert feat.axis_strength_key("party") == "axis/party/strength"
    assert feat.axis_active_key("party") == "axis/party/active"
    assert feat.axis_sync_key("party") == "axis/party/sync"


def test_short_labels_stay_compact():
    """Was "one word", which is a proxy for the real constraint: the rank
    list and the axis cards already carry "Danceability" at twelve
    characters. Two short words that say which bass measure this is beat one
    word that does not."""
    for f in feat.FEATURES:
        assert len(f.short_label) <= 12, (
            f"{f.key} short label is too long for a card title: {f.short_label!r}"
        )
        assert "\n" not in f.short_label


def test_features_in_group():
    ml = {f.key for f in feat.features_in_group("ml")}
    assert {"party", "valence", "similarity"} <= ml
    assert "melodic" not in ml  # melodic is its own (HPSS) group


def test_every_compute_group_has_a_scan_sentinel() -> None:
    """A group with no sentinel column is never backfilled: the scan asks
    "which tracks lack this column?" and, for a group it does not know about,
    the answer is always none."""
    from workers.analysis import _GROUP_SENTINEL

    computed = {g for g in feat.COMPUTE_GROUPS if g not in ("core", "embed")}
    assert computed <= set(_GROUP_SENTINEL), (
        f"compute groups the scan would skip: {computed - set(_GROUP_SENTINEL)}"
    )


def test_each_sentinel_is_a_real_column_of_its_group() -> None:
    from workers.analysis import _GROUP_SENTINEL

    for group, column in _GROUP_SENTINEL.items():
        columns = {f.column for f in feat.FEATURES if f.group == group}
        assert column in columns, f"{group}'s sentinel {column!r} is not one of {columns}"


def test_short_labels_are_unique() -> None:
    """The cards and the spread list show the short label; two axes wearing
    the same one cannot be told apart — "Bass density" and "Bass range" both
    came out as "Bass"."""
    shorts = [f.short_label for f in feat.FEATURES]
    dupes = {s for s in shorts if shorts.count(s) > 1}
    assert not dupes, f"short labels used twice: {sorted(dupes)}"
