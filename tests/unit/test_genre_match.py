"""Unit tests for core.genre_match — token (Traktor) and style (CLAP) matching."""

from __future__ import annotations

import json

import pytest

from core.genre_match import (
    document_frequencies,
    genre_tokens,
    idf_weights,
    score_style,
    score_tokens,
    style_vector,
)


# ── tokenization ────────────────────────────────────────────────────────────
def test_genre_tokens_splits_words_and_separators():
    assert genre_tokens("Dark Ambient · Electronica · Drone") == frozenset(
        {"dark", "ambient", "electronica", "drone"}
    )


def test_genre_tokens_lowercases_and_drops_stopwords_and_punctuation():
    assert genre_tokens("Drum & Bass / Jungle") == frozenset({"drum", "bass", "jungle"})
    assert genre_tokens("Rhythm and Blues") == frozenset({"rhythm", "blues"})


def test_genre_tokens_empty():
    assert genre_tokens("") == frozenset()
    assert genre_tokens("&") == frozenset()


def test_multiword_genres_share_a_token():
    assert "house" in genre_tokens("Deep House") & genre_tokens("Tech House")


# ── document frequency / idf (the inverted index) ───────────────────────────
def test_document_frequencies_counts_documents():
    sets = [genre_tokens("Deep House"), genre_tokens("Tech House"), genre_tokens("Drone")]
    df = document_frequencies(sets)
    assert df["house"] == 2
    assert df["drone"] == 1


def test_idf_weights_rare_token_outweighs_common():
    df = {"house": 100, "drone": 1}
    idf = idf_weights(df, 100)
    assert idf["drone"] > idf["house"]
    assert all(w > 0 for w in idf.values())


def test_idf_weights_empty_corpus():
    assert idf_weights({"x": 1}, 0) == {}


# ── token scoring ────────────────────────────────────────────────────────────
def test_score_tokens_identical_is_one():
    src = genre_tokens("Deep House")
    assert score_tokens(src, src, {}) == pytest.approx(1.0)


def test_score_tokens_no_overlap_is_zero_not_none():
    a = genre_tokens("Drone")
    b = genre_tokens("Funk")
    assert score_tokens(a, b, {}) == 0.0


def test_score_tokens_missing_data_is_none():
    assert score_tokens(frozenset(), genre_tokens("Funk"), {}) is None
    assert score_tokens(genre_tokens("Funk"), frozenset(), {}) is None


def test_score_tokens_partial_overlap_between_zero_and_one():
    src = genre_tokens("Deep House")
    cand = genre_tokens("Tech House")
    s = score_tokens(src, cand, {})
    assert 0.0 < s < 1.0


def test_score_tokens_idf_rewards_rare_shared_token():
    # Source "Ambient Drone"; candidate A shares the rare "drone", candidate B
    # shares the common "ambient". With IDF, sharing the rare token scores higher.
    src = genre_tokens("Ambient Drone")
    cand_rare = genre_tokens("Industrial Drone")
    cand_common = genre_tokens("Ambient House")
    idf = idf_weights({"ambient": 100, "drone": 2, "industrial": 5, "house": 80}, 100)
    assert score_tokens(src, cand_rare, idf) > score_tokens(src, cand_common, idf)


# ── style vector / cosine ────────────────────────────────────────────────────
def _genres(*pairs):
    return json.dumps([{"name": n, "prob": p} for n, p in pairs])


def test_style_vector_parses_and_normalizes_case():
    v = dict(style_vector(_genres(("Dark Ambient", 0.6), ("Drone", 0.3))))
    assert v == {"dark ambient": 0.6, "drone": 0.3}


def test_style_vector_invalid_json_is_empty():
    assert style_vector("not json") == ()
    assert style_vector("") == ()
    assert style_vector(json.dumps({"not": "a list"})) == ()


def test_score_style_identical_is_one():
    v = style_vector(_genres(("Techno", 0.7), ("Acid", 0.3)))
    assert score_style(v, v) == pytest.approx(1.0)


def test_score_style_disjoint_is_zero():
    a = style_vector(_genres(("Techno", 0.7)))
    b = style_vector(_genres(("Funk", 0.7)))
    assert score_style(a, b) == 0.0


def test_score_style_missing_is_none():
    a = style_vector(_genres(("Techno", 0.7)))
    assert score_style(a, ()) is None
    assert score_style((), a) is None


def test_score_style_partial_overlap_between_zero_and_one():
    a = style_vector(_genres(("Techno", 0.7), ("Acid", 0.3)))
    b = style_vector(_genres(("Techno", 0.5), ("House", 0.5)))
    s = score_style(a, b)
    assert 0.0 < s < 1.0
