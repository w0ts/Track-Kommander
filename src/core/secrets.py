"""Passwords and API keys the user gives the app, in a file of their own,
encrypted.

The Discogs token, the Spotify app's secret and login, an add-on's own
(each add-on names its entries): kept in ``secrets.json`` beside the app's data, encrypted
with a key made for this install (``secrets.key``, readable by the user
alone). Not the system keychain any more (2026-10-05, the user's call): it
asked for the login password whenever a new build, or a separate add-on
program, read an entry another program had made. Anyone who can read the user's own files can read
these too; nobody else, and the settings plist never holds them.

The cipher is the standard library's only: an HMAC-SHA256 keystream (CTR
mode, a fresh nonce per value) XORed with the text, then an HMAC tag over
nonce and ciphertext, checked before decrypting. Both TK and the add-on
read the same folder, worked out here without Qt (the add-on has none).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets as _random
import sys
from pathlib import Path

log = logging.getLogger(__name__)

DISCOGS_TOKEN = "discogs-token"
SPOTIFY_SECRET = "spotify-client-secret"

STORE_NAME = "secrets.json"
KEY_NAME = "secrets.key"
NONCE_BYTES = 16
TAG_BYTES = 32


class SecretStoreUnavailable(Exception):
    """The secrets file could not be written (or its key not made)."""


def _default_folder() -> Path:
    """TK's per-user data folder, as Qt names it for the app."""
    home = Path.home()
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / "Track Kommander"
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", home / "AppData" / "Roaming")) / "Track Kommander"
    return home / ".local" / "share" / "Track Kommander"


#: Where both files live; tests point it at a folder of their own.
FOLDER = _default_folder()


def _private_write(path: Path, data: bytes) -> None:
    """Write *data* readable by the user alone, swapped in whole."""
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    fd = os.open(part, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    os.replace(part, path)


def _existing_key() -> bytes | None:
    path = FOLDER / KEY_NAME
    return path.read_bytes() if path.is_file() else None


def _key() -> bytes:
    """This install's key, made the first time a secret is saved."""
    key = _existing_key()
    if key is None:
        key = _random.token_bytes(32)
        _private_write(FOLDER / KEY_NAME, key)
    return key


def _keystream(key: bytes, nonce: bytes, length: int) -> bytes:
    blocks = (length + 31) // 32
    stream = b"".join(
        hmac.new(key, b"stream" + nonce + n.to_bytes(8, "big"), hashlib.sha256).digest()
        for n in range(blocks)
    )
    return stream[:length]


def _tag(key: bytes, nonce: bytes, cipher: bytes) -> bytes:
    return hmac.new(key, b"tag" + nonce + cipher, hashlib.sha256).digest()


def encrypt(key: bytes, text: str) -> str:
    plain = text.encode("utf-8")
    nonce = _random.token_bytes(NONCE_BYTES)
    cipher = bytes(a ^ b for a, b in zip(plain, _keystream(key, nonce, len(plain)), strict=True))
    return base64.b64encode(nonce + cipher + _tag(key, nonce, cipher)).decode("ascii")


def decrypt(key: bytes, sealed: str) -> str:
    """The text; ValueError when it was not sealed with *key* or was altered."""
    raw = base64.b64decode(sealed.encode("ascii"), validate=True)
    if len(raw) < NONCE_BYTES + TAG_BYTES:
        raise ValueError("too short")
    nonce, cipher, tag = raw[:NONCE_BYTES], raw[NONCE_BYTES:-TAG_BYTES], raw[-TAG_BYTES:]
    if not hmac.compare_digest(tag, _tag(key, nonce, cipher)):
        raise ValueError("not this install's, or altered")
    plain = bytes(a ^ b for a, b in zip(cipher, _keystream(key, nonce, len(cipher)), strict=True))
    return plain.decode("utf-8")


def _read_store() -> dict[str, str]:
    path = FOLDER / STORE_NAME
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.warning("secrets file unreadable: %s", exc)
        return {}
    return {k: v for k, v in data.items() if isinstance(v, str)} if isinstance(data, dict) else {}


def get_secret(name: str) -> str:
    """The stored value, or "" when there is none (or it cannot be read)."""
    sealed = _read_store().get(name)
    key = _existing_key()
    if not sealed or key is None:
        return ""
    try:
        return decrypt(key, sealed)
    except ValueError as exc:
        log.warning("secret %s unreadable: %s", name, exc)
        return ""


def set_secret(name: str, value: str) -> None:
    """Store *value*, or forget it when it is empty. Raises
    :class:`SecretStoreUnavailable` when it could not be written."""
    try:
        store = _read_store()
        if value:
            store[name] = encrypt(_key(), value)
        elif name in store:
            del store[name]
        else:
            return
        _private_write(FOLDER / STORE_NAME, json.dumps(store, indent=1).encode("utf-8"))
    except OSError as exc:
        raise SecretStoreUnavailable(f"could not save it: {exc}") from exc
