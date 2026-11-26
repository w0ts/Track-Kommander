"""The analysis folder a player derives from a track's path.

The first case is ours: Rekordbox 7.2.18 and an XDJ-1000 both used it for
the same track on 2026-09-24. The other three are the baken project's,
taken from a real Rekordbox export.
"""

from __future__ import annotations

import pytest

from core.anlz_path import anlz_dir, anlz_hash


@pytest.mark.parametrize(
    ("path", "folder"),
    [
        (
            "/Contents/IGLO/5 Years Backhaus Fundraiser Compilation Part 3/"
            "IGLO - 5 Years Backhaus Fundraiser Compilati.mp3",
            "PIONEER/USBANLZ/P03B/0002E2B7",
        ),
        ("/Contents/XamarA/UnknownAlbum/Unreal.mp3", "PIONEER/USBANLZ/P060/00012938"),
        (
            "/Contents/Szeir/Stolperfalle EP/Szeir - Stolperfalle EP - 01 Oort Cloud.flac",
            "PIONEER/USBANLZ/P074/0001B742",
        ),
        (
            "/Contents/Lidvall/The Strange Guy EP/Lidvall - The Strange Guy EP - 05 Where hav.flac",
            "PIONEER/USBANLZ/P005/0000CD61",
        ),
    ],
)
def test_the_folder_is_the_one_rekordbox_uses(path: str, folder: str) -> None:
    assert anlz_dir(path) == folder


def test_the_bucket_fits_the_players_table() -> None:
    _p, r = anlz_hash("/Contents/x.mp3")
    assert 0 <= r < 200003


def test_characters_beyond_the_bmp_hash_as_utf16_pairs() -> None:
    """The hash runs over UTF-16 code units, so an emoji counts twice."""
    assert anlz_hash("/Contents/\U0001f3b5.mp3") != anlz_hash("/Contents/?.mp3")
