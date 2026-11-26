"""Unit tests for the matching-axis card flow geometry.

Covers ``_flow_columns``: given an available width, decide how many cards sit
on a row and how wide each one is. Rows are balanced by item count and cards
stretch to fill each row edge-to-edge; the column count drops as the window
narrows (cards never go below the minimum width). ``max_w`` is an optional cap
— in production it is 0 (unbounded) so the row is always completely filled. The
function is pure arithmetic (no Qt), so it's tested directly.
"""

from __future__ import annotations

from ui.now_playing import _flow_columns

HSPACE = 6
MIN_W = 130
MAX_W = 240


def cols(n: int, width: int) -> int:
    return _flow_columns(n, width, HSPACE, MIN_W, MAX_W)[0]


def item_w(n: int, width: int) -> int:
    return _flow_columns(n, width, HSPACE, MIN_W, MAX_W)[1]


def test_no_items_returns_min_width() -> None:
    assert _flow_columns(0, 1000, HSPACE, MIN_W, MAX_W) == (0, MIN_W)


def test_single_item_fills_up_to_max_width() -> None:
    # One card, plenty of room → capped at the maximum, not stretched edge-to-edge.
    assert _flow_columns(1, 1000, HSPACE, MIN_W, MAX_W) == (1, MAX_W)


def test_columns_grow_with_width() -> None:
    # Six cards available; wider windows pack more per row.
    assert cols(6, 200) == 1  # 200 < 2·130 + 6 = 266
    assert cols(6, 270) == 2  # 2·130 + 6 = 266 ≤ 270 < 3·130 + 12 = 402
    assert cols(6, 410) == 3  # 3·130 + 12 = 402 ≤ 410


def test_column_count_never_exceeds_item_count() -> None:
    # A very wide window with only two cards still uses two columns.
    assert cols(2, 5000) == 2


def test_rows_are_balanced_not_packed() -> None:
    # 12 cards that could fit 8 per row are split 6 + 6, not 8 + 4.
    assert cols(12, 1200) == 6
    # 9 cards over two rows → 5 + 4 (each row as even as possible).
    assert cols(9, 1200) == 5
    # 9 cards that only fit 4 per row → three balanced rows of 3.
    assert cols(9, 560) == 3


def test_items_stretch_to_share_row_evenly() -> None:
    # Two columns across 400px (one 6px gap) → (400-6)/2 = 197, within bounds.
    assert _flow_columns(2, 400, HSPACE, MIN_W, MAX_W) == (2, 197)
    # Wider row → even share exceeds the max, so each card is capped at 240.
    assert _flow_columns(2, 600, HSPACE, MIN_W, MAX_W) == (2, MAX_W)


def test_item_width_never_below_min() -> None:
    # Width too small even for one min-width card → still never below the min.
    assert item_w(3, 50) == MIN_W


def test_item_width_respects_min_and_max_bounds() -> None:
    for width in range(50, 2000, 37):
        for n in range(1, 8):
            w = item_w(n, width)
            assert MIN_W <= w <= MAX_W


# ── Unbounded mode (max_w=0) — the production configuration ─────────────────


def test_unbounded_fills_row_edge_to_edge() -> None:
    # 12 cards, two balanced rows of 6, no max cap → each card grows past 240
    # so 6 cards + gaps exactly span the available width.
    c, w = _flow_columns(12, 1620, HSPACE, MIN_W)  # max_w defaults to 0
    assert c == 6
    assert w == (1620 - 5 * HSPACE) // 6  # 265, not clamped to 240
    assert 6 * w + 5 * HSPACE <= 1620 < (6 * (w + 1) + 5 * HSPACE)


def test_unbounded_single_card_spans_width() -> None:
    # With no cap a lone card fills the whole panel.
    assert _flow_columns(1, 1000, HSPACE, MIN_W) == (1, 1000)


def test_unbounded_never_below_min_width() -> None:
    for width in range(50, 2500, 31):
        for n in range(1, 14):
            assert _flow_columns(n, width, HSPACE, MIN_W)[1] >= MIN_W
