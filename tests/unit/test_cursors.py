"""The cursor shapes that crash Qt on macOS stay out of the app's code."""

from __future__ import annotations

from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"
CRASHING = ("SizeAllCursor", "WaitCursor")


def test_no_code_sets_a_cursor_that_crashes_on_macos() -> None:
    offenders = [
        f"{path.relative_to(SRC)}: {name}"
        for path in SRC.rglob("*.py")
        if path.name != "cursors.py"
        for name in CRASHING
        if name in path.read_text(encoding="utf-8")
    ]
    assert offenders == []


def test_the_safe_shapes_are_not_the_crashing_ones() -> None:
    from ui.cursors import MOVE_CURSOR, WAIT_CURSOR

    assert MOVE_CURSOR.name == "OpenHandCursor"
    assert WAIT_CURSOR.name in ("BusyCursor", "WaitCursor")
