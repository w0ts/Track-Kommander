"""What the elevated helper will and will not do, whatever it is handed.

The helper runs as root. Its instructions are a file in a folder the user
owns, written before the password prompt and read after it — so anything
running as that user has the length of the prompt to change them. It used to
take every path from that file and act on it.

Two lines of defence, tested separately:

* the job is pinned — its hash travels on the command line the user
  authorised, and a job that no longer matches is not run;
* the helper is bounded — even a job that matches cannot send it outside a
  Traktor ``CSI/D2`` folder, make it write its result somewhere else, or
  make it write through a link someone left in its way.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

from core.d2_patch import (
    RESULT_NAME,
    BackupError,
    back_up,
    helper_main,
    is_patched,
    is_traktor_d2,
    patch_qml,
    restore,
    run_job,
)
from core.elevate import (
    build_macos_command,
    job_digest,
    windows_parameters,
    write_job,
)

NI_STYLE = """
import CSI 1.0
import QtQuick 2.0

Mapping
{
  LoopModePads { name: "loop_mode_pads" }
}
"""

VERSION = "Traktor Pro 4 (4.5.1)"


@pytest.fixture
def install(tmp_path) -> Path:
    d2 = tmp_path / "Traktor Pro 4.app" / "qml" / "CSI" / "D2"
    d2.mkdir(parents=True)
    (d2 / "D2.qml").write_text(NI_STYLE)
    (d2 / "Extra.qml").write_text("// something else NI ships\n")
    return d2


@pytest.fixture
def api(tmp_path) -> Path:
    folder = tmp_path / "Api"
    folder.mkdir()
    (folder / "ApiModule.qml").write_text("// ours\n")
    return folder


@pytest.fixture
def work(tmp_path) -> Path:
    folder = tmp_path / "work"
    folder.mkdir()
    return folder


def _link(link: Path, target: Path, *, directory: bool = False) -> None:
    """A symbolic link, or a skip where this account cannot make one.

    Windows grants that only to administrators and to accounts with the
    privilege switched on; the GitHub runner service has neither. What the
    helper does with a link is then untested there, not wrongly passed.
    """
    try:
        link.symlink_to(target, target_is_directory=directory)
    except OSError as exc:
        if getattr(exc, "winerror", None) != 1314:
            raise
        pytest.skip("this account may not create symbolic links (Windows privilege 1314)")


def _job(work: Path, **fields) -> Path:
    return write_job(work, fields)


def _patch_job(work: Path, install: Path, api: Path, tmp_path: Path, **changes) -> Path:
    fields = {
        "action": "patch-d2",
        "csi_d2": str(install),
        "api": str(api),
        "backup_root": str(tmp_path / "backups"),
        "version": VERSION,
    }
    return _job(work, **{**fields, **changes})


def _result(work: Path) -> dict:
    return json.loads((work / RESULT_NAME).read_text())


# ── The job is pinned ───────────────────────────────────────────────────────


def test_the_digest_is_the_hash_of_the_file(work) -> None:
    job = _job(work, action="patch-d2")
    assert job_digest(job) == hashlib.sha256(job.read_bytes()).hexdigest()


def test_a_job_that_matches_is_run(work, install, api, tmp_path) -> None:
    job = _patch_job(work, install, api, tmp_path)

    assert run_job(job, job_digest(job)) == 0
    assert _result(work)["ok"] is True
    assert is_patched((install / "D2.qml").read_text())


def test_a_job_changed_after_it_was_authorised_is_not_run(work, install, api, tmp_path) -> None:
    job = _patch_job(work, install, api, tmp_path)
    authorised = job_digest(job)
    elsewhere = tmp_path / "traktor" / "CSI" / "D2"
    elsewhere.mkdir(parents=True)
    (elsewhere / "D2.qml").write_text(NI_STYLE)
    job.write_text(json.dumps({**json.loads(job.read_text()), "csi_d2": str(elsewhere)}))

    assert run_job(job, authorised) != 0

    assert not is_patched((elsewhere / "D2.qml").read_text())
    assert not is_patched((install / "D2.qml").read_text())
    assert "changed" in _result(work)["error"]


@pytest.mark.parametrize("digest", ["", "not a hash", "0" * 64])
def test_a_job_without_its_own_digest_is_not_run(work, install, api, tmp_path, digest: str) -> None:
    job = _patch_job(work, install, api, tmp_path)

    assert run_job(job, digest) != 0
    assert not is_patched((install / "D2.qml").read_text())


# ── The command line carries the digest ─────────────────────────────────────


def test_the_macos_command_names_the_digest(work) -> None:
    job = _job(work, action="patch-d2")
    digest = job_digest(job)

    script = build_macos_command(Path("/usr/bin/true"), job, "Patch", digest)[-1]

    assert f"--job-sha256 {digest}" in script


def test_the_windows_parameters_name_the_digest(work) -> None:
    job = _job(work, action="patch-d2")
    digest = job_digest(job)

    assert windows_parameters(["app.exe"], job, digest) == (
        f'--patch-d2 "{job}" --job-sha256 {digest}'
    )
    assert windows_parameters(["python.exe", "C:/a b/app.py"], job, digest) == (
        f'"C:/a b/app.py" --patch-d2 "{job}" --job-sha256 {digest}'
    )


@pytest.mark.parametrize("digest", ["", "abc", "z" * 64, "0" * 63 + '"'])
def test_only_a_digest_goes_on_the_command_line(work, digest: str) -> None:
    job = _job(work, action="patch-d2")
    with pytest.raises(ValueError):
        build_macos_command(Path("/usr/bin/true"), job, "Patch", digest)
    with pytest.raises(ValueError):
        windows_parameters(["app.exe"], job, digest)


@pytest.mark.skipif(
    sys.platform != "darwin",
    reason="an AppleScript string, around a folder name Windows cannot "
    "have: a double quote is not legal in one there",
)
def test_a_quote_in_a_path_stays_inside_the_applescript_string(tmp_path) -> None:
    """The command is parsed by AppleScript before sh sees it. A double quote
    that reached it bare would end the string it sits in."""
    folder = tmp_path / 'a "quoted" folder'
    folder.mkdir()
    job = _job(folder, action="patch-d2")

    script = build_macos_command(Path("/usr/bin/true"), job, "Patch", job_digest(job))[-1]

    inner = script[len('do shell script "') : script.index('" with prompt')]
    assert '\\"quoted\\"' in inner
    assert '"' not in inner.replace('\\"', "")


def test_the_helper_reads_both_from_its_arguments(work, install, api, tmp_path) -> None:
    job = _patch_job(work, install, api, tmp_path)

    assert helper_main(["app", "--patch-d2", str(job), "--job-sha256", job_digest(job)]) == 0
    assert is_patched((install / "D2.qml").read_text())


@pytest.mark.parametrize(
    "argv",
    [
        ["app", "--patch-d2"],
        ["app", "--patch-d2", "job.json"],
        ["app", "--patch-d2", "job.json", "--job-sha256"],
    ],
)
def test_the_helper_refuses_half_a_command_line(argv: list[str]) -> None:
    assert helper_main(argv) == 2


# ── The result goes where the job is, and nowhere else ──────────────────────


def test_the_result_is_written_beside_the_job_whatever_the_job_says(
    work, install, api, tmp_path
) -> None:
    elsewhere = tmp_path / "elsewhere" / "passwd"
    job = _patch_job(work, install, api, tmp_path, result=str(elsewhere))

    run_job(job, job_digest(job))

    assert (work / RESULT_NAME).is_file()
    assert not elsewhere.exists() and not elsewhere.parent.exists()


def test_the_result_is_not_written_through_a_link(work, install, api, tmp_path) -> None:
    victim = tmp_path / "victim.conf"
    victim.write_text("keep me")
    _link(work / RESULT_NAME, victim)
    job = _patch_job(work, install, api, tmp_path)

    run_job(job, job_digest(job))

    assert victim.read_text() == "keep me"


# ── The target has to be a Traktor D2 folder ────────────────────────────────


@pytest.mark.parametrize(
    "parts",
    [
        ("Traktor Pro 4.app", "Contents", "Resources", "qml", "CSI", "D2"),
        ("Native Instruments", "Traktor Pro 3", "qml", "csi", "d2"),
    ],
)
def test_a_d2_folder_inside_traktor_is_one(tmp_path, parts) -> None:
    assert is_traktor_d2(tmp_path.joinpath(*parts))


@pytest.mark.parametrize(
    "parts",
    [
        ("Documents",),
        ("traktor", "csi"),  # the old rule let this through
        ("traktor", "csi", "somewhere", "else"),
        ("Traktor Pro 4.app", "qml", "CSI", "S4MK2"),
        ("Music", "CSI", "D2"),  # no Traktor above it
    ],
)
def test_anything_else_is_not(tmp_path, parts) -> None:
    assert not is_traktor_d2(tmp_path.joinpath(*parts))


def test_a_target_that_only_mentions_traktor_is_refused(work, api, tmp_path) -> None:
    lookalike = tmp_path / "traktor" / "csi"
    lookalike.mkdir(parents=True)
    (lookalike / "D2.qml").write_text(NI_STYLE)
    job = _patch_job(work, lookalike, api, tmp_path)

    assert run_job(job, job_digest(job)) == 1

    assert not is_patched((lookalike / "D2.qml").read_text())
    assert "CSI" in _result(work)["error"]


def test_a_link_shaped_like_traktor_does_not_redirect_the_write(work, api, tmp_path) -> None:
    victim = tmp_path / "Documents"
    victim.mkdir()
    (victim / "D2.qml").write_text(NI_STYLE)
    csi = tmp_path / "Traktor Pro 4.app" / "qml" / "CSI"
    csi.mkdir(parents=True)
    _link(csi / "D2", victim, directory=True)
    job = _patch_job(work, csi / "D2", api, tmp_path)

    assert run_job(job, job_digest(job)) == 1

    assert not is_patched((victim / "D2.qml").read_text())
    assert sorted(p.name for p in victim.iterdir()) == ["D2.qml"]


# ── The version is a label, not a path ──────────────────────────────────────


@pytest.mark.parametrize("version", ["..", "../../etc", "a/b", "a\\b", "", "."])
def test_a_version_that_is_a_path_is_refused(work, install, api, tmp_path, version: str) -> None:
    job = _patch_job(work, install, api, tmp_path, version=version)

    assert run_job(job, job_digest(job)) == 1

    assert not is_patched((install / "D2.qml").read_text())
    assert "version" in _result(work)["error"]


# ── Links left in the backup folder ─────────────────────────────────────────


def test_a_backup_is_not_written_through_a_link(install, tmp_path) -> None:
    victim = tmp_path / "victim.conf"
    planted = tmp_path / "backups" / VERSION / "D2"
    planted.mkdir(parents=True)
    _link(planted / "D2.qml", victim)  # dangling: not "a file"

    with pytest.raises(BackupError, match="link"):
        back_up(install, tmp_path / "backups", VERSION)

    assert not victim.exists()


def test_a_backup_folder_that_is_a_link_is_refused(install, tmp_path) -> None:
    victim = tmp_path / "victim"
    victim.mkdir()
    (tmp_path / "backups").mkdir()
    _link(tmp_path / "backups" / VERSION, victim, directory=True)

    with pytest.raises(BackupError, match="link"):
        back_up(install, tmp_path / "backups", VERSION)

    assert list(victim.iterdir()) == []


def test_the_manifest_is_not_written_through_a_link(install, tmp_path) -> None:
    victim = tmp_path / "victim.conf"
    victim.write_text("keep me")
    (tmp_path / "backups" / VERSION).mkdir(parents=True)
    _link(tmp_path / "backups" / VERSION / "manifest.json", victim)

    back_up(install, tmp_path / "backups", VERSION)

    assert victim.read_text() == "keep me"
    manifest = tmp_path / "backups" / VERSION / "manifest.json"
    assert not manifest.is_symlink()
    assert json.loads(manifest.read_text())["version"] == VERSION


def test_a_link_in_the_backup_is_not_restored(install, tmp_path) -> None:
    secret = tmp_path / "secret.txt"
    secret.write_text("not Traktor's")
    back_up(install, tmp_path / "backups", VERSION)
    _link(tmp_path / "backups" / VERSION / "D2" / "Planted.qml", secret)

    restore(install, tmp_path / "backups", VERSION)

    assert not (install / "Planted.qml").exists()


# ── Restore checks the backup before it removes anything ────────────────────


def test_a_damaged_backup_leaves_traktor_as_it_is(install, tmp_path) -> None:
    original = (install / "D2.qml").read_text()
    back_up(install, tmp_path / "backups", VERSION)
    patched = patch_qml(original)
    (install / "D2.qml").write_text(patched)
    (tmp_path / "backups" / VERSION / "D2" / "D2.qml").write_text("trunca")

    with pytest.raises(BackupError, match="D2.qml"):
        restore(install, tmp_path / "backups", VERSION)

    assert (install / "D2.qml").read_text() == patched
    assert (install / "Extra.qml").is_file()


def test_a_backup_missing_a_file_leaves_traktor_as_it_is(install, tmp_path) -> None:
    back_up(install, tmp_path / "backups", VERSION)
    os.remove(tmp_path / "backups" / VERSION / "D2" / "Extra.qml")

    with pytest.raises(BackupError, match="Extra.qml"):
        restore(install, tmp_path / "backups", VERSION)

    assert (install / "Extra.qml").is_file()


def test_a_sound_backup_is_restored(install, tmp_path) -> None:
    original = (install / "D2.qml").read_text()
    back_up(install, tmp_path / "backups", VERSION)
    (install / "D2.qml").write_text(patch_qml(original))
    (install / "Api").mkdir()
    (install / "Api" / "ApiModule.qml").write_text("// ours\n")

    restore(install, tmp_path / "backups", VERSION)

    assert (install / "D2.qml").read_text() == original
    assert not (install / "Api").exists()
