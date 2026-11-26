"""Tests for the "Traktor Kommander" → "Track Kommander" migration.

Both the per-user data directory and the settings store are keyed on the
application name, so the rename moves them. Getting this wrong doesn't produce
an error — it produces an app that looks brand new, with the user's whole
analysed library and ~600 MB of downloaded model weights stranded in a folder
they'll never look in.

The rules being pinned:

* an existing new directory with data in it is never clobbered;
* an *empty* new directory is the placeholder this run just created, so it
  doesn't count as "already migrated";
* preferences already set under the new name always beat migrated ones;
* nothing raises — a failed migration must still leave a startable app and an
  intact legacy directory.
"""

from __future__ import annotations

import pytest

from core.migrate import SETTINGS_MIGRATED_KEY, migrate_data_dir, migrate_settings


@pytest.fixture
def qsettings_env(tmp_path):
    """QSettings writing into tmp_path, so tests never touch real prefs."""
    pytest.importorskip("PySide6.QtCore")
    from PySide6.QtCore import QSettings

    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path))
    yield tmp_path


def _populate(d, name: str = "traktor_kommander.db") -> None:
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text("library")


# ── Data directory ──────────────────────────────────────────────────────────


def test_moves_legacy_directory(tmp_path) -> None:
    legacy = tmp_path / "Traktor Kommander"
    new = tmp_path / "Track Kommander"
    _populate(legacy)

    assert migrate_data_dir(new, legacy) == "migrated"
    assert not legacy.exists()
    assert (new / "traktor_kommander.db").read_text() == "library"


def test_nothing_to_do_without_a_legacy_directory(tmp_path) -> None:
    assert migrate_data_dir(tmp_path / "new", tmp_path / "old") == "no-legacy"


def test_empty_new_directory_is_replaced(tmp_path) -> None:
    """The app creates its data dir eagerly, so an empty one means nothing."""
    legacy = tmp_path / "Traktor Kommander"
    new = tmp_path / "Track Kommander"
    _populate(legacy)
    new.mkdir()

    assert migrate_data_dir(new, legacy) == "migrated"
    assert (new / "traktor_kommander.db").exists()


def test_populated_new_directory_is_never_clobbered(tmp_path) -> None:
    legacy = tmp_path / "Traktor Kommander"
    new = tmp_path / "Track Kommander"
    _populate(legacy)
    _populate(new, "already_here.db")

    assert migrate_data_dir(new, legacy) == "already-done"
    assert (new / "already_here.db").exists()
    assert legacy.exists(), "legacy data must be left recoverable"


def test_same_path_is_a_noop(tmp_path) -> None:
    d = tmp_path / "Kommander"
    _populate(d)
    assert migrate_data_dir(d, d) == "no-legacy"
    assert (d / "traktor_kommander.db").exists()


def test_failure_is_reported_not_raised(tmp_path, monkeypatch) -> None:
    legacy = tmp_path / "Traktor Kommander"
    new = tmp_path / "Track Kommander"
    _populate(legacy)

    def _boom(self, target):
        raise OSError("read-only volume")

    monkeypatch.setattr("pathlib.Path.rename", _boom)
    assert migrate_data_dir(new, legacy) == "failed"
    assert legacy.exists(), "a failed move must leave the original intact"


def test_legacy_file_instead_of_directory_is_ignored(tmp_path) -> None:
    legacy = tmp_path / "Traktor Kommander"
    legacy.write_text("not a directory")
    assert migrate_data_dir(tmp_path / "Track Kommander", legacy) == "no-legacy"


# ── Preferences ─────────────────────────────────────────────────────────────


class _FakeSettings:
    """Minimal QSettings stand-in — the real one writes to the user's prefs."""

    def __init__(self, initial: dict | None = None) -> None:
        self._d = dict(initial or {})
        self.synced = 0

    def allKeys(self):
        return list(self._d)

    def contains(self, key):
        return key in self._d

    def value(self, key, default=None, type=None):
        v = self._d.get(key, default)
        if type is bool and not isinstance(v, bool):
            return str(v).lower() in ("true", "1")
        return v

    def setValue(self, key, value):
        self._d[key] = value

    def sync(self):
        self.synced += 1


def test_copies_legacy_preferences() -> None:
    new = _FakeSettings()
    legacy = _FakeSettings({"bpm_restrict": True, "s4mk2_enabled": False})

    assert migrate_settings(new, legacy) == 2
    assert new.value("bpm_restrict") is True
    assert new.value("s4mk2_enabled") is False


def test_existing_values_win() -> None:
    new = _FakeSettings({"bpm_restrict": False})
    legacy = _FakeSettings({"bpm_restrict": True, "harmonic_only": True})

    assert migrate_settings(new, legacy) == 1
    assert new.value("bpm_restrict") is False, "must not overwrite a live setting"
    assert new.value("harmonic_only") is True


def test_runs_only_once() -> None:
    new = _FakeSettings()
    legacy = _FakeSettings({"bpm_restrict": True})

    assert migrate_settings(new, legacy) == 1
    legacy.setValue("added_later", 1)
    assert migrate_settings(new, legacy) == 0
    assert not new.contains("added_later")


def test_marks_itself_done_even_with_nothing_to_copy() -> None:
    new = _FakeSettings()
    assert migrate_settings(new, _FakeSettings()) == 0
    assert new.value(SETTINGS_MIGRATED_KEY, False, type=bool) is True


def test_the_marker_itself_is_not_copied() -> None:
    new = _FakeSettings()
    legacy = _FakeSettings({SETTINGS_MIGRATED_KEY: True, "keep": 1})
    assert migrate_settings(new, legacy) == 1


def test_explicit_key_list_limits_what_is_copied() -> None:
    """macOS QSettings reports NSGlobalDomain keys too — those must not come."""
    new = _FakeSettings()
    legacy = _FakeSettings(
        {
            "bpm_restrict": True,
            "AppleLanguages": ["en"],  # system fallback, not ours
            "AKLastLocale": "en_GB",  # system fallback, not ours
        }
    )

    assert migrate_settings(new, legacy, keys=["bpm_restrict"]) == 1
    assert not new.contains("AppleLanguages")
    assert not new.contains("AKLastLocale")


def test_own_keys_strips_the_platform_fallback(qsettings_env) -> None:
    from PySide6.QtCore import QSettings

    from core.migrate import own_keys

    store = QSettings("tk-migrate-test-org", "tk-migrate-test-app")
    store.setValue("mine/one", 1)
    store.setValue("mine/two", 2)
    store.sync()
    try:
        keys = own_keys(store)
        assert sorted(keys) == ["mine/one", "mine/two"], (
            "only the store's own keys, never the platform's fallbacks"
        )
    finally:
        store.clear()
        store.sync()


# ── Whole-run safety ────────────────────────────────────────────────────────


def test_run_never_raises(monkeypatch) -> None:
    """A broken migration must not stop the app from starting."""
    from core import migrate

    def _boom(_app_name):
        raise RuntimeError("everything is on fire")

    monkeypatch.setattr(migrate, "_run", _boom)
    migrate.run("Track Kommander")  # must not raise


@pytest.mark.parametrize("app_name", ["Track Kommander", ""])
def test_run_is_callable_with_any_name(monkeypatch, app_name: str) -> None:
    from core import migrate

    calls = []
    monkeypatch.setattr(migrate, "_run", calls.append)
    migrate.run(app_name)
    assert calls == [app_name]
