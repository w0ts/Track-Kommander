"""A different set of tracks at each launch while no deck is the source.

With no source track TK lists the library ranked on the absolute axes, or browsed by rating and
recency: the same first tracks every time, and Traktor's list shows only those (User,
2026-11-26: "it seems to always display the same set of tracks. can we have a random set ?
or sorted random"). So a random selection is shown instead, in the list's own order (best match,
or rating then recency, first). It is drawn once per launch: while TK runs, the selection stays
put, so the list does not jump under the user's hand.
"""

from __future__ import annotations

import random

#: How many tracks are shown (Traktor's list shows TK's first 200).
SAMPLE = 200
#: A ranked list is sampled from its best ones only: the rest fit the axes too poorly to show.
RANKED_POOL = 600

#: This launch's draw.
_SEED = random.randrange(2**32)


def sample[T](items: list[T], *, ranked: bool, n: int = SAMPLE, seed: int | None = None) -> list[T]:
    """*n* of *items* at random, in their order. *ranked*: drawn from the best RANKED_POOL."""
    pool = items[:RANKED_POOL] if ranked else items
    if len(pool) <= n:
        return list(pool)
    rng = random.Random(_SEED if seed is None else seed)
    return [pool[i] for i in sorted(rng.sample(range(len(pool)), n))]
