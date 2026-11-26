"""What ships inside the app and under which terms — the data behind
Settings ▸ About.

The Python packages are read from ``licenses/THIRD_PARTY_NOTICES.txt``, which
``scripts/collect_licenses.py`` generates from the installed dependency
closure, so this list cannot drift from the notices that ship. Native
libraries that are not Python packages are the only hand-kept rows
(:data:`NATIVE_LIBRARIES`); THIRD_PARTY.md is the audit behind both.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from core.runtime import bundle_data_dir


@dataclass(frozen=True)
class Component:
    name: str
    version: str
    licence: str
    url: str


#: The FFmpeg inside vendor/wheels/ — must match FFMPEG_VERSION in
#: scripts/build_ffmpeg_lgpl.sh (a test holds them together).
FFMPEG_VERSION = "8.0.1"

#: Libraries linked into the app that are not Python packages, so the notices
#: generator cannot see them. Keep in step with THIRD_PARTY.md.
NATIVE_LIBRARIES: tuple[Component, ...] = (
    Component("Qt 6 (via PySide6)", "", "LGPL-3.0-only", "https://www.qt.io"),
    Component(
        "FFmpeg (audio-only build, via PyAV)",
        FFMPEG_VERSION,
        "LGPL-2.1-or-later",
        "https://ffmpeg.org",
    ),
    Component(
        "libsndfile (via soundfile)",
        "",
        "LGPL-2.1-or-later",
        "https://libsndfile.github.io/libsndfile/",
    ),
    Component(
        "GCC runtime libraries (via SciPy)",
        "",
        "GPL-3.0-or-later WITH GCC-exception-3.1",
        "https://gcc.gnu.org",
    ),
    Component(
        "SQLCipher (via sqlcipher3-wheels)",
        "",
        "BSD-3-Clause",
        "https://www.zetetic.net/sqlcipher/",
    ),
    Component("OpenSSL (inside sqlcipher3-wheels)", "", "Apache-2.0", "https://www.openssl.org"),
    Component(
        "CLAP audio model (LAION)",
        "",
        "Apache-2.0",
        "https://huggingface.co/laion/clap-htsat-fused",
    ),
    Component("Inter typeface", "", "OFL-1.1", "https://rsms.me/inter/"),
    Component("Roboto typeface", "", "Apache-2.0", "https://fonts.google.com/specimen/Roboto"),
)

#: The FFmpeg source the bundled libraries were built from, shipped under
#: ``licenses/source/`` with the build script (LGPL corresponding source).
FFMPEG_SOURCE_TARBALL = f"ffmpeg-{FFMPEG_VERSION}.tar.xz"

#: Files the build maps into ``licenses/`` from elsewhere in the repository;
#: running from source they are only found at their original place.
_REPO_ORIGINALS = {"LICENSE.txt": "LICENSE", "THIRD_PARTY.md": "THIRD_PARTY.md"}

_BAR = "=" * 72
_HEADER = re.compile(
    # Project: is omitted for packages that declare no URL.
    rf"^{_BAR}\n(?P<name>\S+) (?P<version>\S+)\nLicence: (?P<licence>.*)\n"
    rf"(?:Project: ?(?P<url>.*)\n)?{_BAR}$",
    re.MULTILINE,
)


def parse_notices(text: str) -> tuple[Component, ...]:
    """Every package header in a THIRD_PARTY_NOTICES.txt, in file order."""
    return tuple(
        Component(m["name"], m["version"], m["licence"].strip(), (m["url"] or "").strip())
        for m in _HEADER.finditer(text)
    )


def licences_dir(bundle_dir: Path | None = None) -> Path:
    return (bundle_dir or bundle_data_dir()) / "licenses"


def licence_file(name: str, *, bundle_dir: Path | None = None) -> Path | None:
    """A shipped licence file by its name under ``licenses/``, or None.

    Falls back to the repository original for the files the build maps in,
    so the page also works when running from source.
    """
    base = bundle_dir or bundle_data_dir()
    shipped = licences_dir(base) / name
    if shipped.is_file():
        return shipped
    original = _REPO_ORIGINALS.get(name)
    if original and (base / original).is_file():
        return base / original
    return None


def ffmpeg_source(*, bundle_dir: Path | None = None) -> Path | None:
    """The shipped FFmpeg source tarball; from source, the vendored one."""
    base = bundle_dir or bundle_data_dir()
    for path in (
        licences_dir(base) / "source" / FFMPEG_SOURCE_TARBALL,
        base / "vendor" / "source" / FFMPEG_SOURCE_TARBALL,
    ):
        if path.is_file():
            return path
    return None


def components(notices: Path | None = None) -> tuple[Component, ...]:
    """Native libraries first, then every Python package from the notices."""
    path = notices or licences_dir() / "THIRD_PARTY_NOTICES.txt"
    try:
        packages = parse_notices(path.read_text("utf-8"))
    except OSError:
        packages = ()
    return NATIVE_LIBRARIES + packages
