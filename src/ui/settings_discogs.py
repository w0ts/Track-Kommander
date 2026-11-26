"""Settings, Discogs tab: the personal access token and the field mapping.

The token is kept encrypted with the app's other secrets (:mod:`core.secrets`),
never in the settings file. The mapping says which Discogs value fills which Traktor
field when the user fills the collection from Discogs (Library ▸ Tagging).
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from core.discogs_fields import (
    EXACT_DATES_KEY,
    PLACEHOLDER_KEY,
    REGION_KEY,
    TRAKTOR_FIELDS,
    mapping_key,
    read_mapping,
    sources_for,
)
from core.discogs_match import DEFAULT_REGION, REGIONS
from core.secrets import DISCOGS_TOKEN, SecretStoreUnavailable, get_secret, set_secret
from core.tag_sources import (
    DEFAULT_GENRE_SOURCES,
    GENRE_SOURCE_LABELS,
    GENRE_SOURCES_KEY,
)
from ui.cursors import WAIT_CURSOR
from ui.style import T

TOKEN_PAGE = "https://www.discogs.com/settings/developers"


class _DiscogsPageMixin:
    """The DISCOGS groups. Widgets and state belong to ``SettingsTab``."""

    def _build_discogs_groups(self) -> None:
        self._metadata_groups = [
            self._build_discogs_token_group(),
            self._build_discogs_mapping_group(),
        ]
        for grp in self._metadata_groups:
            self._tab("Traktor").addWidget(grp)

    # ── token ────────────────────────────────────────────────────────────────

    def _build_discogs_token_group(self) -> QGroupBox:
        grp = QGroupBox("DISCOGS ACCOUNT")
        lay = QVBoxLayout(grp)
        lay.setSpacing(8)
        note = QLabel(
            "Looking tracks up works without a token at 25 requests a minute "
            "(about 6 seconds a track). A personal access token raises that to "
            f'60. Create one, free, on <a href="{TOKEN_PAGE}">Discogs ▸ Settings ▸ '
            "Developers</a>. It is kept encrypted, not in the settings file."
        )
        note.setWordWrap(True)
        note.setOpenExternalLinks(True)
        note.setTextFormat(Qt.TextFormat.RichText)
        note.setStyleSheet(f"color: {T.TEXT_DIM}; font-size: 11px;")
        lay.addWidget(note)

        row = QHBoxLayout()
        self.discogs_token_edit = QLineEdit()
        self.discogs_token_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.discogs_token_edit.setPlaceholderText("Personal access token")
        self.discogs_token_edit.setText(get_secret(DISCOGS_TOKEN))
        self.discogs_token_edit.editingFinished.connect(self._save_discogs_token)
        row.addWidget(self.discogs_token_edit, stretch=1)
        test_btn = QPushButton("Test")
        test_btn.setToolTip("Ask Discogs whose token this is.")
        test_btn.clicked.connect(self._test_discogs_token)
        row.addWidget(test_btn)
        lay.addLayout(row)

        self.discogs_token_status = QLabel("")
        self.discogs_token_status.setStyleSheet(f"color: {T.TEXT_DIM}; font-size: 11px;")
        lay.addWidget(self.discogs_token_status)
        return grp

    def _save_discogs_token(self) -> bool:
        token = self.discogs_token_edit.text().strip()
        if token == get_secret(DISCOGS_TOKEN):
            return True
        try:
            set_secret(DISCOGS_TOKEN, token)
        except SecretStoreUnavailable as exc:
            self.discogs_token_status.setText(f"Not saved: {exc}")
            return False
        self.discogs_token_status.setText("Saved." if token else "Token removed.")
        return True

    def _test_discogs_token(self) -> None:
        from PySide6.QtGui import QGuiApplication

        from core.discogs_api import DiscogsClient, DiscogsError

        if not self._save_discogs_token():
            return
        token = self.discogs_token_edit.text().strip()
        if not token:
            self.discogs_token_status.setText("No token: lookups run at 25 a minute.")
            return
        QGuiApplication.setOverrideCursor(WAIT_CURSOR)
        try:
            user = DiscogsClient(token).check()
            self.discogs_token_status.setText(f"Works — signed in as {user}, 60 lookups a minute.")
        except DiscogsError as exc:
            self.discogs_token_status.setText(f"Discogs said no: {exc}")
        finally:
            QGuiApplication.restoreOverrideCursor()

    # ── mapping ──────────────────────────────────────────────────────────────

    def _build_discogs_mapping_group(self) -> QGroupBox:
        grp = QGroupBox("FILLING TRAKTOR")
        lay = QVBoxLayout(grp)
        lay.setSpacing(8)
        note = QLabel(
            "Which value goes into which Traktor field when you fill "
            "your collection (Library ▸ Tagging). Only empty fields are ever "
            "filled; nothing you typed is overwritten."
        )
        note.setWordWrap(True)
        note.setStyleSheet(f"color: {T.TEXT_DIM}; font-size: 11px;")
        lay.addWidget(note)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        mapping = read_mapping(self._settings)
        self.discogs_map_combos: dict[str, QComboBox] = {}
        for field in TRAKTOR_FIELDS:
            combo = QComboBox()
            combo.addItem("— leave alone —", "")
            for source in sources_for(field.key):
                combo.addItem(source.label, source.key)
            combo.setCurrentIndex(max(0, combo.findData(mapping[field.key])))
            combo.currentIndexChanged.connect(
                lambda _i, f=field.key, c=combo: self._settings.setValue(
                    mapping_key(f), c.currentData()
                )
            )
            self.discogs_map_combos[field.key] = combo
            form.addRow(f"{field.label}:", combo)
        lay.addLayout(form)

        self.discogs_placeholder_cb = QCheckBox(
            "Treat a January 1st release date (2024/1/1) as missing"
        )
        self.discogs_placeholder_cb.setToolTip(
            "Most taggers write a year they know as YYYY/1/1. With this on,\n"
            "such a date is replaced by the real one when Discogs has it."
        )
        self.discogs_placeholder_cb.setChecked(
            bool(self._settings.value(PLACEHOLDER_KEY, False, type=bool))
        )
        self.discogs_placeholder_cb.toggled.connect(
            lambda on: self._settings.setValue(PLACEHOLDER_KEY, bool(on))
        )
        lay.addWidget(self.discogs_placeholder_cb)

        self.discogs_exact_cb = QCheckBox("Fetch exact release dates (one more request per track)")
        self.discogs_exact_cb.setToolTip(
            "Most tracks are decided by the search alone, which gives the release\n"
            "year but not the day: the date filled is then YYYY/1/1, as Traktor\n"
            "writes a year-only tag. With this on, every release is fetched for\n"
            "its exact date and the track is checked on its tracklist — about\n"
            "twice as many requests, so lookups take twice as long."
        )
        self.discogs_exact_cb.setChecked(
            bool(self._settings.value(EXACT_DATES_KEY, False, type=bool))
        )
        self.discogs_exact_cb.toggled.connect(
            lambda on: self._settings.setValue(EXACT_DATES_KEY, bool(on))
        )
        lay.addWidget(self.discogs_exact_cb)

        genre_row = QHBoxLayout()
        genre_row.addWidget(QLabel("Genre and style from:"))
        self.tag_genre_combo = QComboBox()
        for key, label in GENRE_SOURCE_LABELS.items():
            self.tag_genre_combo.addItem(label, key)
        saved = str(self._settings.value(GENRE_SOURCES_KEY, DEFAULT_GENRE_SOURCES) or "")
        self.tag_genre_combo.setCurrentIndex(max(0, self.tag_genre_combo.findData(saved)))
        self.tag_genre_combo.currentIndexChanged.connect(
            lambda _i: self._settings.setValue(
                GENRE_SOURCES_KEY, self.tag_genre_combo.currentData()
            )
        )
        genre_row.addWidget(self.tag_genre_combo)
        genre_row.addStretch()
        lay.addLayout(genre_row)
        genre_note = QLabel(
            "Every other field takes its value from whichever source has one: Discogs "
            "first, then Spotify (its release date, and its label — read from the "
            "album's ℗ line, so only when Discogs has none). Spotify gives no genres "
            "to an app like this one today: a Spotify genre source falls through to "
            "Discogs, and Spotify only fills nothing."
        )
        genre_note.setWordWrap(True)
        genre_note.setStyleSheet(f"color: {T.TEXT_DIM}; font-size: 11px;")
        lay.addWidget(genre_note)

        region_row = QHBoxLayout()
        region_row.addWidget(QLabel("When a record has several releases, prefer:"))
        self.discogs_region_combo = QComboBox()
        for key, (label, _countries) in REGIONS.items():
            self.discogs_region_combo.addItem(label, key)
        saved = str(self._settings.value(REGION_KEY, DEFAULT_REGION) or "")
        self.discogs_region_combo.setCurrentIndex(max(0, self.discogs_region_combo.findData(saved)))
        self.discogs_region_combo.setToolTip(
            "Among releases that match equally well: digital first, then CD,\n"
            "then vinyl — and within a format, this region's release, then a\n"
            "worldwide one. It decides the label and catalog number you get."
        )
        self.discogs_region_combo.currentIndexChanged.connect(
            lambda _i: self._settings.setValue(REGION_KEY, self.discogs_region_combo.currentData())
        )
        region_row.addWidget(self.discogs_region_combo)
        region_row.addStretch()
        lay.addLayout(region_row)
        return grp
