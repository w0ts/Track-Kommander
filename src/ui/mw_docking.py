"""Docking over Traktor: always-on-top, the floating button and hover focus.

While the window is snapped over Traktor's library the two read as one
application: the window floats on top only while Traktor can be seen, folds to
a small button, and takes focus from the pointer. This is that behaviour,
split out of ``MainWindow`` as a mixin so the window's own file is only its
layout and wiring. Every method keeps using ``self`` (``_docked``, ``_on_top``,
``_traktor_rect``, the timers ``MainWindow.__init__`` builds), so nothing was
re-plumbed by moving them.
"""

from __future__ import annotations

import logging
import sys

from PySide6.QtCore import Qt

#: How often, while docked, to check that Traktor can be seen. Short: when
#: Traktor is clicked it comes up over this window until the next check.
_TRAKTOR_VISIBLE_POLL_MS = 300

#: How often, while on top, to see which of the two the pointer is over
#: (macOS focus-follows-pointer, see ui.hover_focus). Fast, so Traktor is
#: active before a quick move-and-click lands.
_HOVER_POLL_MS = 40


def _moved(a, b, slop: float = 2.0) -> bool:
    """Whether two ``(x, y, w, h)`` rectangles differ by more than *slop*."""
    return any(abs(p - q) > slop for p, q in zip(a, b, strict=True))


class DockingMixin:
    """Raising, docking, always-on-top, the minimised button, hover focus."""

    def _force_raise(self) -> None:
        """Bring Kommander's window back above Traktor.

        Qt's ``raise_()``/``activateWindow()`` alone cannot reclaim focus
        from another application on macOS — the OS ignores activation
        requests from apps that aren't already active — so activate at the
        AppKit level first. Best-effort on Windows (the OS may hold the
        foreground lock).
        """
        if sys.platform == "win32":
            # raise_()/activateWindow() from a non-foreground process are
            # refused by the foreground lock — the OS just flashes the taskbar
            # button. A topmost on/off toggle raises above Traktor without
            # activating (and without the flash).
            try:
                from core import drag_win

                drag_win.raise_above_without_focus(int(self.winId()))
                # The raise ends by clearing topmost; on top keeps it.
                if self._on_top:
                    drag_win.set_topmost(int(self.winId()), True)
            except Exception:
                logging.getLogger(__name__).debug("SetWindowPos raise failed", exc_info=True)
            return
        if sys.platform == "darwin":
            try:
                from AppKit import NSApplication

                NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
            except Exception:
                logging.getLogger(__name__).debug("AppKit activation failed", exc_info=True)
        self.raise_()
        self.activateWindow()

    def _on_app_unhidden(self) -> None:
        """Kommander was just unhidden/restored — bring Traktor back too.

        Defer one event-loop turn: the AppKit unhide notification arrives
        mid-Cocoa dispatch, and the Windows path is called from inside
        ``changeEvent`` — both are bad moments to start reordering windows.
        """
        from PySide6.QtCore import QTimer

        QTimer.singleShot(0, self._restore_traktor_and_raise)

    def _on_app_state(self, state) -> None:
        """Back from another app while docked: bring Traktor up behind us."""
        if state != Qt.ApplicationState.ApplicationActive:
            return
        if not getattr(self, "_docked", False) or self._last_front != "other":
            return
        # Pairing activates Traktor, then this window again: marked now so
        # that second activation does not pair once more.
        self._last_front = "traktor"
        from PySide6.QtCore import QTimer

        QTimer.singleShot(0, self._restore_traktor_and_raise)
        logging.getLogger(__name__).info("back from another app: Traktor brought up behind")

    def _restore_traktor_and_raise(self) -> None:
        from PySide6.QtCore import QTimer

        from core.traktor import bring_traktor_forward

        if not bring_traktor_forward():
            return  # Traktor not running — nothing to restore
        # Traktor's activation lands asynchronously; put Kommander back on
        # top once it has settled.
        QTimer.singleShot(250, self._force_raise)

    # ── Docked mode ─────────────────────────────────────────────────────────

    def _update_docked(self) -> None:
        """Re-derive docked mode from where the window is now.

        Called when a snap is recorded or cleared and on every move or
        resize, so dragging the window off the library undocks it.
        """
        now_tab = getattr(self, "now_tab", None)
        if now_tab is None:  # a move while the window is being built
            return
        docked = now_tab.is_snapped()
        if docked == getattr(self, "_docked", False):
            return
        self._docked = docked
        now_tab.set_docked_frame(docked)
        timer = getattr(self, "_traktor_visible_timer", None)
        if timer is not None:
            if docked:
                timer.start()
            else:
                timer.stop()
        logging.getLogger(__name__).info("docked mode %s", "on" if docked else "off")
        self._update_on_top()

    def _update_on_top(self) -> None:
        """Always on top exactly while docked and Traktor can be seen.

        Hidden (Cmd+H), minimised or quit, Traktor is not there to be the
        top part of. With another app active it may be covered entirely —
        its windows still count as on screen — and a window floating over
        that app would just be in the way.
        """
        from core.drag import (
            _traktor_logical_bounds,
            front_app,
            is_traktor_visible,
        )

        docked = bool(getattr(self, "_docked", False))
        # One window-list read per tick: the bounds answer "is Traktor on
        # screen" too (is_traktor_visible is the same lookup), and this runs
        # every 300 ms while docked. Only read when docked — undocked, the
        # answer is needed at most for the log line below.
        visible: bool | None = None
        if docked:
            # Kept fresh for the hover check, which runs far more often.
            previous = getattr(self, "_traktor_rect", None)
            try:
                self._traktor_rect = _traktor_logical_bounds()
                visible = self._traktor_rect is not None
            except Exception:
                logging.getLogger(__name__).debug("Traktor window lookup failed", exc_info=True)
                self._traktor_rect = None
                # As is_traktor_visible: a broken lookup must not switch
                # features off.
                visible = True
            # Traktor's window moved or resized: its decks moved with it.
            if (
                previous is not None
                and self._traktor_rect is not None
                and _moved(previous, self._traktor_rect)
            ):
                self._schedule_recalibrate("Traktor window moved")
        front = front_app()
        if front != "us":
            self._last_front = front
        want = docked and front != "other" and bool(visible)
        # Focus follows the pointer only while the two read as one window.
        hover = getattr(self, "_hover_timer", None)
        if hover is not None and sys.platform == "darwin":
            if docked and visible:
                if not hover.isActive():
                    hover.start()
            else:
                hover.stop()
        if want == getattr(self, "_on_top", False):
            if want:
                self._keep_on_top()
            return
        self._on_top = want
        from ui.window_level import set_always_on_top

        set_always_on_top(self, want)
        if want:
            logging.getLogger(__name__).info("always on top on (docked, Traktor in front)")
        else:
            from core.drag import front_app_name

            logging.getLogger(__name__).info(
                "always on top off — front: %s (%s), docked=%s, Traktor on screen=%s",
                front,
                front_app_name(),
                docked,
                visible if visible is not None else is_traktor_visible(),
            )

    # ── Minimised to a button ───────────────────────────────────────────────

    def _collapse_to_button(self) -> None:
        """Hide the window, leaving one small button above Traktor."""
        from ui.mini_button import MiniButton

        if self._mini_button is None:
            self._mini_button = MiniButton()
            self._mini_button.restore_requested.connect(self._expand_from_button)
            from PySide6.QtCore import QTimer

            self._mini_visible_timer = QTimer(self)
            self._mini_visible_timer.setInterval(_TRAKTOR_VISIBLE_POLL_MS)
            self._mini_visible_timer.timeout.connect(self._update_mini_visibility)
        from core.traktor import is_traktor_running

        where = self.now_tab.cover_top_left()
        # Traktor closed: the button is the only way back to the window, so
        # it stays (hidden with Traktor, nothing was left at all, 2026-10-05).
        self._mini_follows_traktor = is_traktor_running()
        self._collapsed_docked = self._docked
        self._collapsed_geometry = self.geometry()
        self.hide()
        self._mini_button.show_at(where)
        self._mini_visible_timer.start()
        self._update_mini_visibility()
        logging.getLogger(__name__).info("minimised to the floating button")

    def _update_mini_visibility(self) -> None:
        """Show the minimised button only while Traktor can be seen.

        The same rule as always-on-top: Traktor on screen, and it or this app
        the active one. Otherwise the button would float over whatever else
        the user is doing. Minimised with Traktor closed, it always shows.
        """
        button = self._mini_button
        if button is None:
            return
        from core.drag import front_app_is_traktor_or_us, is_traktor_visible

        want = not self._mini_follows_traktor or (
            is_traktor_visible() and front_app_is_traktor_or_us()
        )
        if want and not button.isVisible():
            button.show()
            button.raise_()
        elif not want and button.isVisible():
            button.hide()

    def _expand_from_button(self) -> None:
        """Bring the window back: re-snapped if it was docked, else as it was.

        Re-snapping rather than restoring the old rectangle: Traktor may have
        moved or changed layout while the window was away.
        """
        if self._mini_button is not None:
            self._mini_visible_timer.stop()
            self._mini_button.hide()
        self.setGeometry(self._collapsed_geometry)
        self.show()
        # Showing the window again can leave it at the normal level while
        # _on_top still says on top; the periodic check repairs it, this
        # makes it immediate.
        if getattr(self, "_on_top", False):
            from PySide6.QtCore import QTimer

            QTimer.singleShot(0, self._keep_on_top)
        if self._collapsed_docked:
            from core.drag import snap_over_traktor_library

            if snap_over_traktor_library(self):
                self.now_tab.mark_snapped()
        # Traktor comes back with it, right behind — the pair reappears
        # together, as on unhide. Without Traktor, just come forward.
        from core.traktor import bring_traktor_forward

        if bring_traktor_forward():
            from PySide6.QtCore import QTimer

            QTimer.singleShot(250, self._force_raise)
        else:
            self._force_raise()
        logging.getLogger(__name__).info("restored from the floating button")

    def _keep_on_top(self) -> None:
        """Re-apply always-on-top if the window lost it behind our back."""
        from ui.window_level import is_always_on_top, set_always_on_top

        if self.isVisible() and is_always_on_top(self) is False:
            set_always_on_top(self, True)
            logging.getLogger(__name__).info("always on top re-applied (the window had lost it)")

    def _on_app_switch(self) -> None:
        """The active app changed: re-decide on-top and the minimised button.

        Deferred a turn: the notice arrives mid-dispatch in the OS callback.
        """
        from PySide6.QtCore import QTimer

        QTimer.singleShot(0, self._react_to_app_switch)

    def _react_to_app_switch(self) -> None:
        if getattr(self, "_docked", False):
            self._update_on_top()
        timer = getattr(self, "_mini_visible_timer", None)
        if timer is not None and timer.isActive():
            self._update_mini_visibility()

    def _hover_focus_tick(self) -> None:
        """Make active whichever of Traktor and this window is under the pointer."""
        from PySide6.QtGui import QCursor

        from core.drag import front_app, mouse_buttons_down
        from ui.hover_focus import decide

        if not self.isVisible():  # minimised to the button: nothing to hover
            return
        pos = QCursor.pos()
        rect = self._traktor_rect
        in_traktor = rect is not None and (
            rect[0] <= pos.x() < rect[0] + rect[2] and rect[1] <= pos.y() < rect[1] + rect[3]
        )
        front = front_app()
        if front != "us":
            self._last_front = front
        target = decide(
            in_us=self.frameGeometry().contains(pos),
            in_traktor=in_traktor,
            front=front,
            buttons_down=mouse_buttons_down(),
        )
        if target == "traktor":
            from core.traktor import bring_traktor_forward

            bring_traktor_forward()
        elif target == "us":
            self._force_raise()
