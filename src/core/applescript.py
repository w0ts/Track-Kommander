"""Text as an AppleScript string literal.

AppleScript has two characters that mean something inside a string: the
double quote ends it, and the backslash escapes the next character. Text put
between quotes without handling them stops being text at the first one —
the rest is read as script.
"""

from __future__ import annotations


def escaped(text: str) -> str:
    """*text* with its quotes and backslashes escaped, for use inside a
    string literal that is already open."""
    return text.replace("\\", "\\\\").replace('"', '\\"')


def quoted(text: str) -> str:
    """*text* as a complete string literal, quotes included."""
    return f'"{escaped(text)}"'
