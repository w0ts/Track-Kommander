"""One colour per harmonic relationship in the Key column.

The Key column was painted by the composite traffic light, which is three
buckets with its first cliff at 80. Every relationship a DJ actually mixes on
scores above that — same key 100, neighbour 90 — so a perfect match and a
neighbour came out the identical green, and the column could not be read at a
glance. Which is the one thing it is for.
"""

from __future__ import annotations

from core.matcher import _KEY_CROSS, _KEY_SAME, KEY_SCORE_LEVELS
from ui.style import key_score_color


def test_every_level_the_tables_can_produce_is_covered() -> None:
    """Derived from the tables, so adding a distance cannot leave a level
    with no colour of its own."""
    assert set(KEY_SCORE_LEVELS) == set(_KEY_SAME) | set(_KEY_CROSS)
    assert list(KEY_SCORE_LEVELS) == sorted(KEY_SCORE_LEVELS, reverse=True)


def test_no_two_levels_share_a_colour() -> None:
    """The whole point: one colour per score level."""
    seen = {}
    for level in KEY_SCORE_LEVELS:
        name = key_score_color(level).name()
        assert name not in seen, f"score {level} and {seen[name]} are both {name}"
        seen[name] = level


def test_the_regression_a_perfect_match_is_not_a_neighbour() -> None:
    """100 and 90 were both #00e676."""
    assert key_score_color(100.0).name() != key_score_color(90.0).name()


def test_the_four_mixing_relationships_are_far_apart() -> None:
    """Same key, neighbour, relative major/minor, neighbour across modes.
    These are read mid-mix, so they need obvious separation, not the one-step
    difference that separates two unrelated keys."""
    tops = [key_score_color(s) for s in (100.0, 90.0, 75.0, 50.0)]
    for a, b in zip(tops, tops[1:], strict=False):
        gap = abs(a.red() - b.red()) + abs(a.green() - b.green()) + abs(a.blue() - b.blue())
        assert gap >= 100, f"{a.name()} and {b.name()} are too close ({gap})"


def test_a_perfect_match_is_the_green_everything_else_is_measured_against() -> None:
    assert key_score_color(100.0).name() == "#00e676"


def _lum(score: float) -> float:
    """Perceived brightness. Qt's lightness treats saturated lime and dark
    red as near-equal, which is not how an eye scanning a column sees them."""
    c = key_score_color(score)
    return 0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue()


def test_the_mixable_set_is_brighter_than_everything_else() -> None:
    """Brightness is the glanceable channel. The four relationships that work
    should catch the eye; the ones that clash should recede. Ranking inside
    each group is the hue's job, not brightness'."""
    mixable = [_lum(s) for s in (100.0, 90.0, 75.0, 50.0)]
    rest = [_lum(s) for s in KEY_SCORE_LEVELS if s <= 40.0]
    assert min(mixable) > max(rest)


def test_the_unrelated_tail_fades_with_distance() -> None:
    """Within the tail, further away is dimmer — so the eye is not drawn to
    a key that happens to be eight steps round the wheel."""
    tail = [_lum(s) for s in KEY_SCORE_LEVELS if s <= 40.0]
    assert tail == sorted(tail, reverse=True)


def test_the_safe_moves_are_one_colour_family() -> None:
    """Same key, the energy step and the relative major/minor all work. They
    should read as a family — green — so "can I mix this?" is answered before
    "how exactly?". The relative is a different hue rather than a duller
    green: it is a different kind of move, not a worse one."""
    for score in (100.0, 90.0, 75.0):
        c = key_score_color(score)
        assert c.green() > c.red() and c.green() > c.blue(), (
            f"{score} is {c.name()}, which does not read as green"
        )


def test_the_diagonal_is_a_warning_not_a_refusal() -> None:
    """+-1 across modes works, but it is the least reliable of the set."""
    c = key_score_color(50.0)
    assert c.red() > 200 and c.green() > 120 and c.blue() < 80, c.name()


def test_an_unknown_key_is_not_painted_like_a_bad_one() -> None:
    """0.0 means "no key on this track", not "incompatible" — the matcher is
    explicit about that, and the colour should not lie about it."""
    assert key_score_color(0.0).name() != key_score_color(min(KEY_SCORE_LEVELS)).name()


def test_a_score_between_levels_takes_the_nearest_one_below() -> None:
    """Nothing produces these today, but a table change or a rounded value
    must not fall through to an exception."""
    assert key_score_color(95.0).name() == key_score_color(90.0).name()
    assert key_score_color(1000.0).name() == key_score_color(100.0).name()
    assert key_score_color(-5.0).name() == key_score_color(0.0).name()
