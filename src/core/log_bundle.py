"""One zip holding what is needed to debug the app on someone else's machine.

``log.log`` alone is often not enough: it is truncated at every launch, and
it does not say which build is running, on what, or how far analysis has got.
The bundle adds ``crash.log`` (appended across launches) and a short
``diagnostics.txt`` — version, OS, the analysis state, which models are
present — so one file sent over chat answers the first round of questions.

Settings are deliberately not included, and the home folder is written as
``~`` throughout: the bundle is sent to someone else, and should say what
went wrong without saying whose machine it was.
"""

from __future__ import annotations

import logging
import platform
import sys
import tomllib
import zipfile
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from core.prefs import VERBOSE_LOG
from core.runtime import bundle_data_dir

log = logging.getLogger(__name__)

#: Log files taken from the log folder, when present.
LOG_FILES = ("log.log", "crash.log")

#: Package name, for the version when Qt does not know it.
_DIST_NAME = "track-kommander"


def _qt_version() -> str:
    try:
        from PySide6.QtCore import QCoreApplication

        return QCoreApplication.applicationVersion()
    except Exception:
        return ""


def _pyproject_version(base: Path) -> str:
    """The version in *base*/pyproject.toml — both builds ship that file,
    since a Nuitka build has no package metadata to ask."""
    try:
        return str(
            tomllib.loads((base / "pyproject.toml").read_text(encoding="utf-8"))["project"][
                "version"
            ]
        )
    except (OSError, KeyError, TypeError, tomllib.TOMLDecodeError):
        return ""


def app_version() -> str:
    """The running build's version, or "unknown".

    The pyproject that ships with the build is asked first. Qt is never told
    the version, and what it answers unasked is whatever the executable
    carries — which, run from source on Windows, is the interpreter's.
    """
    version = _pyproject_version(bundle_data_dir()) or _qt_version()
    if version:
        return version
    try:
        from importlib.metadata import version as dist_version

        return dist_version(_DIST_NAME)
    except Exception:
        return "unknown"


def _safe(label: str, probe: Callable[[], object]) -> str:
    """``label: value``, or the error — one failing probe must not cost the
    rest of the report."""
    try:
        return f"{label}: {probe()}"
    except Exception as exc:
        return f"{label}: unavailable ({type(exc).__name__}: {exc})"


def _analysis_state() -> str:
    from core.analysis_status import describe
    from core.db import analysis_gaps

    return describe(analysis_gaps())


def _clap_state() -> str:
    from core.clap_models import clap_dir, clap_model_ready

    return f"{'present' if clap_model_ready() else 'MISSING'} in {clap_dir()}"


def _traktor_state() -> str:
    from core.traktor import find_traktor_app, find_traktor_nml

    return f"app {find_traktor_app()}, collection {find_traktor_nml()}"


def _verbose_state() -> str:
    return "on" if VERBOSE_LOG.get() else "off"


#: Lines another layer adds to the report — the UI's watchdog, say, which
#: ``core`` cannot see. Each is a label and a probe, listed after core's own.
_extra_probes: tuple[tuple[str, Callable[[], object]], ...] = ()


def add_probe(label: str, probe: Callable[[], object]) -> None:
    """Add ``label: probe()`` to the report, for what ``core`` cannot see."""
    global _extra_probes
    _extra_probes = (*_extra_probes, (label, probe))


def diagnostics() -> str:
    """The text of ``diagnostics.txt``."""
    lines = [
        "Track Kommander diagnostics",
        f"written: {datetime.now().isoformat(timespec='seconds')}",
        f"version: {app_version()}",
        f"frozen: {bool(getattr(sys, 'frozen', False) or '__compiled__' in globals())}",
        f"os: {platform.platform()} ({platform.machine()})",
        f"python: {platform.python_version()}",
        _safe("verbose logging", _verbose_state),
        _safe("traktor", _traktor_state),
        _safe("clap model", _clap_state),
        _safe("analysis", _analysis_state),
        *(_safe(label, probe) for label, probe in _extra_probes),
    ]
    return "\n".join(lines) + "\n"


def write_bundle(
    log_dir: Path,
    dest_dir: Path,
    *,
    now: datetime | None = None,
    report: Callable[[], str] = diagnostics,
) -> Path:
    """Zip the logs and a diagnostics report into *dest_dir*. Returns the zip."""
    stamp = (now or datetime.now()).strftime("%Y-%m-%d_%H%M%S")
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / f"TrackKommander-log-{stamp}.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("diagnostics.txt", without_home(report()))
        for name in LOG_FILES:
            path = log_dir / name
            if path.is_file():
                zf.writestr(name, _without_home_bytes(path.read_bytes()))
    log.info("log bundle written: %s", target)
    return target


def _home_spellings() -> tuple[str, ...]:
    """The home folder as it can appear in a path: the platform's own
    spelling, with forward slashes, and — on Windows — with each backslash
    doubled, which is how a path reads after ``repr()`` or JSON."""
    home = Path.home()
    return tuple(dict.fromkeys((str(home).replace("\\", "\\\\"), str(home), home.as_posix())))


def without_home(text: str) -> str:
    """*text* with the home folder written as ``~``.

    The bundle is sent to someone else, and every path under the home folder
    carries the name of the account. Nothing a bundle is read for needs it.
    """
    for spelling in _home_spellings():
        text = text.replace(spelling, "~")
    return text


def _without_home_bytes(data: bytes) -> bytes:
    """:func:`without_home` for a log as it is on disk — not decoded, so a
    log with a stray byte in it goes in as it is."""
    for spelling in _home_spellings():
        data = data.replace(spelling.encode("utf-8"), b"~")
    return data


def default_destination() -> Path:
    """The Desktop, where a bundle is easy to find and drag into a chat."""
    desktop = Path.home() / "Desktop"
    return desktop if desktop.is_dir() else Path.home()
