"""Two things that were slow for a reason that is easy to put back.

Neither is timed here — a timing test fails on a busy machine and passes on
a fast one. Each pins the cause instead.
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from core import anlz
from core.genre_match import genre_tokens, style_vector

# ── The style cache ─────────────────────────────────────────────────────────
# A track's detected genres are three names with their probabilities, so the
# text is different for every track. The cache was sized for genre *tags*,
# which repeat: on a library larger than the cache, every lookup of every
# re-rank missed, evicted, and parsed the JSON again.

LIBRARY = 20_000


def _detected(i: int) -> str:
    return json.dumps(
        [
            {"name": "Techno", "prob": 0.5 + i * 1e-6},
            {"name": "House", "prob": 0.3},
            {"name": "Acid", "prob": 0.1},
        ]
    )


def test_a_large_library_is_parsed_once() -> None:
    style_vector.cache_clear()
    tracks = [_detected(i) for i in range(LIBRARY)]

    for text in tracks:  # the first re-rank
        style_vector(text)
    before = style_vector.cache_info()
    for text in tracks:  # every one after it
        style_vector(text)
    after = style_vector.cache_info()

    assert after.misses == before.misses, (
        f"{after.misses - before.misses} of {LIBRARY} tracks were parsed again"
    )
    style_vector.cache_clear()


def test_the_caches_are_still_bounded() -> None:
    assert style_vector.cache_info().maxsize is not None
    assert genre_tokens.cache_info().maxsize is not None


# ── The waveform's colours ──────────────────────────────────────────────────
# Built one column at a time in Python: 62 ms for a six-minute track, against
# 4 ms done as arrays. What matters is that the arrays give the same colours.


def _one_at_a_time(low, mid, high, loudest: float) -> tuple:
    scaled = [np.sqrt(b / loudest) * 7.0 for b in (low, mid, high)]
    return tuple(
        (int(round(r)), int(round(g)), int(round(b))) for r, g, b in zip(*scaled, strict=True)
    )


@pytest.mark.parametrize("seed", [0, 1, 2, 3])
def test_the_colours_are_the_ones_the_loop_gave(seed: int) -> None:
    rng = np.random.default_rng(seed)
    low, mid, high = (rng.random(5000, dtype=np.float32) for _ in range(3))
    loudest = float(max(low.max(), mid.max(), high.max()))

    assert anlz.colour_columns(low, mid, high, loudest) == _one_at_a_time(low, mid, high, loudest)


def test_a_value_exactly_between_two_levels_rounds_the_same_way() -> None:
    """Halves are where two ways of rounding part company."""
    levels = np.array([(k + 0.5) / 7.0 for k in range(7)], dtype=np.float64)
    band = (levels**2).astype(np.float32)  # sqrt(band) * 7 = k + 0.5

    assert anlz.colour_columns(band, band, band, 1.0) == _one_at_a_time(band, band, band, 1.0)


def test_the_colours_are_plain_integers() -> None:
    band = np.array([0.0, 0.25, 1.0], dtype=np.float32)

    colours = anlz.colour_columns(band, band, band, 1.0)

    assert colours == ((0, 0, 0), (4, 4, 4), (7, 7, 7))
    assert all(type(c) is int for column in colours for c in column)
