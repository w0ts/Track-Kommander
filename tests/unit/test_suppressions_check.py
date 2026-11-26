"""The suppression ratchet: the tree may lose a lint or type suppression,
never gain one. A silenced finding is where a real bug hides."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "checks" / "suppressions.py"
_spec = importlib.util.spec_from_file_location("suppressions", _SCRIPT)
assert _spec is not None and _spec.loader is not None
check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check)


def test_every_kind_of_suppression_is_found(tmp_path) -> None:
    (tmp_path / "a.py").write_text(
        "import os  # noqa: F401\n"
        "x: int = 'no'  # type: ignore[assignment]\n"
        "y = z  # pyright: ignore\n"
        "fine = 1\n",
        encoding="utf-8",
    )
    found = check.find([tmp_path])
    assert [(p.name, line) for p, line, _text in found] == [("a.py", 1), ("a.py", 2), ("a.py", 3)]


def test_a_comment_that_merely_mentions_the_word_is_not_one(tmp_path) -> None:
    (tmp_path / "a.py").write_text("# never add a noqa here\nx = 1\n", encoding="utf-8")
    assert check.find([tmp_path]) == []


def test_only_python_files_count(tmp_path) -> None:
    (tmp_path / "notes.md").write_text("# noqa\n", encoding="utf-8")
    assert check.find([tmp_path]) == []


def test_more_than_allowed_fails() -> None:
    ok, message = check.verdict(found=26, allowed=25)
    assert ok is False
    assert "26" in message and "25" in message


def test_the_allowed_count_passes() -> None:
    ok, _message = check.verdict(found=25, allowed=25)
    assert ok is True


def test_fewer_than_allowed_passes_and_says_to_lower_the_limit() -> None:
    ok, message = check.verdict(found=20, allowed=25)
    assert ok is True
    assert "20" in message and "ALLOWED" in message


def test_the_limit_matches_the_tree_today() -> None:
    """The ratchet only means something if the number in the script is the
    real one: a limit above the count lets suppressions creep back in."""
    root = Path(__file__).resolve().parents[2]
    found = check.find([root / "src", root / "tests", root / "scripts"])
    assert len(found) == check.ALLOWED, (
        f"{len(found)} suppressions in the tree, ALLOWED is {check.ALLOWED}: "
        "set ALLOWED to the count (lower is the point; higher needs a reason)"
    )
