"""The data behind Settings ▸ About: which components ship, under what terms.

The package list is parsed from the generated THIRD_PARTY_NOTICES.txt rather
than kept by hand, so the page cannot disagree with the notices that ship.
Native libraries that are not Python packages (Qt, FFmpeg, libsndfile) are
the only hand-kept rows.
"""

from __future__ import annotations

import re
from pathlib import Path

from core import licensing
from core.licensing import (
    FFMPEG_SOURCE_TARBALL,
    Component,
    components,
    ffmpeg_source,
    licence_file,
    parse_notices,
)

REPO = Path(__file__).resolve().parents[2]

BAR = "=" * 72

SAMPLE = f"""THIRD-PARTY NOTICES — Track Kommander
{BAR}

Intro text.

{BAR}
audioread 3.1.0
Licence: MIT
Project: https://github.com/beetbox/audioread
{BAR}

--- licenses/LICENSE ---

Copyright (c) 2011-2018 Adrian Sampson
{BAR}

{BAR}
rekordbox-pdb 0.1.0
Licence: MIT © fragmede
Project:
{BAR}

text
"""


def test_parse_notices_reads_each_header() -> None:
    assert parse_notices(SAMPLE) == (
        Component("audioread", "3.1.0", "MIT", "https://github.com/beetbox/audioread"),
        Component("rekordbox-pdb", "0.1.0", "MIT © fragmede", ""),
    )


def test_a_header_without_a_project_line_is_still_read() -> None:
    """collect_licenses.py omits Project: when a package declares no URL
    (cffi, decorator, pooch …)."""
    text = f"{BAR}\ncffi 2.0.0\nLicence: MIT\n{BAR}\n\ntext\n"
    assert parse_notices(text) == (Component("cffi", "2.0.0", "MIT", ""),)


def test_every_shipped_header_is_read() -> None:
    text = (REPO / "licenses" / "THIRD_PARTY_NOTICES.txt").read_text("utf-8")
    assert len(parse_notices(text)) == text.count("\nLicence: ")


def test_a_bar_inside_a_licence_text_is_not_a_header() -> None:
    """Licence texts contain their own ===== underlines (Python's does)."""
    text = SAMPLE.replace("Copyright (c)", f"B. TERMS\n{'=' * 30}\nCopyright (c)")
    assert [c.name for c in parse_notices(text)] == ["audioread", "rekordbox-pdb"]


def test_the_shipped_notices_parse() -> None:
    parsed = parse_notices((REPO / "licenses" / "THIRD_PARTY_NOTICES.txt").read_text("utf-8"))
    names = {c.name for c in parsed}
    assert {"av", "PySide6", "numpy", "soundfile"} <= names
    assert "mutagen" not in names  # GPL — replaced by core/audio_meta.py
    assert all(c.licence for c in parsed)


def test_components_lead_with_the_native_libraries(tmp_path) -> None:
    notices = tmp_path / "THIRD_PARTY_NOTICES.txt"
    notices.write_text(SAMPLE, "utf-8")
    rows = components(notices)
    native = [c.name for c in licensing.NATIVE_LIBRARIES]
    assert [c.name for c in rows[: len(native)]] == native
    assert [c.name for c in rows[len(native) :]] == ["audioread", "rekordbox-pdb"]


def test_components_without_a_notices_file_still_lists_the_natives(tmp_path) -> None:
    assert components(tmp_path / "missing.txt") == licensing.NATIVE_LIBRARIES


def test_no_shipped_component_is_gpl() -> None:
    """The point of the audit: nothing linked into the app may be GPL. LGPL
    and the GCC runtime exception are fine; plain GPL is not."""
    rows = components(REPO / "licenses" / "THIRD_PARTY_NOTICES.txt")
    gpl = [
        c
        for c in rows
        if "GPL" in c.licence.replace("LGPL", "")
        and " OR " not in c.licence  # PySide6's tri-licence
        and "exception" not in c.licence.lower()
    ]
    assert gpl == []


# ── where the licence files are ──────────────────────────────────────────────


def test_licence_file_prefers_the_bundled_copy(tmp_path) -> None:
    (tmp_path / "licenses").mkdir()
    shipped = tmp_path / "licenses" / "LICENSE.txt"
    shipped.write_text("licence", "utf-8")
    assert licence_file("LICENSE.txt", bundle_dir=tmp_path) == shipped


def test_in_development_the_mapped_files_come_from_the_repo_root(tmp_path) -> None:
    """The build maps LICENSE → licenses/LICENSE.txt and THIRD_PARTY.md into
    licenses/; running from source they only exist at the root."""
    (tmp_path / "LICENSE").write_text("licence", "utf-8")
    (tmp_path / "THIRD_PARTY.md").write_text("audit", "utf-8")
    assert licence_file("LICENSE.txt", bundle_dir=tmp_path) == tmp_path / "LICENSE"
    assert licence_file("THIRD_PARTY.md", bundle_dir=tmp_path) == tmp_path / "THIRD_PARTY.md"


def test_a_licence_file_that_does_not_exist_is_none(tmp_path) -> None:
    assert licence_file("LGPL-3.0.txt", bundle_dir=tmp_path) is None


def test_the_ffmpeg_source_is_in_the_repo() -> None:
    """The LGPL source of the FFmpeg TK ships; the build copies it to licenses/source/."""
    assert (REPO / "vendor" / "source" / licensing.FFMPEG_SOURCE_TARBALL).is_file()


def test_ffmpeg_source_is_found_in_the_bundle_or_the_repo(tmp_path) -> None:
    assert ffmpeg_source(bundle_dir=tmp_path) is None
    dev = tmp_path / "vendor" / "source" / FFMPEG_SOURCE_TARBALL
    dev.parent.mkdir(parents=True)
    dev.write_bytes(b"x")
    assert ffmpeg_source(bundle_dir=tmp_path) == dev
    shipped = tmp_path / "licenses" / "source" / FFMPEG_SOURCE_TARBALL
    shipped.parent.mkdir(parents=True)
    shipped.write_bytes(b"x")
    assert ffmpeg_source(bundle_dir=tmp_path) == shipped


def test_the_version_on_the_page_is_the_one_built() -> None:
    script = (REPO / "scripts" / "build_ffmpeg_lgpl.sh").read_text("utf-8")
    assert "FFMPEG_VERSION:-" + licensing.FFMPEG_VERSION + "}" in script
    assert licensing.FFMPEG_SOURCE_TARBALL == f"ffmpeg-{licensing.FFMPEG_VERSION}.tar.xz"


# ── the app's own licence ────────────────────────────────────────────────────

_LICENCE = (REPO / "LICENSE").read_text("utf-8")
_LICENCE_RTF = (REPO / "scripts" / "wix" / "license.rtf").read_text("utf-8")


def test_the_app_is_gpl3() -> None:
    """Track Kommander is GPL-3.0 from 0.1.0, its source public (User, 2026-11-26). Every place
    that names the licence has to agree."""
    assert _LICENCE.lstrip().startswith("GNU GENERAL PUBLIC LICENSE")
    assert "Version 3, 29 June 2007" in _LICENCE
    pyproject = (REPO / "pyproject.toml").read_text("utf-8")
    assert 'license         = "GPL-3.0-only"' in pyproject
    for doc in ("THIRD_PARTY.md", "licenses/README.md", "licenses/THIRD_PARTY_NOTICES.txt"):
        text = (REPO / doc).read_text("utf-8")
        assert "proprietary software" not in text and "free to use, not" not in text, doc


def test_the_msi_licence_page_is_the_licence() -> None:
    """WiX shows scripts/wix/license.rtf during install: the GPL, with TK's copyright."""
    assert "Replace this file" not in _LICENCE_RTF
    plain = re.sub(r"\\[a-z]+-?\d* ?|[{}]", "", _LICENCE_RTF)
    for heading in re.findall(r"^\s*(\d+\. [A-Z][^.]+\.)$", _LICENCE, re.MULTILINE):
        assert heading in plain, heading
    assert "Copyright (C) 2026 w0ts" in plain


def test_gpl3_text_still_ships_because_lgpl3_incorporates_it() -> None:
    gpl = (REPO / "licenses" / "GPL-3.0.txt").read_text("utf-8")
    assert gpl.lstrip().startswith("GNU GENERAL PUBLIC LICENSE")
