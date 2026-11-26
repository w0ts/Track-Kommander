"""macOS privacy-permission checks shared across the UI and the drag pipeline.

Three TCC permissions gate Track Kommander's automation:

* **Accessibility** — required for the synthesized Quartz mouse drags that load
  tracks onto Traktor's decks (``CGEventPost``) and for keyboard injection into
  the browser search field. Without it ``CGEventPost`` *silently* no-ops, so the
  drag appears to do nothing at all.
* **Screen Recording** — required for ``CGWindowListCreateImage`` (the
  deck-letter calibration screenshot). Without it the API returns a blank image.
* **Full Disk Access** — required to copy the D2 module into Traktor's own
  bundle. Since Ventura, writing inside another application's bundle is
  refused *even to root*, so the administrator password the D2 install already
  asks for is not enough on its own. Established by measurement; see below.

macOS scopes these grants to the exact signed binary, so a rebuilt / re-signed
app bundle starts ungranted even if a previous build was authorised. Detect the
state up front and point the user at the right Privacy pane instead of letting
the feature fail mysteriously.
"""

from __future__ import annotations

import ctypes
import logging
import subprocess
import sys

from core import platform_modules

_log = logging.getLogger(__name__)

# These TCC permissions exist only on macOS. On Windows/Linux there is no
# equivalent gate — synthesized input (SendInput) and window capture
# (grabWindow) work without any user grant — so the checks report "granted"
# and the request/open helpers are no-ops. Keeping the same API surface lets
# the shared UI and drag pipeline call them unconditionally.
_IS_MAC = sys.platform == "darwin"

# System Settings deep-links for the relevant Privacy panes.
_ACCESSIBILITY_PANE = (
    "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"
)
_SCREEN_RECORDING_PANE = (
    "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture"
)
_FULL_DISK_ACCESS_PANE = "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles"


# ── Accessibility ────────────────────────────────────────────────────────────


def accessibility_granted() -> bool:
    """Return True if the app holds macOS Accessibility permission.

    Uses ``AXIsProcessTrusted`` which checks without prompting.
    """
    if not _IS_MAC:
        return True
    try:
        ax = ctypes.cdll.LoadLibrary(
            "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
        )
        ax.AXIsProcessTrusted.restype = ctypes.c_bool
        return bool(ax.AXIsProcessTrusted())
    except OSError:
        return False


def request_accessibility() -> bool:
    """Trigger the macOS Accessibility prompt and register the app in TCC.

    Uses ``AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: true})``.
    Side effects:
      * First call ever: shows the system "Open System Settings" prompt and
        adds the app to *Privacy → Accessibility* (initially unchecked).
      * Subsequent calls while still ungranted: no prompt, just returns False —
        the user must flip the toggle in System Settings.
    Returns the current granted state after the call.
    """
    if not _IS_MAC:
        return True
    try:
        cf = platform_modules.core_foundation()
        ax = platform_modules.application_services()
        opts = cf.CFDictionaryCreate(
            None,
            [ax.kAXTrustedCheckOptionPrompt],
            [cf.kCFBooleanTrue],
            1,
            cf.kCFTypeDictionaryKeyCallBacks,
            cf.kCFTypeDictionaryValueCallBacks,
        )
        return bool(ax.AXIsProcessTrustedWithOptions(opts))
    except (ImportError, AttributeError):
        return accessibility_granted()


def open_accessibility_settings() -> None:
    """Open *System Settings → Privacy & Security → Accessibility*."""
    _open_pane(_ACCESSIBILITY_PANE)


# ── Screen Recording ─────────────────────────────────────────────────────────


def screen_recording_granted() -> bool:
    """Return True if the app holds macOS Screen Recording permission.

    Uses ``CGPreflightScreenCaptureAccess`` which checks without prompting.
    """
    if not _IS_MAC:
        return True
    try:
        cg = ctypes.cdll.LoadLibrary(
            "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics"
        )
        cg.CGPreflightScreenCaptureAccess.restype = ctypes.c_bool
        return bool(cg.CGPreflightScreenCaptureAccess())
    except (OSError, AttributeError):
        return False


def request_screen_recording() -> bool:
    """Trigger the macOS Screen Recording prompt and register the app in TCC.

    ``CGRequestScreenCaptureAccess`` adds the app to *Privacy → Screen & System
    Audio Recording* and shows the system prompt the first time. Returns the
    granted state after the call (False on first call — macOS needs the user to
    flip the toggle and re-launch the app).
    """
    if not _IS_MAC:
        return True
    try:
        cg = ctypes.cdll.LoadLibrary(
            "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics"
        )
        cg.CGRequestScreenCaptureAccess.restype = ctypes.c_bool
        return bool(cg.CGRequestScreenCaptureAccess())
    except (OSError, AttributeError):
        return screen_recording_granted()


def open_screen_recording_settings() -> None:
    """Open *System Settings → Privacy & Security → Screen Recording*."""
    _open_pane(_SCREEN_RECORDING_PANE)


# ── Full Disk Access ─────────────────────────────────────────────────────────
#
# The grant the D2 install needs, established by measurement rather than by
# what the documentation names. The write happens in the helper running as
# root, not in this process; Full Disk Access is what covers it.
#
# There is no public API and no prompt. TCC's own ``TCCAccessPreflight`` can
# read the state, but nothing can raise a dialog for this grant — the user
# adds the app with the + button and relaunches — so
# :func:`request_full_disk_access` only reports where things stand and the UI
# falls through to opening the pane.
#
# Private API, so every call is defensive: a missing framework or renamed
# symbol reports "granted" and lets the install proceed to whatever macOS
# actually says. A false "not granted" would block a user whose permission is
# fine.

#: What ``TCCAccessPreflight`` returns for a grant that is held.
_TCC_GRANTED = 0

_TCC_FRAMEWORK = "/System/Library/PrivateFrameworks/TCC.framework/TCC"
_TCC_ALL_FILES = "kTCCServiceSystemPolicyAllFiles"

#: The loaded framework, kept for the life of the process.
_tcc_keepalive: dict = {}


def full_disk_access_granted() -> bool:
    """Return True if the app holds Full Disk Access.

    "Couldn't tell" counts as granted: this is a private-API probe, and it
    must never be the reason a working install is refused.
    """
    if not _IS_MAC:
        return True
    state = _tcc_preflight(_TCC_ALL_FILES)
    return True if state is None else state == _TCC_GRANTED


def request_full_disk_access() -> bool:
    """Report the state. macOS has no prompt to raise for this grant.

    Kept so the permission rows can call every grant the same way; the row
    then falls through to opening System Settings, which is the only route
    there is.
    """
    return full_disk_access_granted()


def open_full_disk_access_settings() -> None:
    """Open *System Settings → Privacy & Security → Full Disk Access*."""
    _open_pane(_FULL_DISK_ACCESS_PANE)


# ── internals ────────────────────────────────────────────────────────────────


def _open_pane(url: str) -> None:
    try:
        subprocess.Popen(["open", url])
    except OSError:
        _log.exception("permissions: failed to open System Settings pane %s", url)


def _tcc_lib():
    """The private TCC framework, loaded once. None if it cannot be used."""
    if "lib" not in _tcc_keepalive:
        try:
            _tcc_keepalive["lib"] = ctypes.cdll.LoadLibrary(_TCC_FRAMEWORK)
        except OSError:
            _log.warning(
                "permissions: TCC.framework unavailable — treating Full Disk Access as granted"
            )
            _tcc_keepalive["lib"] = None
    return _tcc_keepalive["lib"]


def _tcc_service(name: str):
    """The ``CFStringRef`` for one TCC service, or None if it is not there."""
    lib = _tcc_lib()
    if lib is None:
        return None
    try:
        return ctypes.c_void_p.in_dll(lib, name)
    except ValueError:
        _log.warning("permissions: TCC service %s not exported on this macOS", name)
        return None


def _tcc_preflight(name: str) -> int | None:
    """``TCCAccessPreflight``: 0 granted, 1 denied, 2 not yet asked.

    None when the check could not be made at all — the caller decides what an
    unknown means, and for us it means "don't get in the way".
    """
    lib, service = _tcc_lib(), _tcc_service(name)
    if lib is None or service is None:
        return None
    try:
        lib.TCCAccessPreflight.restype = ctypes.c_int
        lib.TCCAccessPreflight.argtypes = (ctypes.c_void_p, ctypes.c_void_p)
        return int(lib.TCCAccessPreflight(service, None))
    except (AttributeError, OSError):
        _log.exception("permissions: TCCAccessPreflight(%s) failed", name)
        return None
