"""scripts/public_site.py — SITE.md rendered as the public site.

The pages at https://w0ts.github.io/Track-Kommander/ (About, Install, Matching,
Export) are generated from the public README on every release, so the README
stays the one source.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _load():
    spec = importlib.util.spec_from_file_location(
        "public_site", REPO / "scripts" / "public_site.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["public_site"] = module  # dataclasses looks its module up here
    spec.loader.exec_module(module)
    return module


public_site = _load()

README = """<!--
  a private note that must not reach the page
-->
# Track Kommander

**A companion**

![shot](docs/screenshot.png)

---

## What it does

See [the export notes](docs/rekordbox-export.md) and [CLAP](https://example.org/clap).

| A | B |
|---|---|
| 1 | 2 |

---

## Install

Text.

---

## First run

Text.

---

## Troubleshooting

Text.

---

## Contributing

Text.

---

## Licence and attribution

Text.

---

## Matching and loading

Text.

---

## Exporting to Rekordbox and CDJs

Text.
"""


def test_title_goes_to_the_titlebar_and_the_comment_is_dropped() -> None:
    page = public_site.build_page(README)
    assert (
        '<div class="titlebar"><h1><a href="https://w0ts.github.io/">w0ts</a> / Track Kommander</h1></div>'
        in page
    )
    assert "<title>w0ts / Track Kommander</title>" in page
    assert "private note" not in page
    assert page.count("<h1") == 1


def test_each_h2_part_is_a_section_with_github_anchor() -> None:
    page = public_site.build_page(README)
    assert '<section id="what-it-does">' in page
    assert '<section id="install">' in _page("install.html")
    assert "<hr" not in page


def test_intro_is_a_box_like_every_part() -> None:
    page = public_site.build_page(README)
    assert '<section class="intro">' in page
    assert '<div class="intro">' not in page


def test_each_h3_part_is_an_inset_card_inside_its_section() -> None:
    parts = public_site.render_parts(
        "intro\n\n## Part\n\nlead\n\n### Sub one\n\na\n\n#### Deep\n\nd\n\n### Sub two\n\nb\n"
    )
    part = parts["part"]
    assert part.startswith('<section id="part">') and part.endswith("</section>")
    assert part.index("lead") < part.index('<div class="card">')
    assert part.count('<div class="card">') == 2
    assert part.count("</div>") == 2
    first_card = part.split('<div class="card">')[1]
    assert '<h3 id="sub-one">' in first_card and '<h4 id="deep">' in first_card


def test_relative_links_point_at_github_but_images_stay_relative() -> None:
    page = public_site.build_page(README)
    assert (
        'href="https://github.com/w0ts/Track-Kommander/blob/main/docs/rekordbox-export.md"' in page
    )
    assert 'href="https://example.org/clap"' in page
    assert 'src="docs/screenshot.png"' in page


def test_tables_scroll_and_shared_style_is_loaded() -> None:
    page = public_site.build_page(README)
    assert '<div class="table-wrap"><table>' in page
    assert "https://w0ts.github.io/site-style/common.css" in page
    assert "--accent: #2b8cf5" in page


def _page(name: str, text: str = README) -> str:
    page = next(p for p in public_site.PAGES if p.file == name)
    return public_site.build_page(text, page)


def test_real_public_readme_fills_every_page() -> None:
    text = (REPO / "SITE.md").read_text(encoding="utf-8")
    expected = {
        "index.html": ("what-it-does", "contributing", "licence-and-attribution"),
        "install.html": ("install", "first-run", "troubleshooting"),
        "matching.html": ("matching-and-loading",),
        "export.html": ("exporting-to-rekordbox-and-cdjs",),
    }
    for name, anchors in expected.items():
        page = _page(name, text)
        for anchor in anchors:
            assert f'<section id="{anchor}">' in page, (name, anchor)


def test_tabs_are_real_pages_in_order_github_last_no_issues() -> None:
    for name in ("index.html", "install.html", "matching.html", "export.html"):
        page = _page(name)
        nav = page[page.index('<nav class="site"') : page.index("</nav>")]
        hrefs = re.findall(r'href="([^"]+)"', nav)
        assert hrefs == [
            "./",
            "install.html",
            "matching.html",
            "export.html",
            "https://github.com/w0ts/Track-Kommander",
        ]
        assert 'href="#' not in nav
        assert "issues" not in page.lower().split("</nav>")[0]
        assert nav.count('aria-current="page"') == 1


def test_current_tab_and_title_follow_the_page() -> None:
    assert '<a href="install.html" aria-current="page">Install</a>' in _page("install.html")
    assert '<a href="./" aria-current="page">About</a>' in _page("index.html")
    assert "<title>w0ts / Track Kommander / Export</title>" in _page("export.html")
    assert _page("matching.html").count("<h1") == 1


def test_download_buttons_only_on_install() -> None:
    assert 'class="buttons"' in _page("install.html")
    for name in ("index.html", "matching.html", "export.html"):
        assert 'class="buttons"' not in _page(name)


def test_a_readme_part_on_no_page_is_an_error() -> None:
    import pytest

    with pytest.raises(ValueError):
        public_site.build_page(README + "\n---\n\n## Surprise\n\nText.\n")


def test_main_writes_index_and_nojekyll(tmp_path: Path) -> None:
    (tmp_path / "SITE.md").write_text(README, encoding="utf-8")
    assert public_site.main(["public_site.py", str(tmp_path)]) == 0
    assert not (tmp_path / "SITE.md").exists()  # the user text is on the site only
    for name in ("index.html", "install.html", "matching.html", "export.html"):
        assert (tmp_path / name).read_text(encoding="utf-8").startswith("<!doctype html>")
    assert (tmp_path / ".nojekyll").exists()


def test_every_page_loads_the_w0ts_project_switcher() -> None:
    for name in ("index.html", "install.html", "matching.html", "export.html"):
        page = _page(name)
        head = page[: page.index("</head>")]
        assert (
            '<script src="https://w0ts.github.io/site-style/switcher.js" defer></script>' in head
        ), name


def test_install_shows_one_tab_per_system_the_first_open() -> None:
    """User, 2026-11-26: "one page with two tabs" (macOS, Windows); no script needed."""
    from public_site import PAGES, build_page

    page = build_page((REPO / "SITE.md").read_text("utf-8"), PAGES[1])
    assert '<input type="radio" name="install-tab" id="install-macos" checked>' in page
    assert '<input type="radio" name="install-tab" id="install-windows">' in page
    assert page.count('class="card tab-panel"') == 2
    assert '#install-windows:checked ~ [data-tab="install-windows"]' in page
