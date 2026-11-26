"""Settings, General tab: the PROBLEMS group.

Everything currently wrong, in one place, driven by the same health
checks as the main window's banner but reporting the quiet cases too.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.permissions import accessibility_granted as _accessibility_granted
from core.permissions import screen_recording_granted as _screen_recording_granted
from ui.style import T


class _ProblemsPageMixin:
    """The PROBLEMS group. Widgets and state belong to ``SettingsTab``."""

    # ── Problems ────────────────────────────────────────────────────────────

    #: Which tab a notice's action should jump to.
    _NOTICE_TAB = {
        "settings": "Traktor",
        "accessibility": "Traktor",
        "screen_recording": "Traktor",
    }

    def _build_problems_group(self) -> QGroupBox:
        """Everything currently wrong, in one place.

        The same checks drive the main window's notice banner, but that stays
        silent while Traktor is closed (deliberately — nagging someone who
        isn't DJing is noise). Opening Settings is an explicit "tell me what's
        up", so this panel reports the quiet cases too.
        """
        grp = QGroupBox("PROBLEMS")
        lay = QVBoxLayout(grp)
        lay.setSpacing(6)
        self._problems_layout = lay

        self.problems_ok = QLabel("Checking…")
        self.problems_ok.setStyleSheet(f"color: {T.TEXT_DIM};")
        lay.addWidget(self.problems_ok)

        self._problem_rows: list[QWidget] = []
        self._last_notices: tuple = ()
        # What the rows currently show, so an unchanged poll leaves them be.
        self._rendered_notices: tuple | None = None
        # MainWindow's health poll only fires every 5 s, so without an initial
        # render the panel would sit on "Checking…" every time it is opened.
        # Permission state is known locally and needs no poll at all.
        self._last_traktor_up = True
        return grp

    def _clear_problem_rows(self) -> None:
        for row in self._problem_rows:
            self._problems_layout.removeWidget(row)
            row.deleteLater()
        self._problem_rows = []

    def _add_problem_row(self, severity: str, text: str, action: str | None = None) -> None:
        colour = {"error": "#ef5350", "warning": T.ORANGE}.get(severity, T.TEXT_DIM)
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(8)

        dot = QLabel("●")
        dot.setStyleSheet(f"color: {colour}; font-size: 14px;")
        dot.setAlignment(Qt.AlignmentFlag.AlignTop)
        h.addWidget(dot)

        label = QLabel(text)
        label.setWordWrap(True)
        label.setStyleSheet(f"color: {T.TEXT}; font-size: 11px;")
        h.addWidget(label, stretch=1)

        target = self._NOTICE_TAB.get(action or "")
        if target:
            btn = QPushButton(f"Go to {target}")
            btn.setStyleSheet("padding: 2px 8px; font-size: 11px;")
            btn.clicked.connect(lambda _=False, t=target: self.show_tab(t))
            h.addWidget(btn)

        self._problems_layout.addWidget(row)
        self._problem_rows.append(row)

    def set_notices(
        self, notices, *, traktor_up: bool = True, permissions: tuple[bool, bool] | None = None
    ) -> None:
        """Render the current health notices. Called from MainWindow's poll.

        *permissions* is ``(accessibility, screen recording)`` when the caller
        has just probed them; otherwise they are probed here. The panel is
        only rebuilt when what it would show differs from what it shows: this
        runs every 5 s and on every show, and rebuilding meant destroying and
        recreating every row each time.
        """
        notices = tuple(notices)
        if permissions is None:
            permissions = (_accessibility_granted(), _screen_recording_granted())
        rendered = (notices, traktor_up, tuple(permissions))
        self._last_notices = notices
        self._last_traktor_up = traktor_up
        if rendered == getattr(self, "_rendered_notices", None):
            return
        self._rendered_notices = rendered
        accessibility, screen_recording = permissions
        self._clear_problem_rows()
        shown = 0

        if not traktor_up:
            # The banner says nothing here by design; Settings should still
            # explain why the connection rows are all grey.
            self._add_problem_row(
                "info",
                "Traktor isn't running — connection and deck features are "
                "inactive until it starts.",
            )
            shown += 1

        for notice in notices:
            self._add_problem_row(notice.severity, notice.text, notice.action)
            shown += 1

        # Permission grants are checked even while Traktor is closed: they are
        # the thing most likely to be silently missing after an app update, and
        # they are fixable right now regardless of whether Traktor is up.
        for granted, name, action, what in (
            (
                accessibility,
                "Accessibility",
                "accessibility",
                "loading tracks onto decks will silently do nothing",
            ),
            (
                screen_recording,
                "Screen Recording",
                "screen_recording",
                "deck positions can't be auto-detected",
            ),
        ):
            if granted:
                continue
            if any(n.action == action for n in notices):
                continue  # already reported above, don't say it twice
            self._add_problem_row(
                "warning",
                f"{name} permission is missing — {what}.",
                action,
            )
            shown += 1

        if shown:
            self.problems_ok.setVisible(False)
        else:
            self.problems_ok.setText("✓  No problems detected.")
            self.problems_ok.setStyleSheet(f"color: {T.GREEN};")
            self.problems_ok.setVisible(True)
