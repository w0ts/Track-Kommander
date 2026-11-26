"""Build the public site's pages from site/SITE.md.

    python scripts/public_site.py <tree-root>

`make site` copies site/SITE.md to <tree-root> (this repo's root) and runs this. It
renders that SITE.md into <tree-root>/index.html and its sibling pages, which GitHub Pages serves at
https://w0ts.github.io/Track-Kommander/, then removes SITE.md from the tree: the
public repo's README is for developers, the user text is only on the site.
SITE.md stays the one source: the pages are never edited by hand.

The site is four real pages, in this tab order: About (index.html), Install,
Matching, Export; the GitHub icon closes the row. PAGES says which "## " part of
the README goes on which page; a part that is on none of them is an error, so a
new README section cannot silently disappear.

The look is the one shared by the w0ts project sites: common.css from
w0ts/site-style, loaded by URL, with only the accent (Traktor blue) set here.
Every part is a box, as on the other w0ts sites (Jank): the intro and each
"## " part become a <section>, and each "### " part an inset .card inside its
section. The horizontal rules between parts are dropped; relative links to Markdown and licence files point at
GitHub, where they render, while images stay relative (Pages serves them).
"""

from __future__ import annotations

import html
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from string import Template

from markdown_it import MarkdownIt
from markdown_it.token import Token

PUBLIC_REPO_URL = "https://github.com/w0ts/Track-Kommander"
BLOB_URL = f"{PUBLIC_REPO_URL}/blob/main/"
SITES_ROOT = "https://w0ts.github.io/"
STYLE_URL = "https://w0ts.github.io/site-style/common.css"
# the w0ts project switcher after the title
SWITCHER_URL = "https://w0ts.github.io/site-style/switcher.js"
SOURCE = "SITE.md"  # in the public tree (copied there from site/SITE.md): the user text; rendered into the pages, then left out of the public tree

_COMMENT = re.compile(r"<!--.*?-->\s*", re.DOTALL)
_SCHEME = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")
_SLUG_DROP = re.compile(r"[^\w\- ]", re.UNICODE)


def slugify(text: str) -> str:
    """GitHub's heading anchor: lower case, punctuation dropped, spaces to -."""
    return _SLUG_DROP.sub("", text.strip().lower()).replace(" ", "-")


def is_relative(target: str) -> bool:
    return bool(target) and not (target.startswith("#") or _SCHEME.match(target))


def split_title(markdown: str) -> tuple[str, str]:
    """Drop the leading HTML comment; return the "# " title and the rest."""
    text = _COMMENT.sub("", markdown, count=1).lstrip()
    first, _, rest = text.partition("\n")
    if not first.startswith("# "):
        raise ValueError("the README must start with a '# ' title")
    return first[2:].strip(), rest


def _parser() -> MarkdownIt:
    md = MarkdownIt("commonmark", {"html": False}).enable("table")
    md.add_render_rule("table_open", lambda *_: '<div class="table-wrap"><table>\n')
    md.add_render_rule("table_close", lambda *_: "</table></div>\n")
    return md


def _fix_links(tokens: list[Token]) -> None:
    """Relative links go to GitHub (a .md renders there, not on Pages)."""
    for token in tokens:
        for child in token.children or []:
            href = child.attrGet("href")
            if child.type == "link_open" and isinstance(href, str) and is_relative(href):
                child.attrSet("href", BLOB_URL + href.removeprefix("./"))


def _add_heading_ids(tokens: list[Token]) -> None:
    for i, token in enumerate(tokens):
        if token.type == "heading_open" and token.tag in ("h2", "h3", "h4"):
            token.attrSet("id", slugify(tokens[i + 1].content))


def _chunks(tokens: list[Token]) -> list[list[Token]]:
    """Split at every h2; the rules between parts are dropped."""
    chunks: list[list[Token]] = [[]]
    for token in tokens:
        if token.type == "hr":
            continue
        if token.type == "heading_open" and token.tag == "h2":
            chunks.append([])
        chunks[-1].append(token)
    return chunks


@dataclass(frozen=True)
class Page:
    """One page of the site: its file, its tab label, the README parts it holds."""

    file: str
    label: str
    parts: tuple[str, ...]
    buttons: bool = False


INTRO = "intro"
PAGES = (
    Page("index.html", "About", (INTRO, "what-it-does", "contributing", "licence-and-attribution")),
    Page("install.html", "Install", ("install", "first-run", "troubleshooting"), buttons=True),
    Page("matching.html", "Matching", ("matching-and-loading",)),
    Page("export.html", "Export", ("exporting-to-rekordbox-and-cdjs",)),
)


def render_parts(markdown: str) -> dict[str, str]:
    """The README (without its title) as HTML per part, keyed by the part's slug."""
    md = _parser()
    tokens = md.parse(markdown)
    _fix_links(tokens)
    _add_heading_ids(tokens)
    intro, *parts = _chunks(tokens)
    out = {INTRO: f'<section class="intro">\n{md.renderer.render(intro, md.options, {})}</section>'}
    for part in parts:
        section_id = str(part[0].attrGet("id"))
        part[0].attrs.pop("id", None)
        out[section_id] = _render_part(md, section_id, part)
    return out


def _split_at_h3(part: list[Token]) -> tuple[list[Token], list[list[Token]]]:
    """A part's lead (its h2 and what precedes the first h3) and its h3 parts."""
    lead: list[Token] = []
    subs: list[list[Token]] = []
    for token in part:
        if token.type == "heading_open" and token.tag == "h3":
            subs.append([])
        (subs[-1] if subs else lead).append(token)
    return lead, subs


#: Parts whose h3 parts are tabs rather than stacked cards: Install, one tab per system (User,
#: 2026-11-26: "one page with two tabs"). The first tab is open; on Windows, the Windows one.
TABBED_PARTS = ("install",)


def _heading_text(sub: list[Token]) -> str:
    return next((t.content for t in sub if t.type == "inline"), "")


def _render_tabs(md: MarkdownIt, section_id: str, subs: list[list[Token]]) -> str:
    """The h3 parts as tabs: radio buttons and CSS only, so they work without JavaScript."""
    group = html.escape(section_id)
    radios, labels, panels = [], [], []
    for i, sub in enumerate(subs):
        name = _heading_text(sub)
        tab_id = f"{group}-{slugify(name)}"
        checked = " checked" if i == 0 else ""
        radios.append(f'<input type="radio" name="{group}-tab" id="{tab_id}"{checked}>')
        labels.append(f'<label for="{tab_id}">{html.escape(name)}</label>')
        body = md.renderer.render(sub[3:], md.options, {})  # without its own h3: the tab says it
        panels.append(f'<div class="card tab-panel" data-tab="{tab_id}">\n{body}</div>')
    return (
        f'<div class="os-tabs">{"".join(radios)}<div class="tabs" role="tablist">'
        f"{''.join(labels)}</div>\n" + "\n".join(panels) + "\n</div>\n"
    )


def _render_part(md: MarkdownIt, section_id: str, part: list[Token]) -> str:
    """One h2 part as a box; each of its h3 parts an inset card inside it (or a tab)."""
    lead, subs = _split_at_h3(part)
    body = md.renderer.render(lead, md.options, {})
    if section_id in TABBED_PARTS and subs:
        body += _render_tabs(md, section_id, subs)
    else:
        body += "".join(
            f'<div class="card">\n{md.renderer.render(sub, md.options, {})}</div>\n' for sub in subs
        )
    return f'<section id="{html.escape(section_id)}">\n{body}</section>'


def check_coverage(parts: dict[str, str]) -> None:
    placed = [slug for page in PAGES for slug in page.parts]
    unplaced = [slug for slug in parts if slug not in placed]
    missing = [slug for slug in placed if slug not in parts]
    if unplaced or missing or len(placed) != len(set(placed)):
        raise ValueError(f"README parts and PAGES disagree: unplaced={unplaced} missing={missing}")


GITHUB_ICON = (
    '<svg viewBox="0 0 16 16" width="20" height="20" aria-hidden="true"><path fill="currentColor" '
    'd="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27s1.36.09 2 .27c1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z"/></svg>'
)


def render_nav(current: Page) -> str:
    """The tab row: one real page per tab, the open one aria-current, GitHub last."""
    tabs = "".join(
        f'<a href="{"./" if page.file == "index.html" else page.file}"'
        + (' aria-current="page"' if page is current else "")
        + f">{page.label}</a>"
        for page in PAGES
    )
    github = f'<a href="{PUBLIC_REPO_URL}" aria-label="GitHub" title="GitHub">{GITHUB_ICON}</a>'
    return f'<nav class="site" aria-label="Track Kommander pages">{tabs}{github}</nav>'


_PAGE = Template("""<!doctype html>
<!-- Generated by scripts/public_site.py from SITE.md: do not edit. -->
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>$page_title</title>
<meta name="description" content="$description">
<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='6' fill='%23171716'/%3E%3Ccircle cx='16' cy='16' r='10' fill='none' stroke='%232b8cf5' stroke-width='3'/%3E%3Ccircle cx='16' cy='16' r='3' fill='%232b8cf5'/%3E%3C/svg%3E">
<link rel="stylesheet" href="$style">
<style>
/* Track Kommander's accent, Traktor blue; common.css does the rest */
:root { --accent: #2b8cf5; --accent-text-l: #1b62c0; --accent-text-d: #3a96ff; }
.intro > p { max-width: 760px; }
.intro img { font-size: var(--fs-3); }
main img { border: 1px solid var(--line); }   /* (its pictures are all screenshots: framed) */
.table-wrap td:first-child { white-space: nowrap; padding-right: var(--sp-3); }
@media (max-width: 560px) { .table-wrap td:first-child { white-space: normal; } }
/* Install: one tab per system (radio buttons, no script needed). The label row is site-style's
   .tabs (a flex row of tab buttons); the container must not be one, or the panel sits beside it. */
.os-tabs > input { position: absolute; opacity: 0; pointer-events: none; }
.os-tabs > .tabs { margin: var(--sp-3) 0 var(--sp-2); }
.os-tabs > .tabs > label { padding: 6px 14px; border-radius: 6px; cursor: pointer; font-weight: 600; }
.tab-panel { display: none; }
$tab_css
</style>
<script>
// Visitors on Windows land on the Windows tab.
addEventListener("DOMContentLoaded", () => {
  if (/Win/.test(navigator.platform)) {
    const tab = document.querySelector('.os-tabs > input[id$$="-windows"]');
    if (tab) tab.checked = true;
  }
});
</script>
<script src="$switcher" defer></script>
</head>
<body>
<main>
  <header class="site"><div class="titlebar"><h1><a href="$root">w0ts</a> / $title</h1></div>$nav</header>
$buttons$body
</main>
</body>
</html>
""")


_BUTTONS = (
    '  <div class="buttons"><a class="button" href="$repo/releases/latest/download/Track.Kommander.dmg">'
    'Download for macOS</a><a class="button quiet" href="$repo/releases/latest">Windows installer</a></div>\n'
)


def build_page(readme: str, page: Page | None = None) -> str:
    """One page of the site (default: About, index.html)."""
    page = page or PAGES[0]
    title, rest = split_title(readme)
    parts = render_parts(rest)
    check_coverage(parts)
    body = "\n".join(parts[slug] for slug in page.parts)
    return _PAGE.substitute(
        title=html.escape(title),
        page_title=html.escape(
            f"w0ts / {title}" if page is PAGES[0] else f"w0ts / {title} / {page.label}"
        ),
        description=html.escape(
            "Track Kommander, inside Traktor Pro 4: finds the next "
            "track in Traktor's own list, exports your library to CDJ USB sticks."
        ),
        style=STYLE_URL,
        switcher=SWITCHER_URL,
        repo=PUBLIC_REPO_URL,
        root=SITES_ROOT,
        nav=render_nav(page),
        buttons=("<section>" + Template(_BUTTONS).substitute(repo=PUBLIC_REPO_URL) + "</section>\n")
        if page.buttons
        else "",
        body=body,
        tab_css=tab_css(body),
    )


def tab_css(body: str) -> str:
    """Per tab: its panel shows, and its label lights, while its radio button is checked."""
    ids = re.findall(r'<input type="radio" name="[^"]+" id="([^"]+)"', body)
    return "\n".join(
        f'#{i}:checked ~ [data-tab="{i}"] {{ display: block; }}\n'
        f'#{i}:checked ~ .tabs label[for="{i}"] {{ background: var(--accent); '
        f"border-color: var(--accent); color: var(--on, #fff); }}"
        for i in ids
    )


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__.strip().splitlines()[2].strip(), file=sys.stderr)
        return 2
    root = Path(argv[1])
    source = root / SOURCE
    if not source.is_file():
        print(f"public_site: no {SOURCE} in {root}", file=sys.stderr)
        return 2
    text = source.read_text(encoding="utf-8")
    for page in PAGES:
        (root / page.file).write_text(build_page(text, page), encoding="utf-8")
    (root / ".nojekyll").write_text("", encoding="utf-8")
    source.unlink()
    print(f"public_site: wrote {', '.join(p.file for p in PAGES)} in {root}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
