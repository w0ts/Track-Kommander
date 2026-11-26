"""Which features a track has been analysed for, as one integer.

Replaces the global analysis version: that re-ran everything whenever any
feature changed, because it could not say which one. A bit per feature,
set when the feature is computed *at its current version*, says exactly.
"""

from __future__ import annotations

from core.feature_bits import (
    FEATURE_BITS,
    bit_of,
    clear_bit,
    enabled_mask,
    mask_for,
    missing_features,
    stale_clause,
)


def test_every_feature_owns_a_bit() -> None:
    from core.features import _ALL_FEATURES

    # Stored features only: a derived column (the release year, the import
    # day) is worked out, not computed by a scan, so it owns no bit.
    keys = [f.key for f in _ALL_FEATURES if f.column and not f.derived]
    assert set(keys) <= set(FEATURE_BITS)
    assert len(set(FEATURE_BITS.values())) == len(FEATURE_BITS), "two features share a bit"


def test_bits_fit_in_a_sqlite_integer() -> None:
    """SQLite integers are 64-bit signed, so bit 63 is the sign bit."""
    assert max(FEATURE_BITS.values()) < 63, (
        "out of bits — add a second mask column rather than overflowing"
    )


def test_bits_are_stable_across_registry_order() -> None:
    """A bit is written into every user's database. Reordering the registry
    must not silently remap them, or every track reads as analysed for the
    wrong things."""
    assert bit_of("melodic") == FEATURE_BITS["melodic"]
    assert bit_of("melodic") != bit_of("danceability")


def test_an_unknown_feature_has_no_bit() -> None:
    assert bit_of("no_such_feature") is None


# ── Building masks ──────────────────────────────────────────────────────────


def test_mask_for_sets_one_bit_per_feature() -> None:
    m = mask_for(["melodic", "danceability"])
    assert m == (1 << bit_of("melodic")) | (1 << bit_of("danceability"))


def test_unknown_features_are_ignored_not_fatal() -> None:
    """A database written by a newer build may name a feature this one has
    dropped. Ignoring it beats refusing to scan."""
    assert mask_for(["melodic", "gone"]) == mask_for(["melodic"])


def test_an_empty_mask_is_zero() -> None:
    assert mask_for([]) == 0


# ── Asking what is missing ──────────────────────────────────────────────────


def test_a_track_with_everything_is_missing_nothing() -> None:
    want = mask_for(["melodic", "danceability"])
    assert missing_features(want, want) == []


def test_the_missing_features_are_named() -> None:
    want = mask_for(["melodic", "danceability"])
    have = mask_for(["melodic"])
    assert missing_features(have, want) == ["danceability"]


def test_extra_features_a_track_has_are_not_missing() -> None:
    """Analysed for something now disabled: still up to date."""
    have = mask_for(["melodic", "danceability", "bounce"])
    assert missing_features(have, mask_for(["melodic"])) == []


def test_a_null_mask_means_nothing_analysed() -> None:
    assert missing_features(None, mask_for(["melodic"])) == ["melodic"]


# ── Bumping a feature's version ─────────────────────────────────────────────


def test_clearing_one_bit_leaves_the_others() -> None:
    have = mask_for(["melodic", "danceability"])
    after = clear_bit(have, "melodic")
    assert missing_features(after, have) == ["melodic"]


def test_clearing_an_unknown_feature_changes_nothing() -> None:
    have = mask_for(["melodic"])
    assert clear_bit(have, "gone") == have


# ── The SQL ─────────────────────────────────────────────────────────────────


def test_the_stale_clause_asks_for_what_needs_work() -> None:
    """Asking which tracks are stale, not which are current: normally there
    are none, and at 200k tracks that is 11 ms against 48."""
    sql, params = stale_clause(mask_for(["melodic"]))
    assert "!=" in sql
    assert params == [mask_for(["melodic"]), mask_for(["melodic"])]


def test_nothing_enabled_means_nothing_is_stale() -> None:
    sql, params = stale_clause(0)
    assert params == [0, 0]


def test_enabled_mask_reads_the_settings(monkeypatch) -> None:
    from PySide6.QtCore import QSettings

    seen: dict[str, bool] = {"melodic": True, "danceability": False}
    monkeypatch.setattr(
        QSettings,
        "value",
        lambda self, key, default=None, type=None: seen.get(key.split("/")[1], default),
    )
    monkeypatch.delenv("TK_EMBED", raising=False)
    m = enabled_mask(["melodic", "danceability"])
    assert m == mask_for(["melodic"])


def test_inside_traktor_every_feature_is_computed(monkeypatch) -> None:
    """No Compute group inside Traktor: the default (all on), whatever was switched off."""
    from PySide6.QtCore import QSettings

    monkeypatch.setattr(QSettings, "value", lambda self, key, default=None, type=None: False)
    monkeypatch.setenv("TK_EMBED", "/repo/traktor-native/re/embed_tk.py")
    assert enabled_mask(["melodic", "danceability"]) == mask_for(["melodic", "danceability"])
