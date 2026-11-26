"""Patching Traktor's own D2 mapping instead of replacing it.

The app used to ship a `D2.qml` that *replaced* NI's wholesale, so anyone with
real Kontrol D2 hardware lost the controller. The fix is to add our module to
the file NI shipped: one import and one element, leaving everything else.

**No NI file appears in this repo.** The fixtures here are synthetic QML with
the same shape — brace on its own line, several imports, a populated
``Mapping`` block. Shipping Traktor's own QML is the thing being removed.
"""

from __future__ import annotations

import errno
import json
import os
import stat
import sys
import time
from pathlib import Path

import pytest

from core import d2_patch
from core.d2_patch import (
    MODULE_NAME,
    PATCH_SENTINEL,
    BackupError,
    PatchResult,
    _write_atomic,
    apply,
    back_up,
    is_patched,
    patch_qml,
    restore,
    run_job,
    unpatch_qml,
    verify_copy,
)
from core.elevate import job_digest, read_result

# NI's real file puts the brace on its own line after `Mapping`.
NI_STYLE = """
import CSI 1.0
import QtQuick 2.0

import "../../Defines"
import "../Common"

Mapping
{
  LoopModePads
  {
    name: "loop_mode_pads"
  }

  MappingPropertyDescriptor { id: useMIDIControls; value: false }
}
"""

BRACE_SAME_LINE = """import CSI 1.0

Mapping {
  Wheel { name: "wheel" }
}
"""


# ── Patching ────────────────────────────────────────────────────────────────


def test_patch_adds_the_import_and_the_module() -> None:
    out = patch_qml(NI_STYLE)
    assert 'import "Api"' in out
    assert f'ApiModule {{ name: "{MODULE_NAME}" }}' in out


def test_patch_keeps_everything_ni_wrote() -> None:
    """The whole point: their mapping survives, so D2 hardware still works."""
    out = patch_qml(NI_STYLE)
    for line in NI_STYLE.splitlines():
        if line.strip():
            assert line in out, f"lost: {line!r}"


def test_patch_puts_the_module_inside_the_mapping_block() -> None:
    out = patch_qml(NI_STYLE)
    body = out[out.index("Mapping") :]
    module_at = body.index("ApiModule")
    assert body.index("{") < module_at, "must be after the opening brace"
    assert module_at < body.rindex("}"), "and before the closing one"


def test_patch_handles_the_brace_on_the_same_line() -> None:
    out = patch_qml(BRACE_SAME_LINE)
    assert "ApiModule" in out
    assert 'Wheel { name: "wheel" }' in out
    body = out[out.index("Mapping") :]
    assert body.index("{") < body.index("ApiModule")


def test_patch_puts_the_import_after_the_existing_ones() -> None:
    out = patch_qml(NI_STYLE)
    lines = out.splitlines()
    ours = next(i for i, line in enumerate(lines) if 'import "Api"' in line)
    last_theirs = max(
        i
        for i, line in enumerate(lines)
        if line.startswith("import ") and 'import "Api"' not in line
    )
    assert ours > last_theirs


def test_patch_is_marked() -> None:
    assert PATCH_SENTINEL in patch_qml(NI_STYLE)
    assert is_patched(patch_qml(NI_STYLE))
    assert not is_patched(NI_STYLE)


def test_patching_twice_changes_nothing() -> None:
    """Idempotent, so a retry after a failure is safe."""
    once = patch_qml(NI_STYLE)
    assert patch_qml(once) == once


def test_unpatch_restores_the_original_exactly() -> None:
    assert unpatch_qml(patch_qml(NI_STYLE)) == NI_STYLE


def test_unpatch_of_an_unpatched_file_is_a_no_op() -> None:
    assert unpatch_qml(NI_STYLE) == NI_STYLE


def test_a_file_with_no_mapping_block_is_refused() -> None:
    """Better to refuse than to write a QML file Traktor cannot load."""
    with pytest.raises(ValueError, match="Mapping"):
        patch_qml("import CSI 1.0\n// nothing here\n")


def test_an_empty_file_is_refused() -> None:
    with pytest.raises(ValueError):
        patch_qml("")


# ── Backup ──────────────────────────────────────────────────────────────────


def _d2(tmp_path: Path) -> Path:
    # Shaped like a real install: run_job refuses a target that is not inside
    # a Traktor CSI directory.
    src = tmp_path / "Traktor Pro 4.app" / "qml" / "CSI" / "D2"
    src.mkdir(parents=True)
    (src / "D2.qml").write_text(NI_STYLE)
    (src / "Extra.qml").write_text("// something else NI ships\n")
    return src


def test_backup_copies_every_file(tmp_path) -> None:
    src = _d2(tmp_path)
    dest = back_up(src, tmp_path / "backup", "Traktor Pro 4")
    assert (dest / "D2.qml").read_text() == NI_STYLE
    assert (dest / "Extra.qml").is_file()


def test_backup_writes_a_manifest(tmp_path) -> None:
    src = _d2(tmp_path)
    dest = back_up(src, tmp_path / "backup", "Traktor Pro 4")
    manifest = json.loads((dest.parent / "manifest.json").read_text())
    assert manifest["version"] == "Traktor Pro 4"
    assert set(manifest["files"]) == {"D2.qml", "Extra.qml"}
    assert all(len(h) == 64 for h in manifest["files"].values())


def test_backup_refuses_to_overwrite_an_existing_one(tmp_path) -> None:
    """The first copy is the pristine one. A second run would otherwise back
    up our own patched files over it — which is how the only clean copy on
    this machine was lost once already."""
    src = _d2(tmp_path)
    back_up(src, tmp_path / "backup", "Traktor Pro 4")
    (src / "D2.qml").write_text(patch_qml(NI_STYLE))

    with pytest.raises(BackupError, match="already"):
        back_up(src, tmp_path / "backup", "Traktor Pro 4")
    kept = tmp_path / "backup" / "Traktor Pro 4" / "D2" / "D2.qml"
    assert kept.read_text() == NI_STYLE, "the pristine copy survived"


def test_backups_are_per_traktor_version(tmp_path) -> None:
    src = _d2(tmp_path)
    back_up(src, tmp_path / "backup", "Traktor Pro 4")
    other = back_up(src, tmp_path / "backup", "Traktor Pro 3")
    assert other.is_dir()


def test_verify_passes_on_a_good_copy(tmp_path) -> None:
    src = _d2(tmp_path)
    dest = back_up(src, tmp_path / "backup", "Traktor Pro 4")
    assert verify_copy(src, dest) == []


def test_verify_catches_a_truncated_copy(tmp_path) -> None:
    src = _d2(tmp_path)
    dest = back_up(src, tmp_path / "backup", "Traktor Pro 4")
    (dest / "D2.qml").write_text(NI_STYLE[:20])
    assert verify_copy(src, dest) == ["D2.qml"]


def test_verify_catches_a_missing_file(tmp_path) -> None:
    src = _d2(tmp_path)
    dest = back_up(src, tmp_path / "backup", "Traktor Pro 4")
    (dest / "Extra.qml").unlink()
    assert verify_copy(src, dest) == ["Extra.qml"]


def test_backup_of_a_missing_source_is_refused(tmp_path) -> None:
    with pytest.raises(BackupError):
        back_up(tmp_path / "nope", tmp_path / "backup", "Traktor Pro 4")


# ── Restore ─────────────────────────────────────────────────────────────────


def test_restore_puts_the_original_back(tmp_path) -> None:
    src = _d2(tmp_path)
    back_up(src, tmp_path / "backup", "Traktor Pro 4")
    (src / "D2.qml").write_text(patch_qml(NI_STYLE))
    (src / "Api").mkdir()
    (src / "Api" / "ApiModule.qml").write_text("// ours\n")

    restore(src, tmp_path / "backup", "Traktor Pro 4")
    assert (src / "D2.qml").read_text() == NI_STYLE
    assert not (src / "Api").exists(), "our module is removed too"


def test_restore_without_a_backup_is_refused(tmp_path) -> None:
    src = _d2(tmp_path)
    with pytest.raises(BackupError):
        restore(src, tmp_path / "backup", "Traktor Pro 4")


# ── The helper subcommand ───────────────────────────────────────────────────
# The elevated side is our own binary run with --patch-d2 <job.json>. A
# separate python.exe does not exist in a frozen build, and running our signed
# binary also keeps the UAC publisher name ours rather than a bare
# interpreter's.


def test_helper_runs_the_job_and_writes_a_result(tmp_path) -> None:
    src = _d2(tmp_path)
    api = tmp_path / "Api"
    api.mkdir()
    (api / "ApiModule.qml").write_text("// ours\n")

    job = tmp_path / "job.json"
    result = tmp_path / "result.json"
    job.write_text(
        json.dumps(
            {
                "action": "patch-d2",
                "csi_d2": str(src),
                "api": str(api),
                "backup_root": str(tmp_path / "b"),
                "version": "Traktor Pro 4",
                "result": str(result),
            }
        )
    )

    assert run_job(job, job_digest(job)) == 0
    assert json.loads(result.read_text())["ok"] is True
    assert is_patched((src / "D2.qml").read_text())
    assert (src / "Api" / "ApiModule.qml").is_file()


def test_helper_reports_failure_without_raising(tmp_path) -> None:
    job = tmp_path / "job.json"
    result = tmp_path / "result.json"
    job.write_text(
        json.dumps(
            {
                "action": "patch-d2",
                "csi_d2": str(tmp_path / "nowhere"),
                "api": str(tmp_path),
                "backup_root": str(tmp_path / "b"),
                "version": "v",
                "result": str(result),
            }
        )
    )

    assert run_job(job, job_digest(job)) == 1
    written = json.loads(result.read_text())
    assert written["ok"] is False
    assert written["step"] == "locate"


def test_helper_rejects_an_unknown_action(tmp_path) -> None:
    job = tmp_path / "job.json"
    job.write_text(json.dumps({"action": "rm -rf", "result": str(tmp_path / "r.json")}))
    assert run_job(job, job_digest(job)) != 0


def test_helper_rejects_a_target_outside_a_traktor_install(tmp_path) -> None:
    """A guard against a wrong or derived path being handed to something
    running as root."""
    victim = tmp_path / "Documents"
    victim.mkdir()
    (victim / "D2.qml").write_text(NI_STYLE)

    job = tmp_path / "job.json"
    result = tmp_path / "result.json"
    job.write_text(
        json.dumps(
            {
                "action": "patch-d2",
                "csi_d2": str(victim),
                "api": str(tmp_path),
                "backup_root": str(tmp_path / "b"),
                "version": "v",
                "result": str(result),
            }
        )
    )

    assert run_job(job, job_digest(job)) == 1
    assert "CSI" in json.loads(result.read_text())["error"]


def test_helper_restores_from_a_job(tmp_path) -> None:
    """Restore runs elevated too — the files it replaces are root-owned."""
    src = _d2(tmp_path)
    original = (src / "D2.qml").read_text()
    back_up(src, tmp_path / "b", "Traktor Pro 4")
    (src / "D2.qml").write_text(patch_qml(original))
    (src / "Api").mkdir()
    (src / "Api" / "ApiModule.qml").write_text("// ours\n")

    job = tmp_path / "job.json"
    result = tmp_path / "result.json"
    job.write_text(
        json.dumps(
            {
                "action": "restore-d2",
                "csi_d2": str(src),
                "backup_root": str(tmp_path / "b"),
                "version": "Traktor Pro 4",
                "result": str(result),
            }
        )
    )

    assert run_job(job, job_digest(job)) == 0
    assert json.loads(result.read_text())["ok"] is True
    assert (src / "D2.qml").read_text() == original
    assert not (src / "Api").exists()


def test_helper_restore_without_a_backup_reports_it(tmp_path) -> None:
    src = _d2(tmp_path)
    job = tmp_path / "job.json"
    result = tmp_path / "result.json"
    job.write_text(
        json.dumps(
            {
                "action": "restore-d2",
                "csi_d2": str(src),
                "backup_root": str(tmp_path / "none"),
                "version": "v",
                "result": str(result),
            }
        )
    )
    assert run_job(job, job_digest(job)) == 1
    written = json.loads(result.read_text())
    assert written["ok"] is False and "backup" in written["error"].lower()


def test_helper_restore_also_guards_the_target(tmp_path) -> None:
    victim = tmp_path / "Documents"
    victim.mkdir()
    job = tmp_path / "job.json"
    result = tmp_path / "result.json"
    job.write_text(
        json.dumps(
            {
                "action": "restore-d2",
                "csi_d2": str(victim),
                "backup_root": str(tmp_path / "b"),
                "version": "v",
                "result": str(result),
            }
        )
    )
    assert run_job(job, job_digest(job)) == 1
    assert "CSI" in json.loads(result.read_text())["error"]


# ── macOS bundle-write refusal ─────────────────────────────────────────────
# Modifying another application's bundle is refused without the grant on
# macOS 13+. Without it the kernel returns EPERM *to root* — the directory is
# drwxrwxr-x root:wheel, so no mode-based denial is even possible. The real
# failure read:
#     copy the Api module: [Errno 1] Operation not permitted:
#     '/Applications/…/Traktor Pro 4.app/…/CSI/D2/Api'
# which tells the user nothing they can act on.


def test_a_blocked_bundle_write_is_named_as_such(tmp_path, monkeypatch) -> None:

    src = _d2(tmp_path)
    api = tmp_path / "Api"
    api.mkdir()
    (api / "ApiModule.qml").write_text("// ours\n")

    back_up(src, tmp_path / "b", "Traktor Pro 4")  # so apply skips it

    def refuse(*_a, **_k):
        raise PermissionError(errno.EPERM, "Operation not permitted")

    monkeypatch.setattr("core.d2_patch.shutil.copy2", refuse)
    result = apply(src, api, tmp_path / "b", "Traktor Pro 4")
    assert result.step == "copy the Api module"

    assert not result.ok
    assert result.needs_full_disk_access, (
        "EPERM into a bundle is the App Management grant, not a bug"
    )
    assert "Full Disk Access" in result.error


def test_an_ordinary_permission_error_is_not_mistaken_for_it(tmp_path, monkeypatch) -> None:
    """EACCES is a plain permission problem; only EPERM means the grant."""

    src = _d2(tmp_path)
    api = tmp_path / "Api"
    api.mkdir()
    (api / "ApiModule.qml").write_text("// ours\n")

    back_up(src, tmp_path / "b", "Traktor Pro 4")

    def refuse(*_a, **_k):
        raise PermissionError(errno.EACCES, "Permission denied")

    monkeypatch.setattr("core.d2_patch.shutil.copy2", refuse)
    result = apply(src, api, tmp_path / "b", "Traktor Pro 4")
    assert not result.ok
    assert not result.needs_full_disk_access


def test_the_flag_survives_the_json_round_trip(tmp_path) -> None:
    """The UI reads the result back out of a file written by the helper."""

    out = tmp_path / "result.json"
    out.write_text(
        PatchResult(False, "copy the Api module", "nope", needs_full_disk_access=True).as_json()
    )
    assert read_result(out).needs_full_disk_access is True


def test_a_successful_result_does_not_set_it(tmp_path) -> None:
    assert not PatchResult(True, "done").needs_full_disk_access


#: ── The patch that was there and never ran ─────────────────────────────────
#
# Qt keeps a compiled-QML cache and validates it against the *source mtime*.
# _write_atomic used to call shutil.copystat, which restores the original
# file's timestamps onto the patched one — so the cache stayed "valid",
# Traktor went on running the pre-patch mapping, and the install looked
# inert: correct bytes on disk, nothing sent, no error anywhere.


def test_patching_moves_the_mtime_forward(tmp_path) -> None:
    """The bug this file exists to never repeat."""
    qml = tmp_path / "D2.qml"
    qml.write_text("original", encoding="utf-8")
    old = time.time() - 86_400  # a day ago, as a shipped file would be
    os.utime(qml, (old, old))
    before = qml.stat().st_mtime

    _write_atomic(qml, "patched")

    assert qml.read_text(encoding="utf-8") == "patched"
    assert qml.stat().st_mtime > before, (
        "the mtime must move, or Qt serves the stale compiled QML and the patch never runs"
    )


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="permission bits are POSIX: Windows keeps one read-only flag per "
    "file, and chmod there can set nothing else",
)
def test_patching_keeps_the_files_mode(tmp_path) -> None:
    """Traktor's QML is root:wheel 775 and has to stay that way."""
    qml = tmp_path / "D2.qml"
    qml.write_text("original", encoding="utf-8")
    os.chmod(qml, 0o775)

    _write_atomic(qml, "patched")

    assert stat.S_IMODE(qml.stat().st_mode) == 0o775


def test_patching_writes_the_platforms_line_endings(tmp_path, monkeypatch) -> None:
    """The QML is read as text and written back as text, as it always was."""
    monkeypatch.setattr(d2_patch.os, "linesep", "\r\n")
    qml = tmp_path / "D2.qml"
    qml.write_text("original", encoding="utf-8")

    _write_atomic(qml, "a\nb\n")

    assert qml.read_bytes() == b"a\r\nb\r\n"


def test_patching_leaves_no_sibling_behind(tmp_path) -> None:
    qml = tmp_path / "D2.qml"
    qml.write_text("original", encoding="utf-8")

    _write_atomic(qml, "patched")

    assert [p.name for p in tmp_path.iterdir()] == ["D2.qml"]


def test_clearing_the_qml_cache_empties_it(tmp_path, monkeypatch) -> None:
    cache = tmp_path / "qmlcache"
    cache.mkdir()
    (cache / "a.qmlc").write_bytes(b"stale")
    (cache / "b.jsc").write_bytes(b"stale")
    monkeypatch.setattr(d2_patch, "_QML_CACHE", cache)

    assert d2_patch.clear_qml_cache() == 2
    assert list(cache.iterdir()) == []


def test_clearing_a_cache_that_is_not_there_is_not_an_error(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(d2_patch, "_QML_CACHE", tmp_path / "nope")
    assert d2_patch.clear_qml_cache() == 0


def test_the_helpers_own_view_of_the_grant_comes_back_too(tmp_path) -> None:
    """The two sides disagreed: granted in the app, refused as root.

    Only the elevated process can say what *it* was allowed, and it has no
    configured logger of its own, so the reading travels in the result file
    or not at all.
    """
    out = tmp_path / "result.json"
    out.write_text(
        PatchResult(
            False,
            "archive the originals",
            "nope",
            needs_full_disk_access=True,
            tcc="euid=0 state=2",
        ).as_json()
    )
    assert read_result(out).tcc == "euid=0 state=2"
