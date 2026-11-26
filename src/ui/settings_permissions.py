"""Settings, Traktor tab: the macOS permission rows and their polling.

Accessibility, Screen Recording and Full Disk Access are shown as rows of
the CONNECTION group. They are re-probed every few seconds while Settings
is showing, because the person grants them in System Settings, outside
this app. ``showEvent``/``hideEvent`` live here for that reason.
"""

from __future__ import annotations

import subprocess
import sys

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QPushButton,
)

from core.permissions import accessibility_granted as _accessibility_granted
from core.permissions import full_disk_access_granted as _full_disk_access_granted
from core.permissions import request_accessibility as _accessibility_request
from core.permissions import request_full_disk_access as _full_disk_access_request
from core.permissions import request_screen_recording as _screen_recording_request
from core.permissions import screen_recording_granted as _screen_recording_granted
from ui.settings_common import (
    log,
)
from ui.style import T


class _PermissionsPageMixin:
    """The permission rows. Widgets and state belong to ``SettingsTab``."""

    #: The macOS grants rendered as CONNECTION rows: (row title, what breaks
    #: without it, granted probe, request call, System Settings pane).
    _PERMISSIONS = (
        # Accessibility — needed for synthesized mouse drags onto Traktor's
        # decks and Quartz keyboard injection into the browser search field.
        (
            "Accessibility",
            "drag-and-drop loads and browser-search typing do nothing",
            _accessibility_granted,
            _accessibility_request,
            "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility",
        ),
        # Screen Recording — needed for the deck-letter screenshot
        # calibration (CGWindowListCreateImage returns a blank image
        # without this entitlement).
        (
            "Screen Recording",
            "the deck-letter calibration screenshot comes back blank",
            _screen_recording_granted,
            _screen_recording_request,
            "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture",
        ),
        # Full Disk Access — the grant that decides the D2 install, because
        # the write happens in the elevated helper. No prompt exists for it,
        # so the row's button only opens the pane; the app has to be added
        # with + and relaunched.
        (
            "Full Disk Access",
            "the D2 install is refused even with your password",
            _full_disk_access_granted,
            _full_disk_access_request,
            "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles",
        ),
    )

    def _add_permission_check(
        self,
        row_fn,
        title: str,
        what: str,
        granted_fn,
        request_fn,
        settings_url: str,
        btn_style: str,
    ):
        """Add one macOS grant as a CONNECTION row (dot + state + button).

        The button first calls ``request_fn`` (which triggers the macOS TCC
        prompt the very first time and registers the app in the relevant
        Privacy pane). If the permission is still ungranted ~600 ms later
        — typical when the user previously denied or the prompt is silent
        — we fall back to opening the matching System Settings pane.

        Returns (dot, value, btn) so the polling tick can repaint them.
        """
        dot, value, line = row_fn(title)

        btn = QPushButton("Grant…")
        btn.setStyleSheet(btn_style)
        btn.setMinimumWidth(130)

        def _on_click() -> None:
            # A failed request is not fatal: the fallback below opens the
            # System Settings pane either way. Logged so a broken prompt is
            # visible in log.log rather than just "the button did nothing".
            try:
                request_fn()
            except Exception:
                log.warning("permission request for %s failed", title, exc_info=True)
            QTimer.singleShot(
                600,
                lambda: None if granted_fn() else subprocess.Popen(["open", settings_url]),
            )

        btn.clicked.connect(_on_click)
        line.addWidget(btn)

        self._paint_permission(dot, value, btn, granted_fn(), what)
        return dot, value, btn

    @staticmethod
    def _paint_permission(dot, value, btn, granted: bool, what: str) -> None:
        """Paint one permission row in the same idiom as the D2 checks."""
        dot.setStyleSheet(f"color: {T.GREEN if granted else T.ORANGE}; font-size: 16px;")
        value.setText("Granted" if granted else f"Not granted — {what}")
        value.setStyleSheet(f"color: {T.TEXT if granted else T.TEXT_DIM};")
        btn.setText("Granted" if granted else "Grant…")
        btn.setEnabled(not granted)

    def showEvent(self, event) -> None:
        # Re-render from the last known state so the panel is correct the
        # instant Settings opens.
        self.set_notices(
            getattr(self, "_last_notices", ()), traktor_up=getattr(self, "_last_traktor_up", True)
        )
        super().showEvent(event)
        # Opening on the Traktor tab does not emit currentChanged, so the
        # re-install offer would never be made to the person most likely to
        # see it — whoever was last on that tab. Deferred by one event-loop
        # turn: a modal dialog raised from inside showEvent runs a nested loop
        # before the window it belongs to has finished appearing.
        self._refresh_d2_device()
        if self._tab_widget.tabText(self._tab_widget.currentIndex()) == "Traktor":
            QTimer.singleShot(0, self._offer_repatch)
        if sys.platform != "darwin":
            return
        self._refresh_permissions()
        self._perm_poll.start()

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self._perm_poll.stop()

    def _refresh_permissions(self) -> None:
        for dot, value, btn, granted_fn, what in self._perm_rows:
            self._paint_permission(dot, value, btn, granted_fn(), what)
