"""Settings, Traktor tab: installing the D2 module into Traktor, and undoing it.

Both jobs write into Traktor's own bundle through an elevated helper, so
they are kept apart from the status rows in ``ui.settings_d2`` that only
read. Nothing here runs unless a person presses Install or Restore.
"""

from __future__ import annotations

import contextlib
import sys
from pathlib import Path

from core.runtime import in_traktor
from ui.settings_common import (
    ELEVATION_NOTE,
    log,
)


class _D2InstallMixin:
    """D2 install and restore. Widgets and state belong to ``SettingsTab``."""

    def _d2_paths(self):
        """(CSI/D2 folder, our Api source, backup root, version label)."""
        from core.runtime import bundle_data_dir, user_data_dir

        inst = self._current_install()
        csi = self._resolve_qml_path()
        if csi is None:
            return None
        # _resolve_qml_path returns the deepest segment that exists, which may
        # stop short of CSI on a partial install. Only a directory actually
        # called D2 will do.
        if csi.name.upper() == "D2":
            d2 = csi
        elif (csi / "D2").is_dir():
            d2 = csi / "D2"
        elif (csi / "CSI" / "D2").is_dir():
            d2 = csi / "CSI" / "D2"
        else:
            return None
        api = bundle_data_dir() / "Traktor" / "D2" / "Api"
        backups = user_data_dir() / "d2-backup"
        version = inst.label if inst is not None else "Traktor"
        return d2, api, backups, version

    def _ensure_can_modify_traktor(self) -> bool:
        """Require Full Disk Access, which is the grant that decides this.

        The write happens in the elevated helper rather than in this process.
        macOS names *App Management* as the permission for modifying another
        app's bundle, but that grant does not reach the helper: with it on,
        the toggle set and the app restarted, the root write was still
        refused; adding Full Disk Access let the same build through.

        Nothing here asks for App Management, by design. It is never the
        thing that unblocks the job, and requesting it raises a system prompt
        — one that appeared on every attempt and made the install impossible
        to test without it.

        Returns False when Full Disk Access is missing, having already
        explained what to do about it.
        """
        if sys.platform != "darwin":
            return True

        from core.permissions import full_disk_access_granted

        if not full_disk_access_granted():
            self._explain_full_disk_access()
            return False
        return True

    @contextlib.contextmanager
    def _d2_busy(self):
        """Hold the D2 buttons down for the length of one job.

        A job runs an elevated helper and then a result dialog, and both turn
        the event loop — which is when any clicks made in the meantime are
        delivered. Without this, an impatient second press of Install starts
        a second job, with its own password prompt and helper run. Qt drops a
        queued click on a *disabled* widget, so the hold is what makes one
        press mean one job, and it has to last through the dialogs too.
        """
        buttons = [
            b
            for b in (getattr(self, "d2_patch_btn", None), getattr(self, "d2_restore_btn", None))
            if b is not None
        ]
        was = [b.isEnabled() for b in buttons]
        for button in buttons:
            button.setEnabled(False)
        try:
            yield
        finally:
            for button, enabled in zip(buttons, was, strict=True):
                button.setEnabled(enabled)

    def _patch_d2(self) -> None:
        if in_traktor():  # never install the D2 bridge from inside Traktor (User, 2026-11-24)
            return
        with self._d2_busy():
            self._patch_d2_job()

    def _patch_d2_job(self) -> None:
        import tempfile

        from core.d2_patch import (
            ORIGINALS_ZIP,
            RESULT_NAME,
            backup_dir,
            clear_qml_cache,
        )
        from core.d2_status import result_from_disk
        from core.elevate import (
            helper_command,
            read_result,
            run_helper,
            write_job,
        )
        from ui.dialogs import confirm, info, warn

        paths = self._d2_paths()
        if paths is None:
            warn(
                self,
                "Traktor not found",
                "Could not locate Traktor's QML folder. Pick the right installation above first.",
            )
            return
        d2, api, backups, version = paths
        if not api.is_dir():
            warn(self, "Module missing", f"The Api module is not where it should be:\n{api}")
            return
        if not confirm(
            self,
            "Install into Traktor",
            f"Add Track Kommander's module to Traktor's D2 mapping.\n\n"
            f"In one step, as administrator:\n"
            f"  1. back up {d2}\n"
            f"     to {backup_dir(backups, version)}, and verify it\n"
            f"  2. archive Traktor's own files as {ORIGINALS_ZIP},\n"
            f"     left in that same folder so you can open it and check\n"
            f"  3. copy our Api module in beside Traktor's files\n"
            f"  4. add two lines to Traktor's D2.qml\n\n"
            "Everything Native Instruments wrote is kept, so a real Kontrol "
            "D2 keeps working. This can be undone with Restore.\n\n" + ELEVATION_NOTE,
            yes_text="Install",
        ):
            return

        # Before the password prompt, not after it: macOS refuses the write
        # into Traktor's bundle even to root, and being told that once the
        # job has already asked for a password looks like a broken installer.
        if not self._ensure_can_modify_traktor():
            return

        work = Path(tempfile.mkdtemp(prefix="tk-d2-"))
        # Where the helper reports is its to decide, not the job's: always
        # beside the job, under this name.
        result_path = work / RESULT_NAME
        job = write_job(
            work,
            {
                "action": "patch-d2",
                "csi_d2": str(d2),
                "api": str(api),
                "backup_root": str(backups),
                "version": version,
            },
        )
        outcome = run_helper(
            helper_command(), job, "Track Kommander needs to add its module to Traktor's D2 mapping"
        )
        log.info("d2 install: elevation %s (job %s)", outcome.describe(), work)

        if outcome.cancelled:
            info(
                self,
                "Not installed",
                "You declined, so nothing was changed. The two folder "
                "buttons open what you need to do it by hand.",
            )
            return
        if not outcome.ran:
            warn(self, "Could not start", outcome.describe())
            return

        # It ran, so its own result names the stage — the elevation wrapper
        # only reports "exited non-zero", which says nothing useful.
        done = read_result(result_path)
        reported = done.ran
        if not reported:
            # No report. That is not the same as no work: the helper writes
            # its result last, so a finished job whose report never landed
            # used to be announced as a failure over a patched Traktor. Ask
            # the folder instead — and keep the elevation's stderr, which is
            # the only clue to why the report is missing.
            log.warning(
                "d2 install: no result file at %s — reading the folder instead", result_path
            )
            done = result_from_disk(d2, want_installed=True, elevation=outcome)
        log.info("d2 install: %s [helper: %s]", done.describe(), done.tcc)

        if done.ok:
            # Before the "it worked" dialog, because it is part of the work:
            # a stale compiled cache means Traktor keeps running the mapping
            # it had before the patch, and the install looks like it did
            # nothing at all.
            clear_qml_cache()
            # Naming the archive, not just claiming a backup: it is in the
            # folder they are being asked to let us modify, and they can open
            # it and see Traktor's own files for themselves.
            kept = (
                f"\n\nTraktor's own files are archived beside them, in\n"
                f"{done.originals_zip}\n— open it to check."
                if done.originals_zip
                else ""
            )
            info(
                self,
                "Installed",
                "Traktor's D2 mapping now loads Track Kommander's module."
                f"{kept}\n\n"
                "Traktor reads that mapping once, at startup, so restart "
                "Traktor now — the connection should come up. If it "
                "doesn't, check the D2 controller is added in Traktor's "
                "Controller Manager.",
            )
        elif done.needs_full_disk_access:
            self._explain_full_disk_access()
        else:
            # Only the helper's own report can promise nothing was touched:
            # it aborts before writing. Reading the folder afterwards says
            # the module is not there, which is not the same claim.
            untouched = "\n\nTraktor's files were left as they were." if reported else ""
            warn(self, "Failed", f"Stopped at: {done.describe()}{untouched}")
        self._refresh_d2_device()

    #: The Full Disk Access pane.
    _FDA_URL = "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles"

    def _explain_full_disk_access(self) -> None:
        """The grant that actually decides this, and it cannot be prompted.

        App Management is the permission macOS *names* for modifying another
        app's bundle, and on this machine it was not the one that mattered:
        with it granted and the app restarted, the elevated write was still
        refused. The write happens in the helper that runs as root, which
        reaches macOS through the authorisation trampoline rather than as
        Track Kommander — Full Disk Access is the grant that still covers it.

        No prompt exists for it, so the app has to be added by hand and the
        relaunch is not optional: Full Disk Access takes effect at launch.
        """
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        from core.elevate import responsible_app_name

        who = responsible_app_name()
        grantee = f"“{who}”" if who else "this application"

        from ui.dialogs import confirm

        open_it = confirm(
            self,
            "One more permission is needed",
            "Full Disk Access is needed to change Traktor's files.\n\n"
            "The files are written by a helper running as administrator, and "
            "macOS does not extend App Management to it. Full Disk Access is "
            "the grant that covers that step.\n\n"
            "Open System Settings → Privacy & Security → Full Disk Access, "
            f"add {grantee} with the + button, then quit and reopen the app "
            "— this one only takes effect at launch.\n\n"
            "Traktor's own files were not touched.",
            yes_text="Open System Settings",
            no_text="Later",
        )
        if open_it:
            QDesktopServices.openUrl(QUrl(self._FDA_URL))

    def _restore_d2(self) -> None:
        if in_traktor():
            return
        with self._d2_busy():
            self._restore_d2_job()

    def _restore_d2_job(self) -> None:
        import tempfile

        from core.d2_patch import (
            RESULT_NAME,
            backup_dir,
            clear_qml_cache,
            has_backup,
        )
        from core.d2_status import result_from_disk
        from core.elevate import (
            helper_command,
            read_result,
            run_helper,
            write_job,
        )
        from ui.dialogs import confirm, info, warn

        paths = self._d2_paths()
        if paths is None:
            return
        d2, _api, backups, version = paths
        backup = backup_dir(backups, version)
        if not has_backup(backups, version):
            warn(
                self,
                "No backup",
                f"There is no saved copy of Traktor's original D2 files for "
                f"{version}, so there is nothing to restore from.\n\n"
                f"Expected at:\n{backup}",
            )
            return
        if not confirm(
            self,
            "Restore Traktor's files",
            f"Put Traktor's original D2 files back and remove ours.\n\n"
            f"From: {backup}\nTo:   {d2}\n\n" + ELEVATION_NOTE,
            yes_text="Restore",
        ):
            return

        if not self._ensure_can_modify_traktor():
            return

        work = Path(tempfile.mkdtemp(prefix="tk-d2-"))
        result_path = work / RESULT_NAME
        job = write_job(
            work,
            {
                "action": "restore-d2",
                "csi_d2": str(d2),
                "backup_root": str(backups),
                "version": version,
            },
        )
        outcome = run_helper(
            helper_command(), job, "Track Kommander needs to restore Traktor's original D2 files"
        )
        log.info("d2 restore: elevation %s (job %s)", outcome.describe(), work)

        if outcome.cancelled:
            return
        if not outcome.ran:
            warn(self, "Could not start", outcome.describe())
            return
        done = read_result(result_path)
        if not done.ran:
            # The helper writes its report last, so a missing one says
            # nothing about the work. Traktor's folder does.
            log.warning(
                "d2 restore: no result file at %s — reading the folder instead", result_path
            )
            done = result_from_disk(d2, want_installed=False, elevation=outcome)
        log.info("d2 restore: %s [helper: %s]", done.describe(), done.tcc)
        if done.ok:
            # Same reason as the install: without this Traktor can go on
            # running the patched mapping out of its cache after the files
            # underneath have been put back.
            clear_qml_cache()
            info(
                self,
                "Restored",
                "Traktor's original D2 files are back.\n\nTraktor reads "
                "them once, at startup, so restart Traktor for the change "
                "to take effect.",
            )
        elif done.needs_full_disk_access:
            self._explain_full_disk_access()
        else:
            warn(self, "Failed", done.describe())
        self._refresh_d2_device()
