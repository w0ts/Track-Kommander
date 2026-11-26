"""The Spotify developer app's client id (Settings ▸ Traktor ▸ Spotify), in TK's own data folder.

Its secret is in the system's encrypted secrets (core.secrets.SPOTIFY_SECRET); the id is not a
secret. One small file, ``spotify.json``: TK's Spotify search (core.spotify_api) and any add-on
read it from here.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from core.atomic_write import write_text_atomic

log = logging.getLogger(__name__)

FILE_NAME = "spotify.json"


def path(folder: Path | None = None) -> Path:
    if folder is None:
        from core.runtime import user_data_dir

        folder = user_data_dir()
    return folder / FILE_NAME


def client_id(folder: Path | None = None) -> str:
    """The app's client id; "" when none is set or the file cannot be read."""
    file = path(folder)
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return ""
    except (OSError, ValueError) as exc:
        log.warning("spotify: cannot read %s (%s)", file, exc)
        return ""
    value = data.get("client_id") if isinstance(data, dict) else None
    return value.strip() if isinstance(value, str) else ""


def set_client_id(value: str, folder: Path | None = None) -> None:
    write_text_atomic(path(folder), json.dumps({"client_id": value.strip()}, indent=2))
