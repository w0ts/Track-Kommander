"""scripts/public_links.py — the publish step's dead-link remover.

The public, binary-only repo receives a handful of Markdown files copied
from here. Some of them link into src/ or the build scripts, which do not
exist publicly. Instead of maintaining public variants, the publish step
unlinks every relative link whose target is missing from the public tree,
keeping the link text so the sentence still reads.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _load():
    spec = importlib.util.spec_from_file_location(
        "public_links", REPO / "scripts" / "public_links.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


public_links = _load()


def _exists_only(*present: str):
    return lambda target: target in present


def test_relative_link_to_missing_target_is_unlinked() -> None:
    text = "see [`core/anlz.py`](../src/core/anlz.py) for the format"
    out = public_links.unlink_missing(text, _exists_only())
    assert out == "see `core/anlz.py` for the format"


def test_relative_link_to_present_target_is_kept() -> None:
    text = "see [the audit](../THIRD_PARTY.md) first"
    out = public_links.unlink_missing(text, _exists_only("../THIRD_PARTY.md"))
    assert out == text


def test_fragment_is_ignored_when_resolving() -> None:
    text = "[MP3 timing](../THIRD_PARTY.md#mp3-timing)"
    out = public_links.unlink_missing(text, _exists_only("../THIRD_PARTY.md"))
    assert out == text


def test_absolute_urls_anchors_and_mailto_are_untouched() -> None:
    text = (
        "[a](https://example.com/x) [b](http://e.com) [c](#the-blank-exportpdb) [d](mailto:x@y.z)"
    )
    assert public_links.unlink_missing(text, _exists_only()) == text


def test_images_are_left_alone() -> None:
    text = "![Track Kommander](docs/screenshot.png)"
    assert public_links.unlink_missing(text, _exists_only()) == text


def test_several_links_on_one_line() -> None:
    text = "| x | [`a.py`](../src/a.py) | [ok](../LICENSE) | [`b.py`](../src/b.py) |"
    out = public_links.unlink_missing(text, _exists_only("../LICENSE"))
    assert out == "| x | `a.py` | [ok](../LICENSE) | `b.py` |"


def test_rewrite_tree_resolves_against_each_file_and_stays_inside_root(
    tmp_path: Path,
) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "THIRD_PARTY.md").write_text("# audit\n", encoding="utf-8")
    doc = tmp_path / "docs" / "guide.md"
    doc.write_text(
        "[kept](../THIRD_PARTY.md) [gone](../src/core/x.py) "
        "[escape](../../../../etc/passwd) [self](guide.md)\n",
        encoding="utf-8",
    )
    untouched = tmp_path / "README.md"
    untouched.write_text("[kept](THIRD_PARTY.md)\n", encoding="utf-8")

    changed = public_links.rewrite_tree(tmp_path)

    assert changed == [doc]
    assert doc.read_text(encoding="utf-8") == (
        "[kept](../THIRD_PARTY.md) gone escape [self](guide.md)\n"
    )
    assert untouched.read_text(encoding="utf-8") == "[kept](THIRD_PARTY.md)\n"
