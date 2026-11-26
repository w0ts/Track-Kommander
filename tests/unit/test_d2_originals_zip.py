"""The originals, zipped and left where the change was made.

The patch is additive and reversible, but you cannot *see* either of those by
looking at Traktor: the pristine copy lives in the app's own data directory,
somewhere else entirely, and "trust me, it was backed up" is not something a
DJ should have to take on faith about an application they paid for.

So the first thing the patch writes is ``original-files.zip``, into Traktor's
own ``CSI/D2`` folder beside the files it is about to change. Open Traktor's
folder, see the zip, list it, and the backup has proved itself.

Two rules make it trustworthy, and they are what these tests pin:

* it is written from the **pristine** directory, before anything of ours is
  copied in — a zip containing our own module is worse than no zip;
* it is never overwritten. The first one holds the originals; a second run
  would otherwise archive the patched files over them, which is exactly how
  the only clean copy on the development machine was lost once already.

It does not survive a Traktor update — an update replaces the whole
application — which is why the app keeps its own per-version copy as well.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from core.d2_patch import (
    ORIGINALS_ZIP,
    BackupError,
    apply,
    has_originals_zip,
    is_patched,
    patch_qml,
    restore,
    run_job,
    write_originals_zip,
    zip_entries,
)
from core.elevate import job_digest, read_result

NI_STYLE = """
import CSI 1.0

Mapping
{
  Thing {}
}
"""

VERSION = "Traktor Pro 4 (4.5.1)"


@pytest.fixture
def install(tmp_path) -> Path:
    """Traktor's D2 folder, as NI ships it."""
    d2 = tmp_path / "Traktor Pro 4.app" / "qml" / "CSI" / "D2"
    d2.mkdir(parents=True)
    (d2 / "D2.qml").write_text(NI_STYLE)
    (d2 / "Extra.qml").write_text("// something else NI ships\n")
    (d2 / "Defines").mkdir()
    (d2 / "Defines" / "Colors.qml").write_text("// nested\n")
    return d2


@pytest.fixture
def api(tmp_path) -> Path:
    src = tmp_path / "Api"
    src.mkdir()
    (src / "ApiModule.qml").write_text("// ours\n")
    (src / "qmldir").write_text("ApiModule 1.0 ApiModule.qml\n")
    return src


def _names(zip_path: Path) -> set[str]:
    with zipfile.ZipFile(zip_path) as zf:
        return set(zf.namelist())


# ── Writing it ─────────────────────────────────────────────────────────────


def test_it_lands_in_traktors_own_folder(install) -> None:
    written = write_originals_zip(install)
    assert written == install / ORIGINALS_ZIP
    assert written.is_file()
    assert has_originals_zip(install)


def test_it_holds_every_original_file(install) -> None:
    write_originals_zip(install)
    assert _names(install / ORIGINALS_ZIP) == {
        "D2.qml",
        "Extra.qml",
        "Defines/Colors.qml",
    }


def test_the_contents_are_the_originals(install) -> None:
    """Byte for byte. The file on disk is what is kept, line endings and
    all — on Windows NI's QML has CRLF, and so has the one written here."""
    shipped = (install / "D2.qml").read_bytes()
    write_originals_zip(install)
    with zipfile.ZipFile(install / ORIGINALS_ZIP) as zf:
        assert zf.read("D2.qml") == shipped
    assert shipped.decode().splitlines() == NI_STYLE.splitlines()


def test_it_does_not_archive_itself(install) -> None:
    """A zip inside its own next archive grows without bound."""
    write_originals_zip(install)
    (install / "D2.qml").write_text(NI_STYLE)  # touch, still pristine
    assert ORIGINALS_ZIP not in _names(install / ORIGINALS_ZIP)


def test_finders_droppings_are_left_out(install) -> None:
    (install / ".DS_Store").write_bytes(b"\x00")
    write_originals_zip(install)
    assert ".DS_Store" not in _names(install / ORIGINALS_ZIP)


def test_an_empty_folder_is_refused(tmp_path) -> None:
    """Nothing to archive means something is wrong with the path, and a zip
    of nothing would read as a successful backup."""
    empty = tmp_path / "D2"
    empty.mkdir()
    with pytest.raises(BackupError):
        write_originals_zip(empty)


def test_it_is_never_overwritten(install) -> None:
    """The first zip is the pristine one."""
    shipped = (install / "D2.qml").read_bytes()
    write_originals_zip(install)
    (install / "D2.qml").write_text(patch_qml(NI_STYLE))
    with pytest.raises(BackupError, match="already"):
        write_originals_zip(install)
    with zipfile.ZipFile(install / ORIGINALS_ZIP) as zf:
        assert zf.read("D2.qml") == shipped, "the originals survived"


def test_its_entries_can_be_listed_with_hashes(install) -> None:
    """What "inspect the zip to know the backup was made" needs: the names
    and enough to tell they are the right bytes."""
    write_originals_zip(install)
    entries = zip_entries(install / ORIGINALS_ZIP)
    assert set(entries) == {"D2.qml", "Extra.qml", "Defines/Colors.qml"}
    assert all(len(h) == 64 for h in entries.values())


# ── Inside the whole operation ─────────────────────────────────────────────


def test_apply_writes_it_before_touching_anything(install, api, tmp_path):
    result = apply(install, api, tmp_path / "backup", VERSION)
    assert result.ok, result.error
    assert result.originals_zip == str(install / ORIGINALS_ZIP)

    # Written from the pristine folder: none of ours, none of the patch.
    names = _names(install / ORIGINALS_ZIP)
    assert not any(n.startswith("Api/") for n in names)
    with zipfile.ZipFile(install / ORIGINALS_ZIP) as zf:
        assert not is_patched(zf.read("D2.qml").decode())

    # ...and the folder itself really was patched.
    assert is_patched((install / "D2.qml").read_text())
    assert (install / "Api" / "ApiModule.qml").is_file()


def test_a_second_apply_keeps_the_first_zip(install, api, tmp_path) -> None:
    """Re-installing over an existing patch must not archive the patch."""
    root = tmp_path / "backup"
    apply(install, api, root, VERSION)
    first = (install / ORIGINALS_ZIP).read_bytes()

    again = apply(install, api, root, VERSION)
    assert again.ok, again.error
    assert (install / ORIGINALS_ZIP).read_bytes() == first


def test_restore_takes_the_zip_away_with_it(install, api, tmp_path) -> None:
    """It describes a state that no longer exists once the originals are
    back, and it is not a file Native Instruments ships."""
    root = tmp_path / "backup"
    apply(install, api, root, VERSION)
    assert has_originals_zip(install)

    restore(install, root, VERSION)
    assert not has_originals_zip(install)
    assert not (install / "Api").exists()
    assert (install / "D2.qml").read_text() == NI_STYLE


def test_the_path_survives_the_elevated_round_trip(install, api, tmp_path) -> None:
    """The archive is written by the root helper in another process; the UI
    only ever sees its result file. A path that does not come back cannot be
    shown to the user, and "it was backed up" without one is just a claim."""
    result_path = tmp_path / "result.json"
    job = tmp_path / "job.json"
    job.write_text(
        json.dumps(
            {
                "action": "patch-d2",
                "csi_d2": str(install),
                "api": str(api),
                "backup_root": str(tmp_path / "backup"),
                "version": VERSION,
                "result": str(result_path),
            }
        )
    )
    assert run_job(job, job_digest(job)) == 0

    done = read_result(result_path)
    assert done.ok, done.describe()
    assert done.originals_zip == str(install / ORIGINALS_ZIP)
    assert Path(done.originals_zip).is_file()


def test_the_zip_is_not_in_the_app_side_backup(install, api, tmp_path) -> None:
    """The app's own copy is of what NI shipped, not of our artefact."""
    root = tmp_path / "backup"
    apply(install, api, root, VERSION)
    backed_up = {p.name for p in (root / VERSION / "D2").rglob("*") if p.is_file()}
    assert ORIGINALS_ZIP not in backed_up
