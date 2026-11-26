"""Library tab, automatic playlists: date smart playlists written to Traktor.

One smart playlist per quarter, half-year or year, in a folder of their own.
Mixed into :class:`ui.library.LibraryTab`, which supplies the collection path
and the "Traktor must be closed" guard.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtWidgets import (
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

log = logging.getLogger(__name__)


class _AutoPlaylistsMixin:
    def _build_auto_playlists_group(self) -> QGroupBox:
        from core.date_smartlists import (
            DATE_FIELDS,
            DEFAULT_CHOICE,
            DEFAULT_PREFIX,
            ROOT_FOLDER,
            WINDOW_MONTHS,
        )

        grp = QGroupBox("AUTOMATIC PLAYLISTS")
        lay = QVBoxLayout(grp)
        lay.setSpacing(8)
        desc = QLabel(
            f'Smart playlists in Traktor, in a "{ROOT_FOLDER}" folder: one '
            "per quarter, half-year or year, newest first, back to your "
            "oldest track (empty ones skipped). Traktor fills the current one "
            "as tracks arrive; run this again when a new period starts or "
            "after changing a choice. Your own playlists are not touched. "
            "Quit Traktor first: it overwrites the collection when it closes."
        )
        desc.setStyleSheet("font-size: 11px; color: #7777aa;")
        desc.setWordWrap(True)
        lay.addWidget(desc)

        tips = {
            "Imported": "One list per period, by the date a track was added to the collection.",
            "Released": "One list per period, by release date. Many release "
            "dates are only a year (stored as 1 January), so "
            "those tracks all land in a year's first period.",
        }
        sizes = {3: "Every 3 months", 6: "Every 6 months", 12: "Every year"}
        self.date_list_combos: dict[str, QComboBox] = {}
        self.date_list_prefixes: dict[str, QLineEdit] = {}
        for kind, _field, _attr in DATE_FIELDS:
            row = QHBoxLayout()
            lbl = QLabel(f"{kind}:")
            lbl.setFixedWidth(80)
            row.addWidget(lbl)
            combo = QComboBox()
            combo.addItem("Off", 0)
            for months in WINDOW_MONTHS:
                combo.addItem(sizes.get(months, f"Every {months} months"), months)
            saved = self._settings.value(
                f"date_windows/{kind}", DEFAULT_CHOICE.get(kind) or 0, type=int
            )
            idx = combo.findData(saved)
            combo.setCurrentIndex(idx if idx >= 0 else 0)
            combo.setToolTip(tips.get(kind, ""))
            combo.setFixedWidth(160)
            combo.currentIndexChanged.connect(
                lambda _i, k=kind, c=combo: self._settings.setValue(
                    f"date_windows/{k}", c.currentData() or 0
                )
            )
            row.addWidget(combo)
            row.addWidget(QLabel("Name prefix:"))
            prefix = QLineEdit(
                self._settings.value(f"date_prefix/{kind}", DEFAULT_PREFIX.get(kind, ""), type=str)
            )
            prefix.setPlaceholderText("none")
            prefix.setToolTip(
                f'Put before each list\'s name, e.g. "{DEFAULT_PREFIX.get(kind, "")}2026-07 to 09". '
                "Changing it renames the lists on the next run."
            )
            prefix.setFixedWidth(110)
            prefix.textChanged.connect(
                lambda text, k=kind: self._settings.setValue(f"date_prefix/{k}", text)
            )
            row.addWidget(prefix)
            row.addStretch()
            lay.addLayout(row)
            self.date_list_combos[kind] = combo
            self.date_list_prefixes[kind] = prefix

        row = QHBoxLayout()
        self.date_lists_btn = QPushButton("Create in Traktor")
        self.date_lists_btn.clicked.connect(self._create_date_smartlists)
        row.addWidget(self.date_lists_btn)
        self.date_lists_status = QLabel("")
        self.date_lists_status.setStyleSheet("font-size: 11px; color: #7777aa;")
        self.date_lists_status.setWordWrap(True)
        row.addWidget(self.date_lists_status, 1)
        lay.addLayout(row)
        return grp

    def _date_list_choice(self) -> dict[str, int | None]:
        return {
            kind: (combo.currentData() or None) for kind, combo in self.date_list_combos.items()
        }

    def _create_date_smartlists(self) -> None:
        from core.date_smartlists import write_to_collection
        from ui.dialogs import warn

        nml = self._nml()
        if not nml or not Path(nml).exists():
            warn(
                self,
                "No collection",
                "No collection.nml for the Traktor selected in Settings → Traktor.",
            )
            return
        if not self._confirm_traktor_closed("Writing playlists"):
            return
        try:
            result = write_to_collection(
                Path(nml),
                self._date_list_choice(),
                {kind: edit.text() for kind, edit in self.date_list_prefixes.items()},
            )
        except Exception as exc:
            log.warning("date smartlists failed", exc_info=True)
            warn(self, "Could not write the playlists", str(exc))
            return
        finally:
            self._collection_written()
        what = (
            f"{result.lists} smart playlist{'s' if result.lists != 1 else ''}"
            if result.lists
            else "no smart playlists"
        )
        self.date_lists_status.setText(
            f"Wrote {what}. Start Traktor to see them. Backup: {result.backup.name}"
        )
        self.reload_playlists()
