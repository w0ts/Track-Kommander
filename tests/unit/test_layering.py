"""The layers only point one way: ``ui`` and ``workers`` use ``core``.

``core`` holds the domain logic and file formats. If it reaches up into a Qt
widget module or a worker thread, it can no longer be imported, tested or run
headless without them, and the two ends start to need each other. This reads
the source rather than importing it, so an import hidden inside a function —
which never runs at import time — is caught as well.
"""

from __future__ import annotations

import ast
from pathlib import Path

CORE = Path(__file__).resolve().parents[2] / "src" / "core"

#: Packages ``core`` must not import from, at module level or in a function.
FORBIDDEN = ("ui", "workers")


def _imported_top_levels(tree: ast.AST) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend((node.lineno, alias.name.split(".")[0]) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.append((node.lineno, node.module.split(".")[0]))
    return found


def _violations() -> list[str]:
    out: list[str] = []
    for path in sorted(CORE.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        out.extend(
            f"{path.relative_to(CORE.parent)}:{line} imports {top}"
            for line, top in _imported_top_levels(tree)
            if top in FORBIDDEN
        )
    return out


def test_core_does_not_import_ui_or_workers_anywhere_in_a_file():
    assert _violations() == []


def test_the_scan_finds_a_function_level_import_of_ui():
    source = "def f():\n    from ui.physical_keys import native_code\n"
    assert _imported_top_levels(ast.parse(source)) == [(2, "ui")]


def test_the_scan_finds_a_plain_import_of_workers():
    assert _imported_top_levels(ast.parse("import workers.hotcue_worker")) == [(1, "workers")]


def test_the_scan_ignores_core_modules_that_merely_contain_ui_in_the_name():
    source = "from core.ui_helpers import x\nimport uinput\n"
    assert _imported_top_levels(ast.parse(source)) == [(1, "core"), (2, "uinput")]
