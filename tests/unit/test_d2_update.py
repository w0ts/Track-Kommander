"""Noticing that Traktor was updated out from under the patch.

A Traktor update replaces the whole application bundle, so our two lines in
``D2.qml`` and the ``Api/`` folder beside it are gone. Nothing announces this:
the bridge simply stops, and the connection rows read exactly like a machine
where nobody ever installed it — "Not installed", go and install it. That
answer is wrong in the way that matters, because it doesn't say *why* a thing
that worked yesterday stopped, and because the honest action is one click.

The signal is the backup directory. Backups are per Traktor version, so the
moment there is a backup for some other version but none for the one
installed now, this machine had the bridge and then Traktor moved.

Three-valued on purpose: "no backups at all" is a first-time install, not an
update, and a still-patched install is not an update either.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from core.d2_patch import (
    apply,
    back_up,
    backed_up_versions,
    has_backup,
    is_patched,
    patch_qml,
)
from core.d2_status import update_check

NI_STYLE = """
import CSI 1.0

Mapping
{
  Thing {}
}
"""

OLD = "Traktor Pro 4 (4.5.1)"
NEW = "Traktor Pro 4 (4.6.0)"


def _install(tmp_path: Path, *, patched: bool = False) -> Path:
    d2 = tmp_path / "Traktor Pro 4.app" / "qml" / "CSI" / "D2"
    d2.mkdir(parents=True)
    (d2 / "D2.qml").write_text(patch_qml(NI_STYLE) if patched else NI_STYLE)
    return d2


def _backup_of(tmp_path: Path, version: str, root: Path) -> None:
    """Record a backup for *version*, as installing the bridge would."""
    src = tmp_path / "src" / version
    src.mkdir(parents=True)
    (src / "D2.qml").write_text(NI_STYLE)
    back_up(src, root, version)


# ── Which versions have we ever backed up? ─────────────────────────────────


def test_no_backups_at_all(tmp_path) -> None:
    assert backed_up_versions(tmp_path / "backup") == ()


def test_a_missing_backup_root_is_not_an_error(tmp_path) -> None:
    """Asked before anything has ever been installed."""
    assert backed_up_versions(tmp_path / "nope" / "never") == ()


def test_every_backed_up_version_is_listed(tmp_path) -> None:
    root = tmp_path / "backup"
    _backup_of(tmp_path, OLD, root)
    _backup_of(tmp_path, NEW, root)
    assert set(backed_up_versions(root)) == {OLD, NEW}


def test_the_most_recent_backup_comes_first(tmp_path) -> None:
    """Which Traktor we came *from* is the one we backed up last."""
    root = tmp_path / "backup"
    _backup_of(tmp_path, OLD, root)
    _backup_of(tmp_path, NEW, root)
    # tmpfs timestamps are coarse enough to tie; make the order unambiguous.
    later = time.time() + 10
    os.utime(root / NEW, (later, later))
    assert backed_up_versions(root)[0] == NEW


def test_an_empty_directory_is_not_a_backup(tmp_path) -> None:
    """A half-finished or hand-cleared backup holds nothing to restore."""
    root = tmp_path / "backup"
    _backup_of(tmp_path, OLD, root)
    (root / NEW / "D2").mkdir(parents=True)
    assert backed_up_versions(root) == (OLD,)


def test_a_folder_finder_touched_is_not_a_backup(tmp_path) -> None:
    """macOS writes .DS_Store just for looking at a folder. Counting it would
    let ``apply`` believe a pristine copy exists and patch Traktor without
    one — the one thing this module says it will never do."""
    root = tmp_path / "backup"
    (root / NEW / "D2").mkdir(parents=True)
    (root / NEW / "D2" / ".DS_Store").write_bytes(b"\x00")
    assert backed_up_versions(root) == ()
    assert not has_backup(root, NEW)


def test_every_guard_agrees_on_what_a_backup_is(tmp_path) -> None:
    """``apply`` skips the backup step when one is already there, ``back_up``
    refuses to overwrite one, ``restore`` reads one and the status chain
    counts them. Four guards, one answer — they disagreed once, in the
    direction that patches without a backup."""
    root = tmp_path / "backup"
    d2 = root / NEW / "D2"
    d2.mkdir(parents=True)
    (d2 / ".DS_Store").write_bytes(b"\x00")

    src = tmp_path / "src"
    src.mkdir()
    (src / "D2.qml").write_text(NI_STYLE)
    api = tmp_path / "Api"
    api.mkdir()
    (api / "ApiModule.qml").write_text("// ours\n")
    target = _install(tmp_path)

    result = apply(target, api, root, NEW)
    assert result.ok, result.error
    assert result.backed_up, "patched without recording a backup"
    assert has_backup(root, NEW)
    # The backup is the pristine file, not the patched one.
    assert (root / NEW / "D2" / "D2.qml").read_text() == NI_STYLE
    assert is_patched((target / "D2.qml").read_text())


# ── Was Traktor updated? ───────────────────────────────────────────────────


def test_a_fresh_traktor_after_an_older_one_is_an_update(tmp_path) -> None:
    root = tmp_path / "backup"
    _backup_of(tmp_path, OLD, root)
    got = update_check(_install(tmp_path), root, NEW)
    assert got is not None
    assert got.previous == OLD
    assert got.current == NEW


def test_a_first_install_is_not_an_update(tmp_path) -> None:
    """Nothing was ever patched here, so there is nothing to have lost."""
    root = tmp_path / "backup"
    assert update_check(_install(tmp_path), root, NEW) is None


def test_a_still_patched_install_is_not_an_update(tmp_path) -> None:
    """Traktor may well have been updated and re-patched already."""
    root = tmp_path / "backup"
    _backup_of(tmp_path, OLD, root)
    assert update_check(_install(tmp_path, patched=True), root, NEW) is None


def test_the_same_version_is_not_an_update(tmp_path) -> None:
    """Unpatched with its own backup present means someone restored or
    uninstalled it — a different story, and not one to blame on Traktor."""
    root = tmp_path / "backup"
    _backup_of(tmp_path, NEW, root)
    assert update_check(_install(tmp_path), root, NEW) is None


def test_a_different_traktor_on_the_same_machine_is_not_an_update(tmp_path) -> None:
    """Two Traktors side by side share one backup directory, and the app
    offers a picker to switch between them. Patching 4 and then selecting an
    untouched 3 must not read as "4 was updated to 3" — it is a first-time
    install for a different product that happens to share the folder."""
    root = tmp_path / "backup"
    _backup_of(tmp_path, OLD, root)
    assert update_check(_install(tmp_path), root, "Traktor Pro 3 (3.11.1)") is None


def test_an_update_of_the_other_traktor_is_still_caught(tmp_path) -> None:
    """Scoping to one product must not make it blind to that product."""
    root = tmp_path / "backup"
    _backup_of(tmp_path, "Traktor Pro 3 (3.11.0)", root)
    _backup_of(tmp_path, OLD, root)
    got = update_check(_install(tmp_path), root, "Traktor Pro 3 (3.11.1)")
    assert got is not None
    assert got.previous == "Traktor Pro 3 (3.11.0)"


def test_a_version_that_cannot_be_read_says_nothing(tmp_path) -> None:
    """Traktor's Info.plist could not be read, so the label carries no
    version. An update is precisely the thing that cannot be detected without
    one — and this is also the label used when no install was resolved."""
    root = tmp_path / "backup"
    _backup_of(tmp_path, OLD, root)
    d2 = _install(tmp_path)
    for label in ("Traktor Pro 4", "Traktor"):
        assert update_check(d2, root, label) is None


def test_an_unlocatable_install_says_nothing(tmp_path) -> None:
    root = tmp_path / "backup"
    _backup_of(tmp_path, OLD, root)
    assert update_check(None, root, NEW) is None


def test_an_unknown_version_says_nothing(tmp_path) -> None:
    """Without a version there is no backup to look for, so no conclusion."""
    root = tmp_path / "backup"
    _backup_of(tmp_path, OLD, root)
    assert update_check(_install(tmp_path), root, "") is None


def test_an_unreadable_qml_says_nothing(tmp_path) -> None:
    """Unknown is not broken — see the rest of the status chain."""
    root = tmp_path / "backup"
    _backup_of(tmp_path, OLD, root)
    d2 = tmp_path / "Traktor Pro 4.app" / "qml" / "CSI" / "D2"
    d2.mkdir(parents=True)
    assert update_check(d2, root, NEW) is None


def test_the_notice_names_both_versions(tmp_path) -> None:
    """It has to be readable in a settings row without further lookup."""
    root = tmp_path / "backup"
    _backup_of(tmp_path, OLD, root)
    got = update_check(_install(tmp_path), root, NEW)
    assert got is not None
    assert OLD in got.detail and NEW in got.detail
    assert got.summary and len(got.summary) < 60
