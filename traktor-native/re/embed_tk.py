"""Track Kommander running INSIDE Traktor (integrated mode, stage 1).

Run inside Traktor by libtk:  printf 'pyexec <this file>\\n' | nc 127.0.0.1 8767
The standalone TK must NOT be running (it owns the webhook port and the DB).

What this does, once (re-running only re-logs the geometry):
* boots TK the way ``ui.app.main`` does, minus what would harm Traktor's
  process: no second event loop (Qt runs as a plugin on Traktor's run loop),
  no sys.exit, no SIGINT handler, no faulthandler (it would take over
  Traktor's crash signals), no UI watchdog (it would sample Traktor's main
  thread), and TK's own window is never shown;
* makes a host view inside Traktor's window, over the track-list area, where
  SETTINGS / LIB / STATS from the band show TK's page in place of the list
  (the same button gives the list back). Pages are hidden and reused, never
  deleted: deleting a Qt window parented to Traktor's view crashed Traktor.
"""

import builtins
import ctypes
import importlib
import logging
import os
import sys
import time
import traceback
from pathlib import Path

#: TK_EMBED_LOG (tk-embed.conf) names it; a checkout keeps it in its worktrees/native-scratch/.
LOG = Path(
    os.environ.get("TK_EMBED_LOG")
    or Path(os.environ.get("TK_REPO", ".")) / "worktrees" / "native-scratch" / "embed.log"
)


def log(msg: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(f"{time.strftime('%H:%M:%S')} {msg}\n")


def list_rect() -> list[int] | None:
    """The track list's rect from libtk (BrowserForm coords) + the form's first fields."""
    lib = ctypes.CDLL(None)
    fn = lib.tk_list_rect
    fn.argtypes = [ctypes.POINTER(ctypes.c_int)]
    buf = (ctypes.c_int * 20)()
    return list(buf) if fn(buf) else None


def safe_child_processes() -> None:
    """Child processes must be Python, never another Traktor.

    Inside Traktor ``sys.executable`` is Traktor's binary, and the environment still carries
    DYLD_INSERT_LIBRARIES (libtk) and TK_EMBED: a multiprocessing "spawn" worker (hotcue
    detection's ProcessPoolExecutor) started a whole Traktor with TK inside for each worker
    (User, 2026-11-24, "it started many traktors"). Workers now run the interpreter this
    embedded one is made from, and no child inherits the injection: from a support app
    (TK_PYTHONHOME, set by libtk) its own python; from a checkout the venv's. Either way the
    "spawn" start hands each worker this process's sys.path, so the packages are found.
    """
    import multiprocessing

    os.environ.pop("DYLD_INSERT_LIBRARIES", None)
    home = os.environ.get("TK_PYTHONHOME")
    python = (
        Path(home) / "bin" / f"python{sys.version_info.major}.{sys.version_info.minor}"
        if home
        else Path(os.environ.get("TK_REPO", "")) / ".venv" / "bin" / "python"
    )
    if not python.is_file():
        raise RuntimeError(f"no Python for worker processes at {python}")
    multiprocessing.set_executable(str(python))
    log(f"worker processes: {python}")


#: Traktor's right-click menu, as measured from a screenshot of it (2026-11-24).
TRAKTOR_MENU_STYLE = """
QMenu { background: #3c3c3c; color: #f0f0f0; border: 1px solid #262626; padding: 3px 0px;
        font-size: 12px; font-weight: 600; }
QMenu::item { padding: 3px 24px 3px 8px; background: transparent; }
QMenu::item:selected { background: #5a5a5a; color: #ffffff; }
QMenu::item:disabled { color: #7c7c7c; }
QMenu::separator { height: 1px; background: #262626; margin: 3px 0px; }
QMenu::right-arrow { width: 8px; height: 8px; }
"""


#: Traktor's tooltip, as its [i] tooltips look (dark box, thin grey border, bold light text).
_HINT_STYLE = (
    "QLabel { background: #2c2c2c; color: #e6e6e6; border: 1px solid #4a4a4a;"
    " font-size: 11px; font-weight: 600; padding: 2px 6px; }"
)


def band_hint(state, button: str, text: str, ms: int = 1500) -> None:
    """*text* under band button *button* for *ms* (ui.traktor_hint): a small native view in
    Traktor's window, like TK's pages — a Traktor label there would sit under the track list,
    which paints over anything in its rect."""
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtWidgets import QLabel

    rect = (ctypes.c_int * 4)()
    fn = ctypes.CDLL(None).tk_band_button_rect
    fn.argtypes = [ctypes.c_char_p, ctypes.POINTER(ctypes.c_int)]
    r = list_rect()
    if not fn(button.encode(), rect) or not r:
        return
    hint = state.get("hint")
    if hint is None:
        hint = QLabel()
        hint.setAttribute(Qt.WidgetAttribute.WA_NativeWindow)
        hint.setStyleSheet(_HINT_STYLE)
        hint.winId()
        hint.windowHandle().setParent(state["foreign"])
        state["hint"] = hint
    hint.setText(text)
    hint.adjustSize()
    h = int(state["content"].bounds().size.height)
    form_top = h - _BORDER - r[9]
    w, hh = max(hint.width(), rect[2] - rect[0]), hint.height()
    y_top = form_top + rect[3] + 3  # just under the button
    hint.setGeometry(_BORDER + rect[0], h - y_top - hh, w, hh)  # Traktor's view: y from the bottom
    hint.show()
    hint.raise_()
    state["hint_gen"] = gen = state.get("hint_gen", 0) + 1
    QTimer.singleShot(ms, lambda: hint.hide() if state.get("hint_gen") == gen else None)


def close_popups_on_outside_click(state) -> None:
    """A TK popup (right-click menu, combo list) closes when the mouse goes down outside it.

    Inside Traktor a click outside lands in Traktor's own window, which Qt never sees, so the
    popup stayed open (User, 2026-11-24). An app-wide AppKit monitor sees every mouse-down first;
    the click still goes on to Traktor.
    """
    from AppKit import NSEvent
    from PySide6.QtGui import QCursor
    from PySide6.QtWidgets import QApplication

    mask = (1 << 1) | (1 << 3) | (1 << 25)  # left / right / other mouse down

    def seen(event):
        try:
            popup = QApplication.activePopupWidget()
            if popup is not None and not popup.frameGeometry().contains(QCursor.pos()):
                popup.close()
        except Exception:  # never let an error escape into AppKit's event dispatch
            log(f"popup closer failed:\n{traceback.format_exc()}")
        return event

    state["popup_monitor"] = NSEvent.addLocalMonitorForEventsMatchingMask_handler_(mask, seen)


def boot_tk():
    safe_child_processes()  # first: before anything can start a worker
    from PySide6.QtCore import QCoreApplication, Qt
    from PySide6.QtWidgets import QApplication

    QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_PluginApplication, True)
    app = QApplication.instance() or QApplication(["Track Kommander"])

    from core.constants import APP_NAME

    QCoreApplication.setApplicationName(APP_NAME)
    QCoreApplication.setOrganizationDomain("trackkommander.app")
    from core import migrate

    migrate.run(APP_NAME)
    from core.runtime import user_log_dir

    fmt = logging.Formatter(
        "%(asctime)s  %(levelname)-7s  %(name)s  %(message)s", datefmt="%H:%M:%S"
    )
    log_path = user_log_dir() / "log-in-traktor.log"
    if log_path.exists():  # keep the last run's log: a stall reported later is matched to it
        log_path.replace(log_path.with_name("log-in-traktor.prev.log"))
    fh = logging.FileHandler(str(log_path), mode="w", encoding="utf-8")
    fh.setFormatter(fmt)
    logging.root.handlers = [fh]
    logging.root.setLevel(logging.INFO)
    logging.getLogger("numba").setLevel(logging.WARNING)

    from core.db import init_db
    from ui.app import open_library

    if not open_library(init_db, lambda *a, **k: None):
        raise RuntimeError("TK library would not open")
    from core.runtime import bundle_data_dir
    from ui.app import load_app_fonts
    from ui.style import STYLE, apply_dark_tooltip_palette

    load_app_fonts(bundle_data_dir() / "assets" / "fonts")
    # TK's popups look like Traktor's own right-click menus there (User, 2026-11-24): dark grey,
    # bold white text, thin dark separators, a lighter grey highlight.
    app.setStyleSheet(STYLE + TRAKTOR_MENU_STYLE)
    apply_dark_tooltip_palette()
    from ui.main_window import MainWindow

    # integrated=True: none of the window-over-Traktor machinery (docking, snapping, deck screen
    # captures, starting Traktor); the window itself is never shown, its pages live in Traktor.
    win = MainWindow(integrated=True)
    from PySide6.QtCore import QTimer

    QTimer.singleShot(5000, win._start_extensions)  # an add-on's start, as standalone
    # PyAV's first import loads FFmpeg (~0.6 s on Traktor's main thread). Pay it here, at boot,
    # not at the first play or analysis: then it froze Traktor's UI (measured 2026-11-24).
    QTimer.singleShot(3000, _warm_imports)
    log("TK booted inside Traktor (main window hidden)")
    return app, win


def _warm_imports() -> None:
    t0 = time.perf_counter()
    importlib.import_module("av")  # for its load-time cost only

    log(f"warm imports: av in {(time.perf_counter() - t0) * 1000:.0f} ms")


_BORDER = 3  # Traktor's window border around its forms (list x 230 sits at window x 233)


def traktor_content():
    from AppKit import NSApplication

    wins = [w for w in NSApplication.sharedApplication().windows() if w.isVisible()]
    return max(wins, key=lambda w: w.frame().size.width * w.frame().size.height).contentView()


def make_host(state):
    """A view inside Traktor's window over the track list, holding TK's pages (a stack)."""
    import objc
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtGui import QWindow
    from PySide6.QtWidgets import QStackedWidget, QVBoxLayout, QWidget

    content = traktor_content()
    foreign = QWindow.fromWinId(objc.pyobjc_id(content))
    host = QWidget()
    host.setAttribute(Qt.WidgetAttribute.WA_NativeWindow)
    lay = QVBoxLayout(host)
    lay.setContentsMargins(0, 0, 0, 0)
    stack = QStackedWidget()
    lay.addWidget(stack)
    host.winId()
    host.windowHandle().setParent(foreign)
    host.hide()
    state.update(host=host, stack=stack, foreign=foreign, content=content, pages={}, current="")

    def place() -> None:
        r = list_rect()
        if not r or not host.isVisible():
            return
        left, top, right, bottom, form_h = r[0], r[1], r[2], r[3], r[9]
        if top < 20:  # the band is hidden: not TK's view any more, give the list back
            show_page(state, "")
            return
        h = int(content.bounds().size.height)
        form_top = h - _BORDER - form_h  # the browser form sits at the bottom of the window
        x, y_top, w, hh = _BORDER + left, form_top + top, right - left, bottom - top
        rect = (x, h - y_top - hh, w, hh)  # Traktor's view is not flipped: y from the bottom
        if state.get("placed") != rect:  # moved only when the list did
            state["placed"] = rect
            host.setGeometry(*rect)

    def placed_now() -> None:
        try:
            place()
        except Exception:  # a callback from Traktor's C++: never let an error escape into it
            log(f"place from libtk failed:\n{traceback.format_exc()}")

    # libtk calls back as soon as Traktor re-lays out its browser (a separator drag, a window
    # resize, the cards opening): the page follows at once, not 400 ms later — Traktor's list
    # showed through it meanwhile (User, 2026-11-24: "feels clunky"). The timer stays, faster,
    # for anything that moves the list without that re-layout.
    state["layout_cb"] = ctypes.CFUNCTYPE(None)(placed_now)  # kept: libtk holds the pointer
    setter = getattr(ctypes.CDLL(None), "tk_set_layout_callback", None)
    if setter is not None:
        setter.argtypes = [ctypes.c_void_p]
        setter(ctypes.cast(state["layout_cb"], ctypes.c_void_p))
    timer = QTimer(host)
    timer.setInterval(150)
    timer.timeout.connect(place)
    timer.start()
    state["place"] = place


def band_active(key: str) -> None:
    """Light that page's button in the band (blue tile), or none."""
    fn = ctypes.CDLL(None).tk_band_active_page
    fn.argtypes = [ctypes.c_char_p]
    fn(key.encode())


def show_page(state, key: str) -> None:
    """Show TK page *key* ("SETTINGS"/"LIB"/"STATS") in place of the list; "" = the list.

    The band's list / settings / library / stats buttons are one selector, like Traktor's loop
    lengths (User, 2026-11-24): one is always selected and clicking it again changes nothing.
    """
    win, host, stack, pages = state["win"], state["host"], state["stack"], state["pages"]
    if key and key == state["current"]:
        return  # already showing: a selector, not a toggle
    from ui.band_extensions import button_for

    # An add-on's page libtk draws with Traktor's own labels (BandButton.native), not a Qt page.
    drawn = state.get("native")
    if drawn and drawn != key:
        was = button_for(drawn)
        if was is not None and was.native is not None:
            was.native(win, False)
        state["native"] = None
    button = button_for(key)
    if button is not None and button.native is not None and button.native(win, True):
        host.hide()  # no Qt window over Traktor: libtk draws this page with Traktor's labels
        state["current"] = state["native"] = key
        band_active(key)  # hides Traktor's list under it
        log(f"page {key} drawn natively in Traktor")
        return
    if not key:
        if not state["current"]:
            return
        host.hide()
        state["current"] = ""
        band_active("")
        log("list back")
        return
    for popup in getattr(win, "_panel_popups", {}).values():
        popup.hide()  # popups opened before this mode: out of the way, never deleted
    from ui.band_pages import page_of

    # Each Library band button is one tab, shown with the tab bar hidden (the band is the tab bar
    # there). Settings is one button whose page keeps its own tab row; About is its own button
    # (User, 2026-11-24).
    kind, tab = page_of(key) or ({"SETTINGS": "settings", "LIB": "library"}.get(key, "stats"), "")
    if kind not in pages:
        page = {
            "settings": lambda: win.settings_tab,
            "library": lambda: win.lib_tab,
            "stats": win._stats_page,
        }[kind]()
        bar = getattr(getattr(page, "_tab_widget", None), "tabBar", None)
        if bar is not None and kind == "library":
            bar().hide()
        stack.addWidget(page)  # lives here for good: TK's own window is never shown in this mode
        pages[kind] = page
    if kind == "settings":
        if tab == "About":
            pages[kind].show_traktor_about()
        else:
            pages[kind].show_traktor_settings()
    elif tab:
        pages[kind].show_tab(tab)
    stack.setCurrentWidget(pages[kind])
    state["current"] = key
    band_active(key)
    host.show()
    host.raise_()
    state["place"]()
    log(f"page {key} shown in Traktor")


def standalone_tk_running() -> bool:
    """A standalone Track Kommander owns the webhook port and the database: TK must not also
    run inside Traktor. The launch scripts check it; a Finder / Dock launch of the slim app
    (make_slim_app.sh) only comes here."""
    import subprocess

    for args in (["pgrep", "-f", "src/TrackKommander.py"], ["pgrep", "-x", "Track Kommander"]):
        if subprocess.run(args, capture_output=True, check=False).returncode == 0:
            return True
    return False


def main() -> None:
    state = getattr(builtins, "_tk_in_traktor", None)
    if state is None:
        if standalone_tk_running():
            log("a standalone Track Kommander is running: TK is not started inside Traktor")
            return
        app, win = boot_tk()
        builtins._tk_in_traktor = state = {"app": app, "win": win}
    if "host" not in state:
        make_host(state)
        close_popups_on_outside_click(state)
        from ui import traktor_hint

        traktor_hint.set_drawer(lambda button, text: band_hint(state, button, text))
        # The band's SETTINGS / LIB / STATS: TK's page in place of the list, not a popup.
        state["win"]._toggle_panel_popup = lambda key: show_page(state, key)
        state["win"]._show_list = lambda: show_page(state, "")  # the band's list button
        log("host ready; band SETTINGS/LIB/STATS now switch the list area")
        on_traktor_quit(state)
        # The band asked for TK's state when it was drawn, before TK was up: answer now.
        win = state["win"]
        win._native_deck.resend()
        win._native_auto.resend()
        win._native_cards.resend()
        # Traktor opened on TK's view before TK was up: fill the Results playlist now.
        win._native_list.activate()
    log(f"list rect + form fields: {list_rect()}")


def on_traktor_quit(state) -> None:
    """When Traktor quits, close TK the normal way (webhook, workers, DB) instead of cutting it."""
    from Foundation import NSNotificationCenter, NSObject

    class _Quit(NSObject):
        def traktorWillTerminate_(self, _note):
            log("Traktor is quitting: closing Track Kommander")
            try:
                state["win"].close()
            except Exception:
                log("close failed:\n" + traceback.format_exc())

    observer = _Quit.alloc().init()
    NSNotificationCenter.defaultCenter().addObserver_selector_name_object_(
        observer, "traktorWillTerminate:", "NSApplicationWillTerminateNotification", None
    )
    state["quit_observer"] = observer


try:
    main()
except BaseException:  # all of it: libtk only sees "-1" (2026-11-25: a launch failed unlogged)
    log("FAILED:\n" + traceback.format_exc())
