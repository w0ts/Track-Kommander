"""The add-on TK runs with, if any, and whether TK may load it.

An add-on is a ``tk-addon`` folder: its Python (the module core.extensions imports) and a
``manifest.json`` — ``{"name": …, "version": …, "page": …}``. It is never searched for: the
release that carries one has it inside the same install as TK (User, 2026-11-26: the Plus
edition is a full version, one installer), and whoever starts TK names it in ``TK_ADDON_DIR`` —
libtk inside Traktor (the folder beside TK's kit), or a developer's launch script.

TK loads only an add-on of its own version (User, 2026-11-25): the two are built together, and an
add-on built for another TK may call what is no longer there. A manifest version of "dev" (a
source checkout) matches any TK.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path

from core.health import Notice

log = logging.getLogger(__name__)

FOLDER = "tk-addon"
MANIFEST = "manifest.json"
DEV_ENV = "TK_ADDON_DIR"
#: The mismatch notice's button: opens the add-on's page (ui.mw_health).
PAGE_ACTION = "addon_page"


@dataclass(frozen=True)
class Found:
    folder: Path
    name: str
    version: str
    page: str = ""


def read(folder: Path) -> Found | None:
    """The add-on in *folder*; None when its manifest is missing or unreadable."""
    try:
        data = json.loads((folder / MANIFEST).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        log.warning("add-on: %s unreadable (%s)", folder / MANIFEST, exc)
        return None
    if not isinstance(data, dict):
        return None
    name, version, page = data.get("name"), data.get("version"), data.get("page", "")
    if not isinstance(name, str) or not isinstance(version, str) or not name or not version:
        log.warning("add-on: %s has no name or version", folder / MANIFEST)
        return None
    return Found(folder, name, version, page if isinstance(page, str) else "")


def find() -> Found | None:
    """The add-on named in TK_ADDON_DIR; None when none is named or its manifest is unreadable."""
    named = os.environ.get(DEV_ENV)
    if not named or not Path(named).is_dir():
        return None
    return read(Path(named))


def matches(found: Found, tk_version: str) -> bool:
    return found.version in (tk_version, "dev")


def mismatch_notice(found: Found, tk_version: str) -> Notice:
    return Notice(
        key=f"addon-mismatch-{found.version}-{tk_version}",
        severity="warning",
        text=(
            f"The installed {found.name} is {found.version}; Track Kommander is {tk_version}. "
            f"Install the matching {found.name}: it is not loaded until then."
        ),
        action=PAGE_ACTION if found.page else None,
        action_label="Get it" if found.page else None,
    )
