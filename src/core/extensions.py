"""What an add-on plugs into Track Kommander.

TK names no add-on and no add-on feature: an add-on calls the ``add_*`` functions below when it is
loaded (its ``register()``), and TK asks the getters where it shows or uses such things. Without
an add-on every list is empty and TK works on its own.

Core only (no Qt here): the UI's hooks keep their widgets behind plain callables.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass

log = logging.getLogger(__name__)

#: The one module name TK imports to find an add-on; its ``register()`` adds what it brings.
ADDON_MODULE = "tk_addon"
_loaded = False
_registered = False

#: Each returns artist names known to stand alone (``core.artists.known_artists`` adds them to
#: the collection's), e.g. the artists of an add-on's playlists.
_artist_sources: list[Callable[[], Iterable[str]]] = []


def add_artist_source(source: Callable[[], Iterable[str]]) -> None:
    _artist_sources.append(source)


def artist_sources() -> tuple[Callable[[], Iterable[str]], ...]:
    return tuple(_artist_sources)


#: Each returns Spotify playlists (objects with ``tracks``, as core.spotify_store.sync reads
#: them) whose tracks' Spotify metadata TK keeps for tagging.
_spotify_playlist_sources: list[Callable[[], Iterable]] = []


def add_spotify_playlist_source(source: Callable[[], Iterable]) -> None:
    _spotify_playlist_sources.append(source)


def spotify_playlist_sources() -> tuple[Callable[[], Iterable], ...]:
    return tuple(_spotify_playlist_sources)


@dataclass(frozen=True)
class LibraryTab:
    """A tab of the Library window, after TK's own. *build* gets the Library page (ui.library) and
    returns the tab's widget; *flush_top*: no margin above it (a widget with its own tabs)."""

    title: str
    build: Callable[[object], object]
    flush_top: bool = False


_library_tabs: list[LibraryTab] = []


def add_library_tab(tab: LibraryTab) -> None:
    _library_tabs.append(tab)


def library_tabs() -> tuple[LibraryTab, ...]:
    return tuple(_library_tabs)


@dataclass(frozen=True)
class SettingsPage:
    """A tab of Settings, before About. *build* gets the Settings page (ui.settings) and returns
    the tab's widget; *in_traktor*: also among the tabs of the band's Settings button."""

    title: str
    build: Callable[[object], object]
    in_traktor: bool = True


_settings_pages: list[SettingsPage] = []


def add_settings_page(page: SettingsPage) -> None:
    _settings_pages.append(page)


def settings_pages() -> tuple[SettingsPage, ...]:
    return tuple(_settings_pages)


@dataclass(frozen=True)
class BandButton:
    """A button in Traktor's band for one of an add-on's Library tabs (TK inside Traktor).

    *tab*: the Library tab it opens. *tooltip*: "Title\nWhat it is". *icon*: draws the button's
    shape, white on transparent, on a QPainter (ui.native_icon_shapes.SIDE square). *busy*: whether
    its job runs (the icon turns blue); slow is fine, it is asked off the main thread. *native*:
    shows (True) or hides (False) a page drawn by libtk itself instead of the tab, and says
    whether it could (None: the tab, always)."""

    tab: str
    tooltip: str
    icon: Callable[[object], None]
    busy: Callable[[], bool] | None = None
    native: Callable[[object, bool], bool] | None = None


_band_buttons: list[BandButton] = []
#: Each gets (main window, band action) and returns True when it handled the action.
_action_handlers: list[Callable[[object, str], bool]] = []
#: Each gets the main window once TK is attached inside Traktor.
_traktor_hooks: list[Callable[[object], None]] = []


def add_band_button(button: BandButton) -> None:
    _band_buttons.append(button)


def band_buttons() -> tuple[BandButton, ...]:
    return tuple(_band_buttons)


def add_action_handler(handler: Callable[[object, str], bool]) -> None:
    _action_handlers.append(handler)


def action_handlers() -> tuple[Callable[[object, str], bool], ...]:
    return tuple(_action_handlers)


def add_traktor_hook(hook: Callable[[object], None]) -> None:
    _traktor_hooks.append(hook)


def traktor_hooks() -> tuple[Callable[[object], None], ...]:
    return tuple(_traktor_hooks)


#: Each gets the main window: started once it is up (5 s after launch), stopped as TK quits.
_start_hooks: list[Callable[[object], None]] = []
_quit_hooks: list[Callable[[object], None]] = []
#: Each gets TK's version and returns a banner notice (core.health.Notice) or None; a notice's
#: button action goes to the action handlers (add_action_handler) first.
_notice_sources: list[Callable[[str], object | None]] = []


def add_start_hook(hook: Callable[[object], None]) -> None:
    _start_hooks.append(hook)


def start_hooks() -> tuple[Callable[[object], None], ...]:
    return tuple(_start_hooks)


def add_quit_hook(hook: Callable[[object], None]) -> None:
    _quit_hooks.append(hook)


def quit_hooks() -> tuple[Callable[[object], None], ...]:
    return tuple(_quit_hooks)


def add_notice_source(source: Callable[[str], object | None]) -> None:
    _notice_sources.append(source)


def notice_sources() -> tuple[Callable[[str], object | None], ...]:
    return tuple(_notice_sources)


#: Each gets (a track's QMenu, the track's path) and may add its entries (the results' and the
#: decks' right-click menus).
_track_menu_entries: list[Callable[[object, str], None]] = []


def add_track_menu_entry(entry: Callable[[object, str], None]) -> None:
    _track_menu_entries.append(entry)


def add_track_menu_entries(menu: object, path: str) -> None:
    """Let each add-on add its entries for *path* to *menu*; one that fails is left out."""
    for entry in tuple(_track_menu_entries):
        try:
            entry(menu, path)
        except Exception:
            log.exception("an add-on's menu entry failed")


#: Each builds one row (a widget) for the bottom of Settings' last tab (Tagging inside Traktor):
#: an add-on's own switches that belong with TK's settings, e.g. whether it shows at all.
_settings_rows: list[Callable[[object], object]] = []


def add_settings_row(build: Callable[[object], object]) -> None:
    _settings_rows.append(build)


def settings_rows() -> tuple[Callable[[object], object], ...]:
    return tuple(_settings_rows)


#: Whether the add-on shows: its band buttons, its Library and Settings tabs. An add-on may hide
#: itself (User, 2026-11-26: "to show people the public version" without another install); TK
#: then looks as it does without one. Listeners (the UI parts) follow a change at once.
_shown = True
_shown_listeners: list[Callable[[bool], None]] = []


def addon_shown() -> bool:
    return _shown


def set_addon_shown(shown: bool) -> None:
    global _shown
    if shown == _shown:
        return
    _shown = shown
    log.info("add-on: %s", "shown" if shown else "hidden")
    for listener in tuple(_shown_listeners):
        try:
            listener(shown)
        except Exception:
            log.exception("showing or hiding the add-on failed in one place")


def on_addon_shown(listener: Callable[[bool], None]) -> None:
    _shown_listeners.append(listener)


def clear() -> None:
    """Forget every registration, and that the add-on was loaded (tests)."""
    global _loaded, _registered, _mismatched, _shown
    _loaded = _registered = False
    _mismatched = None
    _shown = True
    _settings_rows.clear()
    _shown_listeners.clear()
    _artist_sources.clear()
    _spotify_playlist_sources.clear()
    _library_tabs.clear()
    _settings_pages.clear()
    _band_buttons.clear()
    _action_handlers.clear()
    _traktor_hooks.clear()
    _start_hooks.clear()
    _quit_hooks.clear()
    _notice_sources.clear()
    _track_menu_entries.clear()


#: The add-on (TK_ADDON_DIR) TK refused for its version (core.addon_finder), for the banner.
_mismatched = None


def _put_installed_on_path() -> bool:
    """The installed add-on's folder first on the import path; False when its version is not TK's
    (it is then remembered for mismatch_notices and not loaded)."""
    global _mismatched
    import sys

    from core import addon_finder
    from core.log_bundle import app_version

    version = app_version()
    found = addon_finder.find()
    if found is None:
        return True  # none installed: an importable one (a source checkout) may still be there
    if not addon_finder.matches(found, version):
        log.warning(
            "add-on: %s %s is not for Track Kommander %s", found.name, found.version, version
        )
        _mismatched = found
        return False
    if str(found.folder) not in sys.path:
        sys.path.insert(0, str(found.folder))
    log.info("add-on: %s %s from %s", found.name, found.version, found.folder)
    return True


def mismatch_notices(version: str) -> tuple:
    """The banner notice for an installed add-on of another version (not loaded)."""
    from core import addon_finder

    return () if _mismatched is None else (addon_finder.mismatch_notice(_mismatched, version),)


def mismatched_page() -> str:
    return "" if _mismatched is None else _mismatched.page


def load_addons() -> bool:
    """Import the add-on, if there is one, and let it register (once per process). An add-on that
    fails is logged and left out: TK always starts. True when an add-on registered."""
    global _loaded, _registered
    if _loaded:
        return _registered
    _loaded = True
    from core import profile

    if not profile.addons_allowed():
        log.info("add-on: not loaded in the beta profile")
        return False
    if not _put_installed_on_path():
        return False
    import importlib

    try:
        module = importlib.import_module(ADDON_MODULE)
    except ModuleNotFoundError as exc:
        if exc.name != ADDON_MODULE:
            log.exception("add-on: a module it needs is missing")
        return False
    except Exception:
        log.exception("add-on: failed to load")
        return False
    try:
        module.register()
    except Exception:
        log.exception("add-on: failed to register")
        return False
    log.info("add-on: registered")
    _registered = True
    return True
