"""Putting text into an AppleScript string.

A path dropped between two double quotes ends the string at the first double
quote it contains, and whatever follows is read as script. A Traktor installed
in a folder with a quote in its name was enough.
"""

from __future__ import annotations

import shutil
import subprocess
import sys

import pytest

from core.applescript import quoted


@pytest.mark.parametrize(
    ("text", "literal"),
    [
        ("/Applications/Traktor Pro 4", '"/Applications/Traktor Pro 4"'),
        ('a "quoted" folder', '"a \\"quoted\\" folder"'),
        ("back\\slash", '"back\\\\slash"'),
        ('both \\" at once', '"both \\\\\\" at once"'),
        ("", '""'),
    ],
)
def test_quotes_and_backslashes_are_escaped(text: str, literal: str) -> None:
    assert quoted(text) == literal


@pytest.mark.skipif(
    sys.platform != "darwin" or not shutil.which("osascript"), reason="needs AppleScript"
)
@pytest.mark.parametrize(
    "text",
    [
        "/Applications/Traktor Pro 4",
        'a "quoted" folder',
        "back\\slash",
        'both \\" at once',
        '" & (do shell script "id") & "',
        "Motörhead – Ace of Spades",
    ],
)
def test_applescript_reads_back_what_went_in(text: str) -> None:
    """The real test: AppleScript itself gives the same text back, so none
    of it was read as script."""
    done = subprocess.run(
        ["osascript", "-e", f"return {quoted(text)}"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert done.returncode == 0, done.stderr
    assert done.stdout.removesuffix("\n") == text
