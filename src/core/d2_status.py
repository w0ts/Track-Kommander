"""The three things that have to be true for the Traktor bridge to work.

They form a chain, and each can be established on its own:

1. **The D2 QML is patched** — our module is in Traktor's own mapping. Read
   off disk; Traktor does not have to be running.
2. **A D2 controller is added** in Traktor's Controller Manager, which is what
   makes Traktor load that QML at all. Read from its settings file.
3. **The bridge is responding** — Traktor is actually posting to us.

The order is diagnostic. If 1 is false nothing else can work; if 2 is false
the QML is never loaded; only then is 3 worth looking at. The settings screen
used to show just the version number the QML reported *over* a working
connection, which said nothing at all when the connection was broken — the
one time anybody looks.

Every check is three-valued. **Unknown is not broken**: if Traktor's settings
cannot be read, reporting "not configured" would send someone to fix
something that is already right.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from core.d2_patch import ORIGINALS_ZIP, backed_up_versions, is_patched
from core.elevate import ElevationResult
from core.traktor_devices import has_d2

#: A bridge posts keepAlive about once a second, so anything older than this
#: means it has stopped rather than merely paused.
QUIET_AFTER_S = 5.0


@dataclass(frozen=True)
class Check:
    """One of the three. ``ok=None`` means it could not be determined."""

    ok: bool | None
    summary: str
    """Short enough for a status row."""

    detail: str = ""
    """What it means and what to do — the part that makes it actionable."""


def _halves(csi_d2: Path) -> tuple[bool, bool] | None:
    """``(D2.qml is patched, Api/ is there)``, or None if it cannot be read.

    The install is these two facts and nothing else, so everything that asks
    "is it installed?" asks them — one reading of the folder, one answer.
    """
    csi_d2 = Path(csi_d2)
    try:
        text = (csi_d2 / "D2.qml").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    return is_patched(text), (csi_d2 / "Api").is_dir()


def is_installed(csi_d2: Path | None) -> bool | None:
    """Is the bridge fully in place? ``None`` when the folder cannot be read.

    The ground truth the elevated helper's report is *about* — so when that
    report goes missing, this answers instead. Unknown stays unknown: a
    folder that cannot be read says nothing about what is in it.
    """
    halves = _halves(csi_d2) if csi_d2 is not None else None
    if halves is None:
        return None
    patched, has_api = halves
    return patched and has_api


def is_removed(csi_d2: Path | None) -> bool | None:
    """Is *neither* half left — the state a finished restore leaves?

    Not ``not is_installed``: a folder holding one half of the patch is
    neither installed nor restored, and calling a half-undone restore "done"
    would leave Traktor loading a module that is no longer there.
    """
    halves = _halves(csi_d2) if csi_d2 is not None else None
    if halves is None:
        return None
    patched, has_api = halves
    return not patched and not has_api


def result_from_disk(
    csi_d2: Path | None, *, want_installed: bool, elevation: ElevationResult
) -> ElevationResult:
    """What to believe when the elevated helper left no result file.

    The helper writes its report *last*, so a job that did the work and then
    failed to write it is indistinguishable — by that file — from one that
    never started. Reading the absence as failure told someone their install
    had stopped while Traktor sat there patched.

    Traktor's folder is not ambiguous, so it decides. Only when it says the
    work did not happen is this a failure, and then *elevation*'s own stderr
    is the single clue to why, so it is carried into the message rather than
    dropped.
    """
    state = is_installed(csi_d2) if want_installed else is_removed(csi_d2)
    if state:
        zip_path = Path(csi_d2) / ORIGINALS_ZIP if csi_d2 else None
        return ElevationResult(
            True,
            "done",
            ran=True,
            originals_zip=str(zip_path) if zip_path and zip_path.is_file() else "",
        )

    why = elevation.error.strip() or "the helper finished without reporting"
    if state is None:
        why = f"Traktor's folder could not be read — {why}"
    return ElevationResult(False, "run", why, ran=True)


def qml_check(csi_d2: Path | None) -> Check:
    """Is our module installed into Traktor's D2 mapping?

    Both halves have to be there. A patched ``D2.qml`` whose ``import "Api"``
    resolves to nothing would stop Traktor loading the mapping at all, and an
    ``Api/`` folder nothing references does nothing — that second one is the
    state a half-finished manual install leaves behind.
    """
    if csi_d2 is None:
        return Check(None, "Unknown", "Traktor's QML folder could not be located.")
    halves = _halves(csi_d2)
    if halves is None:
        return Check(None, "Unknown", f"Could not read {Path(csi_d2) / 'D2.qml'}.")
    patched, has_api = halves

    if patched and has_api:
        return Check(
            True,
            "Installed",
            "Track Kommander's module is part of Traktor's own D2 "
            "mapping; everything Native Instruments shipped is "
            "still there.",
        )
    if patched and not has_api:
        return Check(
            False,
            "Incomplete — module missing",
            "D2.qml refers to our Api module but the Api folder is "
            "not there, so Traktor cannot load the mapping. "
            "Install again.",
        )
    if has_api and not patched:
        return Check(
            False,
            "Incomplete — not referenced",
            "The Api folder is in place but Traktor's D2.qml does "
            "not load it. Install to add the two lines that do.",
        )
    return Check(
        False,
        "Not installed",
        "Traktor's D2 mapping is untouched, so the bridge has "
        "nothing to run. Use Install into Traktor.",
    )


@dataclass(frozen=True)
class UpdateNotice:
    """Traktor was updated and took the patch with it.

    Not a fourth link in the chain — it is the *reason* the first one broke,
    and it exists separately because it comes with an action nothing else
    does: reinstall, rather than install for the first time.
    """

    previous: str
    """The Traktor this machine had the bridge installed into."""

    current: str
    """The Traktor installed now, which has no backup and no patch."""

    @property
    def summary(self) -> str:
        return "Traktor was updated — reinstall"

    @property
    def detail(self) -> str:
        return (
            f"The bridge was installed into {self.previous}. Traktor is "
            f"now {self.current}, and an update replaces the whole "
            f"application, so our module went with it. Quit Traktor and "
            f"install again — {self.previous}'s original files are still "
            f"backed up."
        )


def _product_of(label: str) -> str | None:
    """ "Traktor Pro 4 (4.5.1)" → "Traktor Pro 4". ``None`` without a version.

    Labels come from :attr:`core.traktor_install.TraktorInstall.label`, which
    appends the full version in parentheses only when it could be read. A
    label with no version identifies no particular install, so it is not
    something to compare against — see :func:`update_check`.
    """
    product, sep, rest = label.partition(" (")
    return product if sep and rest.endswith(")") and product else None


def update_check(csi_d2: Path | None, backup_root: Path, version: str) -> UpdateNotice | None:
    """Did a Traktor update wipe an install that used to be there?

    Backups are per version, so "a backup exists for this Traktor, but not
    for the version of it running now" is the signal — it cannot be true on a
    machine where the bridge was never installed.

    Scoped to one *product*, because every install on the machine shares one
    backup directory. Someone who patched Traktor Pro 4 and then points the
    app at an untouched Traktor Pro 3 has done a first-time install of the
    bridge for 3, not upgraded 4 into 3 — and this app offers a picker that
    makes exactly that two clicks away.

    ``None`` for every other shape, including the ones that merely look
    similar: a first install has no backups for its product, one still
    patched has lost nothing, and a label carrying no version identifies no
    install at all. Silence is the safe answer — this prompts someone to
    modify Traktor's own application, so guessing costs more than missing it.
    """
    if csi_d2 is None or not version:
        return None
    product = _product_of(version)
    if product is None:
        return None
    try:
        text = (Path(csi_d2) / "D2.qml").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    if is_patched(text):
        return None

    versions = backed_up_versions(backup_root)
    if version in versions:
        return None
    same_product = [v for v in versions if _product_of(v) == product]
    if not same_product:
        return None
    return UpdateNotice(previous=same_product[0], current=version)


def controller_check(tsi_path: Path | None) -> Check:
    """Is a Kontrol D2 present in Traktor's Controller Manager?

    Without it Traktor never loads the D2 QML, so the patch above sits there
    doing nothing and the connection silently never comes up.
    """
    if tsi_path is None:
        return Check(None, "Unknown", "Traktor's settings file could not be located.")
    present = has_d2(Path(tsi_path))
    if present is None:
        return Check(None, "Unknown", "Traktor's settings file could not be read.")
    if present:
        return Check(
            True,
            "Added",
            "Traktor has a Kontrol D2 device, which is what makes "
            "it load the mapping. Used by Track Kommander to talk "
            "to Traktor.",
        )
    return Check(
        False,
        "Not added",
        "In Traktor: Preferences → Controller Manager → Add… → "
        "Traktor → Kontrol D2. The hardware is not required — the "
        "device entry is what loads the mapping.",
    )


def api_check(received: int, last_age_s: float | None) -> Check:
    """Is Traktor's bridge actually posting to us?

    "Never started" and "started then stopped" are different faults and get
    different answers, so they are not collapsed into one.
    """
    if last_age_s is not None and last_age_s <= QUIET_AFTER_S:
        return Check(
            True,
            "Responding",
            f"{received:,} message(s) from Traktor, most recent {last_age_s:.0f}s ago.",
        )
    if received:
        return Check(
            False,
            "Went quiet",
            f"{received:,} message(s) arrived but nothing for "
            f"{last_age_s:.0f}s. Traktor may have closed, or the "
            f"bridge has stalled — try Reconnect."
            if last_age_s is not None
            else f"{received:,} message(s) arrived but nothing since.",
        )
    return Check(
        False,
        "No response",
        "Nothing has arrived from Traktor. Check Traktor is running "
        "and has been restarted since the module was installed — it "
        "reads the mapping once, at startup.",
    )
