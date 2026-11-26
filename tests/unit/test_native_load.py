"""Load into Deck A–D from TK's lists inside Traktor (libtk's tk_load_path)."""

from __future__ import annotations

import unicodedata

from workers.native_load import load_on_deck


class FakeLib:
    """tk_load_path: 0 loaded, -2 when the path is not in the collection in that spelling."""

    def __init__(self, stored: set[str]) -> None:
        self.stored, self.calls = stored, []
        lib = self

        class _Fn:
            argtypes = restype = None

            def __call__(self, deck: int, path: bytes) -> int:
                lib.calls.append((deck, path.decode()))
                return 0 if path.decode() in lib.stored else -2

        self.tk_load_path = _Fn()


def test_a_track_loads_onto_the_deck_asked() -> None:
    lib = FakeLib({"/m/a.mp3"})
    assert load_on_deck("C", "/m/a.mp3", lib) is True
    assert lib.calls == [(2, "/m/a.mp3")]


def test_a_path_traktor_stores_decomposed_is_found() -> None:
    nfc = unicodedata.normalize("NFC", "/m/Café.mp3")
    lib = FakeLib({unicodedata.normalize("NFD", nfc)})
    assert load_on_deck("A", nfc, lib) is True


def test_unknown_decks_and_tracks_are_refused() -> None:
    lib = FakeLib(set())
    assert load_on_deck("E", "/m/a.mp3", lib) is False
    assert load_on_deck("A", "/m/gone.mp3", lib) is False
