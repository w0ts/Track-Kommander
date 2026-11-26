"""Check a built libtk.dylib before it is published (scripts/build_libtk.sh runs this).

    python3 scripts/check_libtk.py traktor-native/build/libtk.dylib [--devkit libfrida-gum.a]

Fails (exit 1) unless:
  * it is an arm64 library signed ad-hoc only: the embedded version carries no
    Developer ID (the User's ruling, 2026-11-25);
  * ``nm -gU`` lists every ``tk_*`` function the sources export (``TK_EXPORT`` or
    ``visibility("default")`` in traktor-native/src), the ones TK calls through ctypes;
  * ``strings`` finds none of the private add-on's names. The word list is the one
    tests/unit/test_addon_free.py holds for TK's sources, read from there so the two
    cannot drift. Lines found verbatim in the frida-gum devkit (--devkit) are frida's
    binary tables, not TK's text, and are skipped (see check_strings).

Stdlib only, Python 3.9 (the build Mac's system python3 runs it).
"""

from __future__ import annotations

import argparse
import importlib.util
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NATIVE_SRC = ROOT / "traktor-native" / "src"
ADDON_WORDS_TEST = ROOT / "tests" / "unit" / "test_addon_free.py"

#: A tk_* function given default visibility: through the TK_EXPORT macro, or spelled out.
_EXPORT = re.compile(
    r'(?:\bTK_EXPORT|extern\s+"C"\s+__attribute__\s*\(\(\s*visibility\s*\(\s*"default"\s*\)\s*\)\))'
    r"[^;{(]*?\b(tk_\w+)\s*\("
)


def declared_exports(src_dir: Path = NATIVE_SRC) -> set[str]:
    """Every tk_* function the libtk sources export, without the leading underscore."""
    names: set[str] = set()
    for path in sorted(src_dir.glob("*")):
        if path.suffix in {".mm", ".h", ".c", ".cpp"}:
            names.update(_EXPORT.findall(path.read_text(encoding="utf-8", errors="replace")))
    return names


def addon_names() -> re.Pattern[str] | None:
    """The private names, as tests/unit/test_addon_free.py defines them (its NAMES); None in a
    tree without that file (the public one: it has no private names to keep out)."""
    if not ADDON_WORDS_TEST.is_file():
        return None
    spec = importlib.util.spec_from_file_location("_addon_words", ADDON_WORDS_TEST)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {ADDON_WORDS_TEST}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    names = getattr(module, "NAMES", None)
    if not isinstance(names, re.Pattern):
        raise RuntimeError(f"{ADDON_WORDS_TEST} has no NAMES pattern")
    return names


def _run(*cmd: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, errors="replace", check=False)


def exported_symbols(dylib: Path) -> set[str]:
    """tk_* functions the library exports (nm -gU, text section), without the underscore."""
    result = _run("nm", "-gU", str(dylib))
    if result.returncode != 0:
        raise RuntimeError(f"nm failed: {result.stderr.strip()}")
    names = set()
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[1] == "T" and parts[2].startswith("_tk_"):
            names.add(parts[2][1:])
    return names


def check_signature(dylib: Path) -> list[str]:
    result = _run("codesign", "-dv", str(dylib))
    info = result.stderr  # codesign -d writes to stderr
    print(
        *(
            f"  {line}"
            for line in info.splitlines()
            if line.startswith(("Signature=", "TeamIdentifier=", "Authority="))
        ),
        sep="\n",
    )
    problems = []
    if result.returncode != 0:
        problems.append(f"codesign -dv failed: {info.strip()}")
    if "Signature=adhoc" not in info.splitlines():
        problems.append("not signed ad-hoc (no 'Signature=adhoc')")
    if any(line.startswith("Authority=") for line in info.splitlines()):
        problems.append("carries a signing authority: the embedded version is ad-hoc only")
    return problems


def check_arch(dylib: Path) -> list[str]:
    result = _run("lipo", "-archs", str(dylib))
    archs = result.stdout.split()
    print(f"  archs: {' '.join(archs) or '?'}")
    return [] if "arm64" in archs else [f"no arm64 slice (lipo -archs: {result.stdout.strip()})"]


def check_exports(dylib: Path) -> list[str]:
    expected = declared_exports()
    if not expected:
        return [f'found no TK_EXPORT / visibility("default") tk_* functions in {NATIVE_SRC}']
    have = exported_symbols(dylib)
    print(f"  {len(have & expected)}/{len(expected)} exports: {' '.join(sorted(have & expected))}")
    missing = sorted(expected - have)
    return [f"missing exports: {' '.join(missing)}"] if missing else []


def _strings(path: Path) -> list[str]:
    result = _run("strings", "-a", str(path))
    if result.returncode != 0:
        raise RuntimeError(f"strings {path.name} failed: {result.stderr.strip()}")
    return result.stdout.splitlines()


def private_hits(lines: list[str], names: re.Pattern[str], baseline: set[str]) -> list[str]:
    """The private names in <lines>, skipping lines that occur verbatim in <baseline>."""
    return sorted(
        {m.group(0) for line in lines if line not in baseline for m in names.finditer(line)}
    )


def check_strings(dylib: Path, devkit: Path | None) -> list[str]:
    """No private name in the library's strings.

    frida-gum's static library holds binary tables that `strings` reads as text, and a
    few of those spell a short name by chance (in 17.27.4 two lines hold the four
    letters of the add-on's protocol). A line found verbatim in the devkit's own
    libfrida-gum.a came from frida, not from TK, so it does not count; any other hit
    fails the build.
    """
    names = addon_names()
    if names is None:
        print("  private names: no list in this tree, nothing to check")
        return []
    lines = _strings(dylib)
    hits = private_hits(lines, names, set())
    if hits and devkit is not None:
        from_frida = set(_strings(devkit))
        skipped = len(hits)
        hits = private_hits(lines, names, from_frida)
        print(
            f"  {skipped - len(hits)} chance match(es) skipped: identical lines are in {devkit.name}"
        )
    print(f"  private names found: {', '.join(hits) if hits else 'none'}")
    return [f"names something private: {', '.join(hits)}"] if hits else []


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Check a built libtk.dylib.")
    parser.add_argument("dylib", type=Path)
    parser.add_argument(
        "--devkit",
        type=Path,
        help="the libfrida-gum.a it was linked with (its strings are frida's, not TK's)",
    )
    args = parser.parse_args(argv[1:])
    dylib: Path = args.dylib
    for path in (dylib, args.devkit):
        if path is not None and not path.is_file():
            print(f"check_libtk: no such file: {path}", file=sys.stderr)
            return 2
    problems: list[str] = []
    for title, check in (
        ("architecture", check_arch),
        ("signature", check_signature),
        ("exports", check_exports),
        ("strings", lambda d: check_strings(d, args.devkit)),
    ):
        print(f"→ {title}")
        problems += check(dylib)
    for problem in problems:
        print(f"check_libtk: FAIL {problem}", file=sys.stderr)
    if not problems:
        print(f"check_libtk: {dylib.name} ok")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
