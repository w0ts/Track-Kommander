"""scripts/check_libtk.py, the check the release build runs (release.yml).

The build itself needs a Mac; what it checks against is plain source, so it is tested here
on every commit: the export list it expects, the Python side calling only what libtk
exports, and the private-name filter.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

from core.extensions import ADDON_MODULE

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("check_libtk", ROOT / "scripts" / "check_libtk.py")
assert _spec is not None and _spec.loader is not None
check_libtk = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_libtk)

#: How TK's Python reaches a libtk function: ``lib.tk_x``, ``getattr(lib, "tk_x")``,
#: ``process_symbol("tk_x")`` and the like.
_PY_CALL = re.compile(r"""(?:\.|["'])(tk_[a-z0-9_]+)\b""")


def _python_calls() -> set[str]:
    names: set[str] = set()
    for path in (ROOT / "src").rglob("*.py"):
        names.update(_PY_CALL.findall(path.read_text(encoding="utf-8")))
    return names - {ADDON_MODULE}  # a module name, not a libtk symbol


def test_the_sources_export_what_the_band_and_columns_use() -> None:
    exports = check_libtk.declared_exports()
    assert {"tk_band_icon", "tk_band_set_tooltip", "tk_cols_set_values", "tk_load_path"} <= exports
    assert len(exports) >= 30


def test_tk_calls_only_what_libtk_exports() -> None:
    assert _python_calls() - check_libtk.declared_exports() == set()


def test_the_export_pattern_reads_both_spellings() -> None:
    src = (
        "TK_EXPORT int tk_a(const char* p) {\n"
        'extern "C" __attribute__((visibility("default"))) void tk_b(void (*cb)(void)) {\n'
        "static int tk_hidden(int x) {\n"
    )
    assert set(check_libtk._EXPORT.findall(src)) == {"tk_a", "tk_b"}


def test_a_line_from_frida_is_skipped_any_other_fails() -> None:
    names = re.compile(r"secret", re.IGNORECASE)
    lines = ["xxSECRETxx frida table", "made by tk: secret"]
    assert check_libtk.private_hits(lines, names, set()) == ["SECRET", "secret"]
    assert check_libtk.private_hits(lines, names, {"xxSECRETxx frida table"}) == ["secret"]
