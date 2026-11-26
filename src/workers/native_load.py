"""Load a file onto a Traktor deck from inside Traktor (libtk's ``tk_load_path``).

For TK's own lists drawn in Traktor (the Spotify page): "Load into Deck A–D" in a row's menu,
as Traktor's own track menu has (User, 2026-11-24). Traktor's main thread (Qt's, there).
"""

from __future__ import annotations

import ctypes
import logging
import unicodedata

from workers.native_lib import process_lib

log = logging.getLogger(__name__)

DECKS = "ABCD"


def load_on_deck(deck: str, path: str, lib=None) -> bool:
    """Load *path* (a collection track) onto *deck* ("A"–"D"). False outside Traktor or when
    the deck letter is unknown. The path goes as TK spells it (NFC), then decomposed: Traktor
    may store either form."""
    if deck not in DECKS or not path:
        return False
    lib = lib or process_lib()
    fn = getattr(lib, "tk_load_path", None) if lib is not None else None
    if fn is None:
        return False
    fn.argtypes = [ctypes.c_int, ctypes.c_char_p]
    fn.restype = ctypes.c_int
    for spelled in dict.fromkeys((path, unicodedata.normalize("NFD", path))):
        if fn(DECKS.index(deck), spelled.encode()) == 0:
            log.info("loaded onto deck %s: %s", deck, path)
            return True
    log.warning("could not load onto deck %s: %s", deck, path)
    return False
