"""The stats page says what each feature is and what a correlation means.

A tester read "Sameness", "Similarity" and a grid of r values without knowing
what any of them measured. Every feature carries a description in the
registry; the page shows it where the feature is, and turns r into words.
"""

from __future__ import annotations

from pathlib import Path

from core.features import FEATURES
from ui.stats import correlation_note, feature_explainer


def test_every_feature_has_a_description() -> None:
    assert [s.key for s in FEATURES if not s.desc] == []


def test_the_explainer_names_both_ends_and_what_it_measures() -> None:
    text = feature_explainer("sameness")
    assert text.startswith("Sameness: goes somewhere ←→ stays put")
    assert "32 bars later" in text


def test_the_explainer_wraps_so_a_tooltip_stays_narrow() -> None:
    assert max(len(line) for line in feature_explainer("key").splitlines()) <= 72


def test_an_unknown_key_gives_nothing() -> None:
    assert feature_explainer("nope") == ""


def test_a_strong_correlation_warns_about_counting_twice() -> None:
    note = correlation_note("Party", "Arousal", 0.91)
    assert "same thing" in note and "twice" in note


def test_an_inverse_correlation_says_one_falls_as_the_other_rises() -> None:
    assert "falls" in correlation_note("Relaxed", "Aggressive", -0.7)


def test_a_weak_correlation_reads_as_independent() -> None:
    assert "independent" in correlation_note("BPM", "Vocal", 0.05)


def test_the_public_readme_documents_every_dimension() -> None:
    """SITE.md is the website (public_site.py renders it); a new axis must not
    ship unexplained there. README.md and README.public.md are developer-only
    and link to the site instead."""
    # Named, not left to the platform: the file is UTF-8, and Windows would
    # otherwise read it as CP1252 and stop at the first byte that is not.
    root = Path(__file__).resolve().parents[2]
    readme = (root / "SITE.md").read_text(encoding="utf-8")
    assert [s.label for s in FEATURES if f"**{s.label}**" not in readme] == []
