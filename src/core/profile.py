"""The "beta" profile: a copy of Track Kommander that cannot disturb the real one.

Started with ``TK_PROFILE=beta`` (``scripts/run_beta.sh``). The beta:

* keeps its own data folder and preferences, because it has its own
  application name ("Track Kommander Beta") — the installed app's library
  database, logs and saved keys are never opened;
* serves the Traktor bridge on its own port, so it can run beside the
  installed app without the two fighting over 12345;
* never loads an add-on, which may run processes shared by every copy of
  the app on the machine (``core.extensions.load_addons``);
* never writes to Traktor's collection (``collection.nml``).

Without ``TK_PROFILE`` nothing here changes anything.
"""

from __future__ import annotations

import os

from core.constants import APP_NAME, WEBHOOK_PORT

#: Environment variable that switches the profile on.
PROFILE_ENV = "TK_PROFILE"
BETA_NAME = f"{APP_NAME} Beta"
BETA_WEBHOOK_PORT = WEBHOOK_PORT + 1


def is_beta() -> bool:
    return os.environ.get(PROFILE_ENV, "").strip().lower() == "beta"


def app_name() -> str:
    """The application name Qt files data and preferences under."""
    return BETA_NAME if is_beta() else APP_NAME


def webhook_port() -> int:
    """The port the bridge server starts on, before any saved setting."""
    return BETA_WEBHOOK_PORT if is_beta() else WEBHOOK_PORT


def collection_writes_allowed() -> bool:
    """False in the beta: Traktor's collection belongs to the real app."""
    return not is_beta()


def addons_allowed() -> bool:
    """False in the beta: an add-on may run processes shared by every copy of
    the app on the machine, so the beta never loads one (core.extensions)."""
    return not is_beta()
