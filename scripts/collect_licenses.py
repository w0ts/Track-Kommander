"""Collect the licence text of every runtime dependency that ships in the app.

Writes ``licenses/THIRD_PARTY_NOTICES.txt`` — the notice file that travels
inside the binaries. MIT, BSD, Apache-2.0, MPL and the LGPLs all require their
copyright and permission notices to accompany a binary distribution; this is
that file. The LGPL/GPL full texts ship separately as ``licenses/*.txt``
because they are long and shared.

Two facts decide what goes in, and both are read from the machine rather than
from a hand-kept list:

* **which** packages — the runtime closure of ``[project].dependencies`` in
  ``pyproject.toml``, walked through each installed distribution's
  ``Requires-Dist`` with environment markers evaluated for this platform.
  Build-only extras (nuitka, torch, transformers, dmgbuild, …) are therefore
  excluded automatically: nothing they contain reaches the shipped binary.
* **what text** — every licence file recorded in that distribution's
  ``*.dist-info`` metadata, read from disk.

Usage (after ``make venv``):

    .venv/bin/python scripts/collect_licenses.py            # write
    .venv/bin/python scripts/collect_licenses.py --check    # CI guard

Re-run it after any dependency bump and commit the result — the file is
checked in so a build never has to reach into site-packages, and so a diff
shows up in review when a dependency's licence changes.

Platform note: the closure is resolved for the interpreter running the script,
so a macOS run lists pyobjc and a Windows run lists onnxruntime-directml. Run
it on both and commit the union if you want one file to cover both binaries —
today the macOS run is the committed one, and the Windows-only difference is
recorded in THIRD_PARTY.md.
"""

from __future__ import annotations

import argparse
import sys
import tomllib
from importlib import metadata
from pathlib import Path

try:
    from packaging.requirements import Requirement
except ModuleNotFoundError:  # pragma: no cover - dev-tool guard
    sys.exit("packaging is not installed — run `make venv` first")

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "licenses" / "THIRD_PARTY_NOTICES.txt"

#: This project itself — covered by the root LICENSE, not a third-party notice.
SELF = "track-kommander"

#: Licence files carry these names, either in ``*.dist-info`` or at the top of
#: the installed package itself (onnxruntime ships both a LICENSE and a
#: ThirdPartyNotices.txt that way, covering what is linked into its dylib).
LICENSE_NAMES = ("LICENSE", "LICENCE", "COPYING", "NOTICE", "THIRDPARTYNOTICES")

#: Packages whose wheels record no licence text at all. The note is printed in
#: place of the missing text; nothing here is invented — each one is the
#: licence the package's own metadata declares, pointed at a text we do ship
#: or at the publisher. Verify before adding a line.
NO_TEXT_NOTES = {
    "pyside6": "Taken under the LGPL-3.0 option of Qt for Python's tri-licence."
    " Full text: licenses/LGPL-3.0.txt (read together with"
    " licenses/GPL-3.0.txt, which LGPL-3.0 extends).",
    "pyside6-essentials": "Same terms as PySide6 — licenses/LGPL-3.0.txt.",
    "pyside6-addons": "Same terms as PySide6 — licenses/LGPL-3.0.txt.",
    "shiboken6": "Same terms as PySide6 — licenses/LGPL-3.0.txt.",
}


def _canon(name: str) -> str:
    return name.lower().replace("_", "-").replace(".", "-")


def _seed_requirements() -> list[str]:
    """Runtime dependency names declared in pyproject (no extras)."""
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return [Requirement(dep).name for dep in data["project"]["dependencies"]]


def _runtime_closure() -> list[metadata.Distribution]:
    """Every installed distribution reachable from the runtime dependencies."""
    seen: set[str] = set()
    found: dict[str, metadata.Distribution] = {}
    queue = [_canon(n) for n in _seed_requirements()]

    while queue:
        name = queue.pop()
        if name in seen or name == SELF:
            continue
        seen.add(name)
        try:
            dist = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            # Platform-conditional dependency not installed here (e.g.
            # onnxruntime-directml on macOS). Nothing to notice.
            continue
        found[name] = dist
        for raw in dist.requires or ():
            req = Requirement(raw)
            # Markers are evaluated with no extra active: an "extra ==" guard
            # means the dependency belongs to an optional feature we do not
            # install, so it never reaches the binary.
            if req.marker and not req.marker.evaluate({"extra": ""}):
                continue
            queue.append(_canon(req.name))

    return sorted(found.values(), key=lambda d: _canon(d.metadata["Name"]))


def _is_license_file(path: str) -> bool:
    parts = Path(path).parts
    if "_vendor" in parts or "__pycache__" in parts:
        return False
    stem = Path(path).name.upper()
    if not any(stem.startswith(n) for n in LICENSE_NAMES):
        return False
    if any(p.endswith(".dist-info") for p in parts):
        return True
    # Inside the installed package: only the top of the tree, so a test
    # fixture or an example's licence deep in the tree is not swept up.
    return len(parts) <= 2


def _license_texts(dist: metadata.Distribution) -> list[tuple[str, str]]:
    """(relative filename, text) for every licence file the package records."""
    out: list[tuple[str, str]] = []
    for entry in dist.files or ():
        path = str(entry)
        if not _is_license_file(path):
            continue
        located = entry.locate()
        try:
            text = located.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        # Label with the path below dist-info so numpy's vendored notices stay
        # distinguishable from its own LICENSE.txt.
        label = path.split(".dist-info/", 1)[-1] if ".dist-info/" in path else path
        out.append((label, text.strip()))
    return sorted(out)


def _declared_license(dist: metadata.Distribution) -> str:
    meta = dist.metadata
    expr = meta.get("License-Expression")
    if expr:
        return expr
    classifiers = [
        c.split("::")[-1].strip()
        for c in meta.get_all("Classifier") or []
        if c.startswith("License ::")
    ]
    if classifiers:
        return "; ".join(classifiers)
    legacy = (meta.get("License") or "").strip()
    if legacy and "\n" not in legacy and len(legacy) < 80:
        return legacy
    return "see text below"


def _project_url(dist: metadata.Distribution) -> str:
    for entry in dist.metadata.get_all("Project-URL") or []:
        if entry.lower().startswith(("homepage,", "repository,", "source,")):
            return entry.split(",", 1)[1].strip()
    return dist.metadata.get("Home-page") or ""


def render() -> str:
    lines: list[str] = [
        "THIRD-PARTY NOTICES — Track Kommander",
        "=" * 72,
        "",
        "Track Kommander itself is free software under the GNU GPL, version 3;",
        "see licenses/LICENSE.txt. The components listed below",
        "are redistributed with it under their own terms, reproduced in full.",
        "The GNU LGPL texts referenced by some of them are shipped separately",
        "as licenses/LGPL-3.0.txt and licenses/LGPL-2.1.txt.",
        "",
        "Generated from the installed runtime dependency closure by",
        "scripts/collect_licenses.py — do not edit by hand. THIRD_PARTY.md",
        "explains what each licence means for redistribution.",
        "",
    ]

    for dist in _runtime_closure():
        lines += [
            "",
            "=" * 72,
            f"{dist.metadata['Name']} {dist.metadata['Version']}",
            f"Licence: {_declared_license(dist)}",
        ]
        url = _project_url(dist)
        if url:
            lines.append(f"Project: {url}")
        lines.append("=" * 72)

        texts = _license_texts(dist)
        if texts:
            for filename, text in texts:
                lines += ["", f"--- {filename} ---", "", text]
        else:
            note = NO_TEXT_NOTES.get(_canon(dist.metadata["Name"]))
            lines += [
                "",
                "This distribution ships no licence file of its own.",
                note
                or (
                    "The licence named above is the one it declares; the"
                    " full text is available from the project page."
                ),
            ]
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description="Regenerate licenses/THIRD_PARTY_NOTICES.txt")
    ap.add_argument(
        "--check", action="store_true", help="exit 1 if the checked-in file is out of date"
    )
    args = ap.parse_args()

    content = render()
    if args.check:
        current = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
        if current != content:
            print(
                f"✗ {OUT.relative_to(ROOT)} is out of date — re-run scripts/collect_licenses.py",
                file=sys.stderr,
            )
            return 1
        print(f"✓ {OUT.relative_to(ROOT)} is up to date")
        return 0

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(content, encoding="utf-8")
    print(f"✓ {OUT.relative_to(ROOT)}  ({len(content.encode()) / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
