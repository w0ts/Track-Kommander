"""Settings, Tagging tab: the Spotify developer app's key.

In every build: the tag-update page (Library ▸ Tagging) looks tracks up on
Spotify with it (core.spotify_api.lookup_track), and add-ons may use it too.
The Client ID is kept in TK's data folder (core.spotify_key), the secret
encrypted (core.secrets).
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from core import spotify_key
from core.secrets import SPOTIFY_SECRET, SecretStoreUnavailable, get_secret, set_secret
from ui.style import T

DASHBOARD = "https://developer.spotify.com/dashboard"


class _SpotifyApiMixin:
    """The SPOTIFY group. Widgets and state belong to ``SettingsTab``."""

    def _build_spotify_api_group(self) -> QGroupBox:
        from core.spotify_api import REDIRECT_URI

        grp = QGroupBox("SPOTIFY")
        lay = QVBoxLayout(grp)
        note = QLabel(
            "Optional. With a Spotify developer app's Client ID and secret, TK looks "
            "tracks up on Spotify (album, release date, track number, ISRC, label) and "
            "can link playlist folders to your Spotify playlists. Make one at "
            f'<a href="{DASHBOARD}">developer.spotify.com ▸ Dashboard</a> ▸ Create app, '
            f"with the redirect URI <code>{REDIRECT_URI}</code> and “Web API” ticked. "
            "The secret is kept encrypted, not in the settings file."
        )
        note.setWordWrap(True)
        note.setOpenExternalLinks(True)
        note.setStyleSheet(f"color: {T.TEXT_DIM};")
        lay.addWidget(note)
        form = QFormLayout()
        self.spotify_id = QLineEdit(spotify_key.client_id())
        self.spotify_id.setPlaceholderText("Client ID")
        self.spotify_id.editingFinished.connect(self._save_spotify_id)
        self.spotify_secret = QLineEdit(get_secret(SPOTIFY_SECRET))
        self.spotify_secret.setEchoMode(QLineEdit.EchoMode.Password)
        self.spotify_secret.setPlaceholderText("Client secret")
        self.spotify_secret.editingFinished.connect(self._save_spotify_secret)
        form.addRow("Client ID", self.spotify_id)
        form.addRow("Client secret", self.spotify_secret)
        lay.addLayout(form)
        row = QHBoxLayout()
        test = QPushButton("Test")
        test.clicked.connect(self._test_spotify_key)
        self.spotify_status = QLabel("")
        row.addWidget(test)
        row.addWidget(self.spotify_status, 1)
        lay.addLayout(row)
        return grp

    def _save_spotify_id(self) -> None:
        try:
            spotify_key.set_client_id(self.spotify_id.text())
        except OSError as exc:
            self.spotify_status.setText(f"Client ID not saved: {exc}")

    def _save_spotify_secret(self) -> None:
        try:
            set_secret(SPOTIFY_SECRET, self.spotify_secret.text().strip())
        except SecretStoreUnavailable as exc:
            self.spotify_status.setText(f"Secret not saved: {exc}")

    def _test_spotify_key(self) -> None:
        from core.spotify import SpotifyError, api_token

        self._save_spotify_id()
        self._save_spotify_secret()
        client, secret = self.spotify_id.text().strip(), self.spotify_secret.text().strip()
        if not client or not secret:
            self.spotify_status.setText("Enter both the Client ID and the secret.")
            return
        try:
            api_token(client, secret)
        except SpotifyError as exc:
            self.spotify_status.setText(str(exc))
            return
        self.spotify_status.setText("Works: Spotify accepted the key.")
