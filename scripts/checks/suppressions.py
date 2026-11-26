"""Fail when the tree gains a lint or type suppression.

A ``# noqa``, ``# type: ignore`` or ``# pyright: ignore`` is a finding nobody
fixed. The ones that are here are each deliberate and reported by ruff as
used (RUF100); this keeps their number from growing. When one goes, lower
ALLOWED to the new count — the limit only ever moves down.

Run by ``make check``; ``python scripts/checks/suppressions.py`` prints every
one it finds with ``--list``.
"""

from __future__ import annotations

import re
import sys
import tokenize
from pathlib import Path

#: How many the tree holds today. Lower it when one is removed.
ALLOWED = 33

ROOT = Path(__file__).resolve().parents[2]
CHECKED = ("src", "tests", "scripts")

_SUPPRESSION = re.compile(r"#\s*(?:noqa\b|type:\s*ignore|pyright:\s*ignore)")


def find(dirs: list[Path]) -> list[tuple[Path, int, str]]:
    """Every suppression comment under *dirs*, as (file, line number, line).

    Comments only, by the tokenizer: the same words inside a string or a
    docstring — a test's fixture, this file's own explanation — are not
    suppressions.
    """
    found: list[tuple[Path, int, str]] = []
    for base in dirs:
        for path in sorted(base.rglob("*.py")):
            found.extend((path, number, text) for number, text in _suppression_comments(path))
    return found


def _suppression_comments(path: Path) -> list[tuple[int, str]]:
    with path.open("rb") as source:
        tokens = tokenize.tokenize(source.readline)
        return [
            (token.start[0], token.line.strip())
            for token in tokens
            if token.type == tokenize.COMMENT and _SUPPRESSION.search(token.string)
        ]


def verdict(found: int, allowed: int) -> tuple[bool, str]:
    """Whether *found* suppressions are within *allowed*, and what to say."""
    if found > allowed:
        return False, (
            f"{found} suppressions, {allowed} allowed: fix the finding instead of "
            "silencing it (see scripts/checks/suppressions.py)"
        )
    if found < allowed:
        return True, (
            f"{found} suppressions, {allowed} allowed: lower ALLOWED in "
            f"scripts/checks/suppressions.py to {found} so it cannot creep back"
        )
    return True, f"{found} suppressions, none new"


def main(argv: list[str]) -> int:
    found = find([ROOT / d for d in CHECKED])
    ok, message = verdict(len(found), ALLOWED)
    if "--list" in argv or not ok:
        for path, number, text in found:
            print(f"{path.relative_to(ROOT)}:{number}: {text}")
    print(message)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
