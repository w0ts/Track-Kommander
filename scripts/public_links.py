"""Unlink dead relative links in the Markdown files of the public tree.

    python3 scripts/public_links.py <tree-root>

The public, binary-only repo is assembled by scripts/publish_public.sh from a
few files of this repo. Some of them link into src/ or the build scripts,
which are not published. This pass walks every *.md under <tree-root> and
turns each relative link whose target does not exist in that tree into its
plain link text: ``[`core/anlz.py`](../src/core/anlz.py)`` becomes
```core/anlz.py```. Links with a URL scheme, anchors, mailto and images are
left alone. Standard library only — it runs on a bare runner.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Callable
from pathlib import Path

# [text](target) — not preceded by "!" (an image), text without nested
# brackets, target without spaces or parentheses.
_LINK = re.compile(r"(?<!!)\[([^\[\]]+)\]\(([^()\s]+)\)")
_SCHEME = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")


def _is_relative_path(target: str) -> bool:
    return not (target.startswith("#") or _SCHEME.match(target))


def unlink_missing(markdown: str, exists: Callable[[str], bool]) -> str:
    """Replace ``[text](target)`` with ``text`` when *exists(path)* is false.

    *exists* receives the target with any ``#fragment`` removed. Only
    relative paths are ever queried; URLs, anchors and images pass through.
    """

    def replace(match: re.Match[str]) -> str:
        text, target = match.group(1), match.group(2)
        if not _is_relative_path(target):
            return match.group(0)
        path = target.split("#", 1)[0]
        return match.group(0) if exists(path) else text

    return _LINK.sub(replace, markdown)


def _exists_in(root: Path, base: Path) -> Callable[[str], bool]:
    root = root.resolve()

    def exists(path: str) -> bool:
        candidate = (base / path).resolve()
        return candidate.is_relative_to(root) and candidate.exists()

    return exists


def rewrite_tree(root: Path) -> list[Path]:
    """Rewrite every ``*.md`` under *root*; return the files that changed."""
    changed: list[Path] = []
    for md in sorted(root.rglob("*.md")):
        before = md.read_text(encoding="utf-8")
        after = unlink_missing(before, _exists_in(root, md.parent))
        if after != before:
            md.write_text(after, encoding="utf-8")
            changed.append(md)
    return changed


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__.strip().splitlines()[2].strip(), file=sys.stderr)
        return 2
    root = Path(argv[1])
    if not root.is_dir():
        print(f"public_links: not a directory: {root}", file=sys.stderr)
        return 2
    for path in rewrite_tree(root):
        print(f"unlinked dead links in {path.relative_to(root)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
