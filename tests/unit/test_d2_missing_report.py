"""What the app believes when the elevated helper leaves no report.

The helper writes its result file *last*, so "it did the work and then failed
to write the report" and "it never started" look identical from that file. The
app used to call both a failure — "no result file — the helper did not finish"
— over a Traktor that had in fact just been patched.

Traktor's own folder is not ambiguous, so that is what decides here. And when
the folder says the work really did not happen, the elevation's own stderr is
the only clue to why, so it has to survive into the message rather than being
thrown away.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.d2_patch import ORIGINALS_ZIP, patch_qml
from core.d2_status import is_installed, is_removed, result_from_disk
from core.elevate import ElevationResult

NI_STYLE = """
import CSI 1.0

Mapping
{
  Thing {}
}
"""

#: What osascript says when the helper it started died on its own.
STDERR = "execution error: The command exited with a non-zero status. (1)"

DIED = ElevationResult(False, "run", STDERR, ran=True)
EXITED_CLEANLY = ElevationResult(True, "done", ran=True)


@pytest.fixture
def d2(tmp_path: Path) -> Path:
    folder = tmp_path / "Traktor Pro 4.app" / "qml" / "CSI" / "D2"
    folder.mkdir(parents=True)
    (folder / "D2.qml").write_text(NI_STYLE)
    return folder


def _install(folder: Path) -> None:
    """Both halves of the patch, as :func:`core.d2_patch.apply` leaves them."""
    qml = folder / "D2.qml"
    qml.write_text(patch_qml(qml.read_text()))
    (folder / "Api").mkdir()
    (folder / "Api" / "ApiModule.qml").write_text("// module\n")


# ── The two halves, read off disk ───────────────────────────────────────────


def test_installed_needs_both_halves(d2: Path) -> None:
    assert is_installed(d2) is False
    _install(d2)
    assert is_installed(d2) is True


def test_patched_qml_without_the_module_is_not_installed(d2: Path) -> None:
    qml = d2 / "D2.qml"
    qml.write_text(patch_qml(qml.read_text()))
    assert is_installed(d2) is False


def test_unreadable_folder_is_unknown_not_broken(tmp_path: Path) -> None:
    assert is_installed(tmp_path / "gone") is None
    assert is_removed(tmp_path / "gone") is None
    assert is_installed(None) is None


def test_removed_means_neither_half_is_left(d2: Path) -> None:
    assert is_removed(d2) is True
    _install(d2)
    assert is_removed(d2) is False


def test_removed_notices_an_abandoned_api_folder(d2: Path) -> None:
    (d2 / "Api").mkdir()
    assert is_removed(d2) is False


# ── The verdict when the report is missing ──────────────────────────────────


def test_the_disk_overrules_a_missing_report(d2: Path) -> None:
    """The work is done, so the run succeeded — whatever the helper wrote."""
    _install(d2)
    verdict = result_from_disk(d2, want_installed=True, elevation=DIED)
    assert verdict.ok
    assert verdict.ran


def test_the_archive_is_named_when_it_is_there(d2: Path) -> None:
    """The success message offers it as evidence, so it must be carried."""
    _install(d2)
    (d2 / ORIGINALS_ZIP).write_bytes(b"PK\x03\x04")
    verdict = result_from_disk(d2, want_installed=True, elevation=DIED)
    assert verdict.originals_zip == str(d2 / ORIGINALS_ZIP)


def test_no_archive_means_no_claim_about_one(d2: Path) -> None:
    _install(d2)
    assert not result_from_disk(d2, want_installed=True, elevation=DIED).originals_zip


def test_a_real_failure_keeps_the_reason(d2: Path) -> None:
    """Nothing was written, so this failed — and stderr says why."""
    verdict = result_from_disk(d2, want_installed=True, elevation=DIED)
    assert not verdict.ok
    assert STDERR in verdict.error


def test_a_silent_helper_still_explains_itself(d2: Path) -> None:
    """Exit 0 and no report: no stderr to quote, so say that much plainly."""
    verdict = result_from_disk(d2, want_installed=True, elevation=EXITED_CLEANLY)
    assert not verdict.ok
    assert verdict.error.strip()


def test_an_unreadable_folder_is_not_reported_as_a_failed_patch(tmp_path: Path) -> None:
    verdict = result_from_disk(tmp_path / "gone", want_installed=True, elevation=DIED)
    assert not verdict.ok
    assert "could not be read" in verdict.error
    assert STDERR in verdict.error


def test_restore_is_judged_by_what_is_no_longer_there(d2: Path) -> None:
    verdict = result_from_disk(d2, want_installed=False, elevation=DIED)
    assert verdict.ok

    _install(d2)
    assert not result_from_disk(d2, want_installed=False, elevation=DIED).ok
