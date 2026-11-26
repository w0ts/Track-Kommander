"""Settings, Traktor tab: the CONNECTION group and the D2 bridge status.

Everything that says whether Traktor and Kommander can talk lives here:
the webhook, mapping, controller and bridge rows, the checks that read the
install from disk, the offer to reinstall after a Traktor update, the
folder openers, and the setters MainWindow calls per message. The
elevated install and restore jobs are in ``ui.settings_d2_install``.

A mixin, not a widget: the rows are widgets of ``SettingsTab`` and the
methods read its ``self._settings`` and install picker, so the page
keeps the object lifetimes and signal wiring it had as one class.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from core import applescript
from core.runtime import in_traktor
from ui.settings_common import (
    D2_EXPECTED_VERSION,
    _build_csi_candidates,
    _stat_key,
    bridge_stats_text,
    log,
)
from ui.style import T


class _D2PageMixin:
    """The D2 bridge page. Its widgets and state belong to ``SettingsTab``."""

    # ── Section 3: the connection ──────────────────────────────────────────

    def _build_connection_group(self):
        """Everything about whether Traktor and Kommander can talk.

        One row per fact, in the order they have to be true in: our server is
        up, the mapping is installed, Traktor has a device that loads it, and
        the bridge is actually sending. It previously carried a webhook status
        row *and* a numbered checklist that asked the same question twice.

        The last two rows (macOS only) are the TCC grants that let us act on
        Traktor once it *is* talking to us.
        """
        grp = QGroupBox("CONNECTION")
        lay = QVBoxLayout(grp)
        lay.setSpacing(8)
        btn_style = "QPushButton { text-align: left; padding: 4px 8px; font-size: 11px; }"

        def _row(title: str) -> tuple[QLabel, QLabel, QHBoxLayout]:
            line = QHBoxLayout()
            line.setSpacing(8)
            dot = QLabel("●")
            dot.setStyleSheet("color: #555577; font-size: 16px;")
            line.addWidget(dot)
            name = QLabel(title)
            name.setStyleSheet(f"color: {T.TEXT_DIM};")
            name.setMinimumWidth(130)
            line.addWidget(name)
            value = QLabel("Checking…")
            value.setStyleSheet(f"color: {T.TEXT};")
            value.setWordWrap(True)
            line.addWidget(value, stretch=1)
            lay.addLayout(line)
            return dot, value, line

        # 1. Our own listener.
        self.status_dot, self.status_msg, row = _row("Webhook server")
        self.status_msg.setText("Not started")
        reconnect = QPushButton("↻  Reconnect")
        reconnect.setToolTip(
            "Rebind the webhook server and ask Traktor's bridge to re-send\n"
            "everything. Use this when the connection looks stuck — it avoids\n"
            "having to restart Kommander or Traktor."
        )
        reconnect.setStyleSheet(btn_style)
        reconnect.clicked.connect(self.reconnect_requested)
        row.addWidget(reconnect)

        # 2. Our module inside Traktor's mapping, with everything that acts
        #    on it on the same line.
        self.d2_dot, self.d2_msg, row = _row("D2 mapping")
        self.d2_patch_btn = QPushButton("Install into Traktor…")
        self.d2_patch_btn.setToolTip(
            "Add our module to Traktor's own D2 mapping. Everything Native\n"
            "Instruments wrote is kept, so real Kontrol D2 hardware keeps\n"
            "working, and the originals are backed up first.\n\n"
            "Needs your password, and Traktor must be closed."
        )
        self.d2_patch_btn.setStyleSheet(btn_style)
        self.d2_patch_btn.clicked.connect(self._patch_d2)
        row.addWidget(self.d2_patch_btn)

        self.d2_restore_btn = QPushButton("Restore")
        self.d2_restore_btn.setToolTip("Put Traktor's original D2 files back and remove ours.")
        self.d2_restore_btn.setStyleSheet(btn_style)
        self.d2_restore_btn.clicked.connect(self._restore_d2)
        row.addWidget(self.d2_restore_btn)

        src_btn = QPushButton("📁  D2  (this app)")
        src_btn.setToolTip("Open the bundled Traktor/ folder — the manual fallback.")
        src_btn.setStyleSheet(btn_style)
        src_btn.clicked.connect(self._open_bundled_traktor)
        row.addWidget(src_btn)

        arrow = QLabel("→")
        arrow.setStyleSheet(f"color: {T.TEXT_DIM}; font-size: 14px;")
        row.addWidget(arrow)

        dst_btn = QPushButton("📂  Traktor QML folder")
        dst_btn.setToolTip("Open Traktor's QML/CSI folder.")
        dst_btn.setStyleSheet(btn_style)
        dst_btn.clicked.connect(self._open_qml_folder)
        row.addWidget(dst_btn)

        # 3. The device that makes Traktor load that mapping at all.
        self.d2_device_dot, self.d2_device_msg, _row3 = _row("D2 controller")

        # 4. Whether it is actually sending. This is the only place the
        #    connection is reported: a separate "Traktor: connected" row said
        #    the same thing from worse data.
        self.traktor_dot, self.traktor_msg, _row4 = _row("Traktor bridge")
        self.traktor_msg.setText("Waiting…")

        self.check_rows: dict[str, tuple[QLabel, QLabel]] = {
            "qml": (self.d2_dot, self.d2_msg),
            "device": (self.d2_device_dot, self.d2_device_msg),
            "api": (self.traktor_dot, self.traktor_msg),
        }

        # The raw counter, kept as a dim line: "Responding" answers the
        # question, this is what you read when diagnosing one that is not.
        self.bridge_stats = QLabel("No data received from Traktor yet")
        self.bridge_stats.setStyleSheet(f"color: {T.TEXT_DIM}; font-size: 11px;")
        lay.addWidget(self.bridge_stats)

        # 5–6. The macOS grants. Talking to Traktor is only half of it: without
        #      these the app can be perfectly connected and still silently fail
        #      to act, so they belong with the other checks rather than off in
        #      a Permissions box on another tab.
        perm_rows = []
        if sys.platform == "darwin":
            for title, what, granted_fn, request_fn, url in self._PERMISSIONS:
                dot, value, btn = self._add_permission_check(
                    _row, title, what, granted_fn, request_fn, url, btn_style
                )
                perm_rows.append((dot, value, btn, granted_fn, what))
        self._perm_rows: tuple = tuple(perm_rows)

        # Shown only while Traktor is not connected.
        self.traktor_hint = QLabel(
            "⚠  Not connecting? In Traktor go to Preferences → Controller"
            ' Manager and add a controller: Device = "Pre Mapped" →'
            ' "Traktor Kontrol D2". That is what makes Traktor load the'
            " mapping — the hardware itself is not needed. If it is already"
            " there, restart Traktor: the mapping is read once, at startup."
        )
        self.traktor_hint.setWordWrap(True)
        self.traktor_hint.setStyleSheet(
            f"color: {T.ORANGE}; font-size: 11px; "
            f"background: #2a1f00; border: 1px solid {T.ORANGE}; "
            f"border-radius: 4px; padding: 6px 8px;"
        )
        lay.addWidget(self.traktor_hint)

        self._refresh_d2_device()
        return grp

    # ── D2: is the controller there, and is the QML patched? ───────────────

    def _set_check(self, key: str, check) -> None:
        """Paint one checklist row from a :class:`core.d2_status.Check`.

        The "api" row is fed on every message from Traktor. Its detail (the
        tooltip) carries the message count and so changes every time, but the
        colour and summary rarely do — restyling a widget is what costs, so
        only the parts that changed are touched.
        """
        painted = getattr(self, "_painted_checks", None)
        if painted is None:
            painted = self._painted_checks = {}
        before = painted.get(key)
        painted[key] = (check.ok, check.summary, check.detail)
        dot, value = self.check_rows[key]
        if before is None or before[0] != check.ok:
            colour = {True: T.GREEN, False: T.ORANGE}.get(check.ok, "#555577")
            dot.setStyleSheet(f"color: {colour}; font-size: 16px;")
            value.setStyleSheet(f"color: {T.TEXT if check.ok else T.TEXT_DIM};")
        if before is None or before[1] != check.summary:
            value.setText(check.summary)
        if before is None or before[2] != check.detail:
            value.setToolTip(check.detail)

    def _traktor_settings_path(self):
        """Traktor's settings file, from the selected install's collection."""
        from core.traktor_devices import settings_tsi

        inst = self._current_install()
        return settings_tsi(getattr(inst, "nml_path", None) if inst else None)

    def _refresh_d2_device(self) -> None:
        """Re-evaluate checks 1 and 2, which are read from disk.

        Check 3 is driven by traffic arriving, so it updates itself. Never inside Traktor:
        the D2 bridge is not used there (User, 2026-11-24).
        """
        if in_traktor():
            return
        from core.d2_status import (
            Check,
            controller_check,
            qml_check,
            update_check,
        )

        if not hasattr(self, "check_rows"):
            return
        paths = self._d2_paths()
        d2 = paths[0] if paths else None

        # This runs on every show and every switch to the Traktor tab, and
        # the controller check parses Traktor's settings file, which runs to
        # megabytes. Each answer is kept against the size and modification
        # time of what it read, so an unchanged install is not read again.
        cache = getattr(self, "_d2_check_cache", None)
        if cache is None:
            cache = self._d2_check_cache = {}

        qml_key = (
            (_stat_key(d2 / "D2.qml"), _stat_key(d2 / "Api"), _stat_key(paths[2]), paths[3])
            if paths
            else None
        )
        if cache.get("qml_key") != qml_key or "qml" not in cache:
            # A Traktor update wipes the patch, and "Not installed" is then a
            # true but useless answer — it reads like the bridge was never set
            # up, on a machine where it worked yesterday. When the backups say
            # otherwise, say what actually happened instead.
            notice = update_check(d2, paths[2], paths[3]) if paths else None
            cache["qml_key"] = qml_key
            cache["update_notice"] = notice
            cache["qml"] = (
                Check(False, notice.summary, notice.detail) if notice is not None else qml_check(d2)
            )
        self._update_notice = cache["update_notice"]
        self._set_check("qml", cache["qml"])

        tsi = self._traktor_settings_path()
        tsi_key = _stat_key(tsi) if tsi is not None else None
        if cache.get("device_key") != tsi_key or "device" not in cache:
            cache["device_key"] = tsi_key
            cache["device"] = controller_check(tsi)
        self._set_check("device", cache["device"])

    #: Remembers which Traktor version the re-install offer was made for, so
    #: someone who says no is not asked again every time Settings is opened.
    _REPATCH_ASKED_KEY = "d2_repatch_offered_for"

    def _offer_repatch(self) -> None:
        """Offer to reinstall after a Traktor update. Asked once per version.

        Deliberately not raised while the tab is being built: the check runs
        on every refresh, and a modal dialog appearing out of a widget
        constructor (or out of a five-second poll) is how a prompt becomes an
        interruption. Opening the Traktor tab is the moment the question is
        both relevant and expected. Never inside Traktor (no D2 bridge there).
        """
        if in_traktor():
            return
        from ui.dialogs import confirm

        notice = getattr(self, "_update_notice", None)
        if notice is None or not self.isVisible():
            # An invisible widget has nobody to ask. It is also what keeps a
            # modal out of a headless test run on a machine where Traktor
            # really was updated — the check reads that machine's backups.
            return
        if self._settings.value(self._REPATCH_ASKED_KEY, "", type=str) == notice.current:
            return
        # Recorded before asking, not after: declining is an answer, and a
        # dialog that reappears until you say yes is not a question.
        self._settings.setValue(self._REPATCH_ASKED_KEY, notice.current)
        if confirm(
            self,
            "Traktor was updated",
            f"{notice.detail}\n\nInstall it again now?",
            yes_text="Install…",
            no_text="Later",
        ):
            self._patch_d2()

    @staticmethod
    def _bundled_traktor_path() -> Path:
        """Path to the embedded Traktor/ folder (works frozen and in dev)."""
        from core.runtime import bundle_data_dir

        return bundle_data_dir() / "Traktor"

    def _open_folder(self, path: Path) -> None:
        """Reveal *path* in the OS file manager (Finder / Explorer / xdg).

        A failure is logged and told: otherwise the button just does nothing.
        """
        from ui.dialogs import warn

        if not path.exists():
            log.warning("open folder: %s does not exist", path)
            warn(self, "Folder not found", f"{path} does not exist.")
            return
        try:
            if sys.platform == "darwin":
                subprocess.run(
                    [
                        "osascript",
                        "-e",
                        'tell application "Finder" to open POSIX file '
                        + applescript.quoted(str(path)),
                    ],
                    check=False,
                )
            elif sys.platform == "win32":
                # os.startfile opens a directory in Explorer.
                import os

                os.startfile(str(path))
            else:
                subprocess.run(["xdg-open", str(path)], check=False)
        except OSError as exc:
            log.warning("open folder %s failed", path, exc_info=True)
            warn(self, "Could not open the folder", f"{path} could not be opened: {exc}")

    def _open_bundled_traktor(self) -> None:
        self._open_folder(self._bundled_traktor_path() / "D2")

    def _open_bundled_traktor_root(self) -> None:
        self._open_folder(self._bundled_traktor_path())

    # Target CSI directories for each known Traktor installation. macOS keeps
    # the QML inside the .app bundle; Windows keeps it under the install dir's
    # Resources64\qml\CSI. Non-existent entries are skipped by _resolve_qml_path,
    # so listing every platform's paths together is harmless.
    _CSI_CANDIDATES = _build_csi_candidates()

    def _resolve_qml_path(self) -> Path | None:
        """Return the deepest existing segment of the Traktor QML/CSI path.

        Resolves against the *selected* installation first, so on a machine
        with several Traktors this opens the folder belonging to the one
        everything else is using — not merely the first one on the candidate
        list.
        """
        inst = self._current_install()
        if inst is not None and inst.csi_dir is not None:
            d2 = inst.csi_dir / "D2"
            return d2 if d2.exists() else inst.csi_dir

        candidates = [c / "D2" for c in self._CSI_CANDIDATES]
        for full in candidates:
            for p in [full, *full.parents]:
                if p.parent == p:
                    break
                if p.exists():
                    return p
        return None

    def _open_qml_folder(self) -> None:
        target = self._resolve_qml_path()
        if target is None:
            return
        self._open_folder(target)

    # ── Public API called by MainWindow ───────────────────────────────────────

    def set_status(self, ok: bool | None, message: str) -> None:
        """ok=True → green, ok=False → red, ok=None → yellow (pending)."""
        if ok is True:
            color = T.GREEN
        elif ok is False:
            color = "#ef5350"
        else:
            color = T.ORANGE
        self.status_dot.setStyleSheet(f"color: {color}; font-size: 16px;")
        self.status_msg.setText(message)

    def set_traktor_active(self, active: bool) -> None:
        """A ping arrived, or the watchdog expired.

        When it is talking, :meth:`set_bridge_stats` paints the row from
        better data — how many messages and how long ago — and is called right
        after this, so this only owns the watchdog, the hint, and saying so
        when nothing is coming.
        """
        if active:
            self._traktor_timeout.start()  # reset the 5-second watchdog
            # Called on every message from Traktor, several a second: only
            # the watchdog needs touching once it is already marked active.
            if getattr(self, "_traktor_active", None) is True:
                return
        else:
            self._traktor_timeout.stop()
            self.traktor_dot.setStyleSheet("color: #555577; font-size: 16px;")
            self.traktor_msg.setText("Waiting for Traktor…")
        self._traktor_active = active
        self.traktor_hint.setVisible(not active)

    def set_bridge_stats(self, received: int, last_age_s: float | None) -> None:
        """Show how much the D2 bridge has actually sent, and how long ago."""
        text = bridge_stats_text(received, last_age_s)
        if self.bridge_stats.text() != text:
            self.bridge_stats.setText(text)
        if hasattr(self, "check_rows"):
            from core.d2_status import api_check

            self._set_check("api", api_check(received, last_age_s))

    def set_d2_version(self, version: str) -> None:
        """The version the *running* mapping reports over keepAlive.

        This refines check 1 rather than standing in for it: it only arrives
        once everything already works, so on its own it says nothing when the
        bridge is broken — which is when the checklist is read.
        """
        if not hasattr(self, "check_rows"):
            return
        # Painted here, not by _set_check: the next check must repaint.
        getattr(self, "_painted_checks", {}).pop("qml", None)
        dot, value = self.check_rows["qml"]
        if version == D2_EXPECTED_VERSION:
            dot.setStyleSheet(f"color: {T.GREEN}; font-size: 16px;")
            value.setText(f"Installed and running (v{version})")
            value.setStyleSheet(f"color: {T.TEXT};")
        else:
            dot.setStyleSheet(f"color: {T.ORANGE}; font-size: 16px;")
            value.setText(f"Outdated (v{version}, expected v{D2_EXPECTED_VERSION}) — reinstall")
            value.setStyleSheet(f"color: {T.ORANGE};")
