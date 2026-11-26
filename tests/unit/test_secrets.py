"""core.secrets: passwords and API keys kept encrypted in the app's own file."""

from __future__ import annotations

import json
import stat
import sys

import pytest

from core import secrets


def test_a_secret_comes_back_and_is_not_in_the_file_in_clear():
    secrets.set_secret(secrets.DISCOGS_TOKEN, "tok-123é")
    assert secrets.get_secret(secrets.DISCOGS_TOKEN) == "tok-123é"
    stored = (secrets.FOLDER / secrets.STORE_NAME).read_text(encoding="utf-8")
    assert "tok-123" not in stored
    assert secrets.get_secret(secrets.SPOTIFY_SECRET) == ""  # never set


def test_an_empty_value_forgets_the_secret():
    secrets.set_secret(secrets.SPOTIFY_SECRET, "s")
    secrets.set_secret(secrets.SPOTIFY_SECRET, "")
    assert secrets.get_secret(secrets.SPOTIFY_SECRET) == ""
    assert secrets.SPOTIFY_SECRET not in json.loads(
        (secrets.FOLDER / secrets.STORE_NAME).read_text()
    )


def test_the_same_value_seals_differently_each_time():
    key = b"k" * 32
    assert secrets.encrypt(key, "x") != secrets.encrypt(key, "x")  # a fresh nonce


def test_an_altered_value_or_another_key_reads_as_nothing():
    secrets.set_secret(secrets.DISCOGS_TOKEN, "tok")
    path = secrets.FOLDER / secrets.STORE_NAME
    store = json.loads(path.read_text())
    sealed = store[secrets.DISCOGS_TOKEN]
    store[secrets.DISCOGS_TOKEN] = ("A" if sealed[0] != "A" else "B") + sealed[1:]
    path.write_text(json.dumps(store))
    assert secrets.get_secret(secrets.DISCOGS_TOKEN) == ""
    with pytest.raises(ValueError):
        secrets.decrypt(b"other key " * 3 + b"xx", sealed)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
def test_only_the_user_can_read_the_files():
    secrets.set_secret("an-add-on-password", "pw")
    for name in (secrets.STORE_NAME, secrets.KEY_NAME):
        mode = stat.S_IMODE((secrets.FOLDER / name).stat().st_mode)
        assert mode == 0o600, (name, oct(mode))
