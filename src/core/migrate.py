"""One-time migration from the old "Traktor Kommander" identity.

The app was renamed to **Track Kommander** for public release — "Traktor" is
Native Instruments' trademark and using it in a redistributed product's name
implies an endorsement that doesn't exist.

The rename is not cosmetic. Both the per-user data directory and the settings
store are keyed on the application name, so without a migration an existing
user silently loses:

* the SQLite library — every track's analysis, ratings and embeddings;
* ``models/`` and ``clap_model/`` — roughly 600 MB of downloaded weights;
* every preference: matching axes, result columns, AutoMix config, deck
  calibration.

The data directory is moved with a rename, which is instant and atomic on the
same volume — no 600 MB copy, and no window where the data exists twice.

Nothing here raises. A failed migration must leave the user with a working
(if empty-looking) app and an intact legacy directory they can recover from by
hand — never a refusal to start.
"""

from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)

LEGACY_APP_NAME = "Traktor Kommander"
LEGACY_ORG_DOMAIN = "traktorkommander.app"

# Written into the new settings store once preferences have been copied, so
# the copy happens exactly once even if the legacy store is left in place.
SETTINGS_MIGRATED_KEY = "_migrated_from_traktor_kommander"


def migrate_data_dir(new_dir: Path, legacy_dir: Path) -> str:
    """Move *legacy_dir* to *new_dir*. Returns what happened.

    ``"migrated"``       the legacy directory was renamed into place
    ``"no-legacy"``      nothing to migrate
    ``"already-done"``   the new directory already holds data
    ``"failed"``         the move was attempted and did not work

    An *empty* new directory is treated as "not yet migrated": the app creates
    its data directory eagerly on startup, so an empty one is an artefact of
    this run rather than evidence of existing data.
    """
    if not legacy_dir.is_dir():
        return "no-legacy"
    if legacy_dir.resolve() == new_dir.resolve():
        return "no-legacy"

    if new_dir.exists():
        if any(new_dir.iterdir()):
            log.info(
                "not migrating: %s already has data (legacy data left at %s)",
                new_dir,
                legacy_dir,
            )
            return "already-done"
        try:
            new_dir.rmdir()  # empty placeholder created this run
        except OSError:
            log.warning("could not clear empty %s", new_dir, exc_info=True)
            return "failed"

    try:
        new_dir.parent.mkdir(parents=True, exist_ok=True)
        legacy_dir.rename(new_dir)
    except OSError:
        log.warning("could not move %s → %s", legacy_dir, new_dir, exc_info=True)
        return "failed"

    log.info("migrated user data %s → %s", legacy_dir, new_dir)
    return "migrated"


def migrate_settings(new_settings, legacy_settings, keys=None) -> int:
    """Copy preferences from the legacy store. Returns the number copied.

    Existing values in the new store always win — a migration must never
    overwrite something the user has already set under the new identity. Runs
    once, guarded by :data:`SETTINGS_MIGRATED_KEY`.

    *keys* limits what is copied. Pass it on macOS: ``QSettings.allKeys()``
    there also reports the ``NSGlobalDomain`` fallback, so the legacy store
    claims ~265 keys when the app only ever wrote 185 — the rest are system
    defaults like ``AppleLanguages`` that have no business in our plist.
    :func:`own_keys` computes the real set.
    """
    if new_settings.value(SETTINGS_MIGRATED_KEY, False, type=bool):
        return 0

    candidates = legacy_settings.allKeys() if keys is None else keys
    copied = 0
    for key in candidates:
        if key == SETTINGS_MIGRATED_KEY or new_settings.contains(key):
            continue
        new_settings.setValue(key, legacy_settings.value(key))
        copied += 1

    new_settings.setValue(SETTINGS_MIGRATED_KEY, True)
    new_settings.sync()
    if copied:
        log.info("migrated %d preference(s) from %s", copied, LEGACY_APP_NAME)
    return copied


def own_keys(settings) -> list[str]:
    """Keys a QSettings store actually holds, minus the platform's fallbacks.

    On macOS a ``QSettings`` read falls through to ``NSGlobalDomain``, so
    ``allKeys()`` mixes the app's own preferences with system ones. Asking a
    deliberately non-existent store for its keys yields exactly that fallback
    set, and subtracting it leaves the real ones — no plist parsing and no
    platform branch (on Windows/Linux the fallback set is empty and this is a
    no-op).
    """
    from PySide6.QtCore import QSettings

    fallback = set(QSettings("tk-no-such-org-9f3a", "tk-no-such-app-9f3a").allKeys())
    return [k for k in settings.allKeys() if k not in fallback]


def run(app_name: str) -> None:
    """Perform every migration needed for the rename. Never raises.

    Must be called after ``QCoreApplication.setApplicationName`` and *before*
    anything touches the database, the model cache or ``QSettings``.
    """
    try:
        _run(app_name)
    except Exception:
        log.warning("migration from %s failed", LEGACY_APP_NAME, exc_info=True)


def _run(app_name: str) -> None:
    from PySide6.QtCore import (
        QCoreApplication,
        QSettings,
        QStandardPaths,
    )

    # Data directory. QStandardPaths already reflects the *new* app name, so
    # the legacy directory is its sibling under the same base.
    new_dir = Path(
        QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)
    )
    migrate_data_dir(new_dir, new_dir.parent / LEGACY_APP_NAME)

    # The macOS model cache is addressed by APP_NAME directly rather than
    # through QStandardPaths, and on macOS resolves to the same place — but
    # migrate it explicitly so a divergence in either helper can't strand
    # ~600 MB of weights.
    legacy_models = Path.home() / "Library" / "Application Support" / LEGACY_APP_NAME
    new_models = legacy_models.parent / app_name
    if legacy_models != new_dir:
        migrate_data_dir(new_models, legacy_models)

    # Preferences. The legacy store is addressed by its own org domain + app
    # name; the current QSettings() default already points at the new one.
    legacy = QSettings(LEGACY_ORG_DOMAIN, LEGACY_APP_NAME)
    keys = own_keys(legacy)
    if not keys:
        # Fall back to the organisation *name* form, in case the store was
        # written before the domain was set.
        legacy = QSettings(QCoreApplication.organizationName() or LEGACY_APP_NAME, LEGACY_APP_NAME)
        keys = own_keys(legacy)
    migrate_settings(QSettings(), legacy, keys)
