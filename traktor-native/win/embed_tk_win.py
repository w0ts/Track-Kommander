"""Track Kommander running INSIDE Traktor on Windows (the twin of ../re/embed_tk.py).

libtk.dll runs this file with ``pyexec`` on Traktor's main thread once the band is built
(TK_EMBED in tk-embed.conf). Keep it in step with re/embed_tk.py: the boot and the pages are
the same; only what macOS does with AppKit is done here with libtk.dll's Windows exports
(tk_main_window, tk_set_quit_callback, tk_set_mousedown_callback) and Win32.

What differs from macOS:
* Traktor's window is a Win32 HWND (tk_main_window) and its client area counts y from the TOP;
* Qt measures in device-independent pixels: Traktor's pixel rects are divided by the screen's
  device pixel ratio;
* worker processes run the base pythonw.exe as the venv (inside Traktor sys.executable is
  Traktor, and the venv's own python.exe is a launcher multiprocessing cannot use);
  nothing is inherited that would inject libtk into them (the launcher injects explicitly);
* a standalone TK is detected by its webhook port being taken.
"""

import builtins
import ctypes
import importlib
import logging
import os
import socket
import time
import traceback
from pathlib import Path

LOG = Path(os.environ.get("LOCALAPPDATA", "C:/")) / "Track Kommander" / "logs" / "embed.log"


def log(msg: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"{time.strftime('%H:%M:%S')} {msg}\n")


def lib() -> ctypes.CDLL:
    from workers.native_lib import process_lib

    found = process_lib()
    if found is None:
        raise RuntimeError("libtk.dll is not loaded in this process (TK_EMBED set?)")
    return found


def list_rect() -> list[int] | None:
    """The track list's rect from libtk (BrowserForm coords); [9] is the form's height."""
    fn = lib().tk_list_rect
    fn.argtypes = [ctypes.POINTER(ctypes.c_int)]
    buf = (ctypes.c_int * 20)()
    return list(buf) if fn(buf) else None


def safe_child_processes() -> None:
    """Child processes must be Python, never another Traktor: inside Traktor sys.executable is
    Traktor Pro 4.exe, so a multiprocessing "spawn" worker would start a whole Traktor.

    Not the venv's python.exe: on Windows that is a launcher that starts the real Python as ITS
    child, so multiprocessing hands its pipe and lock handles to the launcher and every worker
    died on "Access is denied" (measured 2026-11-25: 516 of 516 hotcue jobs failed, a console
    window flashing for each). CPython's own answer (bpo-35797), applied by hand because
    sys.executable is not the venv here: the base pythonw.exe, told by __PYVENV_LAUNCHER__ that it
    runs the venv (its packages; checked: sys.prefix is the venv, numpy and librosa import).
    pythonw: no console window per worker.
    """
    import multiprocessing

    venv = Path(os.environ.get("TK_REPO", "")) / ".venv" / "Scripts" / "pythonw.exe"
    home = os.environ.get("PYTHONHOME", "")
    base = Path(home) / "pythonw.exe"
    if not base.is_file():
        raise RuntimeError(f"no Python for worker processes: {base} (PYTHONHOME={home!r})")
    multiprocessing.set_executable(str(base))
    if venv.is_file():  # a checkout's venv; the release's kit has none (its packages are on the
        os.environ["__PYVENV_LAUNCHER__"] = str(venv)  # path the workers get from TK: "spawn")
        log(f"worker processes: {base} as {venv}")
    else:
        log(f"worker processes: {base}")


#: Traktor's right-click menu, as measured from a screenshot of it (2026-11-24, macOS).
TRAKTOR_MENU_STYLE = """
QMenu { background: #3c3c3c; color: #f0f0f0; border: 1px solid #262626; padding: 3px 0px;
        font-size: 12px; font-weight: 600; }
QMenu::item { padding: 3px 24px 3px 8px; background: transparent; }
QMenu::item:selected { background: #5a5a5a; color: #ffffff; }
QMenu::item:disabled { color: #7c7c7c; }
QMenu::separator { height: 1px; background: #262626; margin: 3px 0px; }
QMenu::right-arrow { width: 8px; height: 8px; }
"""

_HINT_STYLE = (
    "QLabel { background: #2c2c2c; color: #e6e6e6; border: 1px solid #4a4a4a;"
    " font-size: 11px; font-weight: 600; padding: 2px 6px; }"
)

_BORDER = 3  # Traktor's window border around its forms (measured on macOS; check on Windows)
# The browser form's top on Windows is 40 px lower than "client height - border - form height":
# measured live on the Windows test PC (2026-11-25, 100 % scaling) in the 2-deck and the 4-deck layout alike;
# without it a TK page covered the band. Why 40 is not known yet. TK_WIN_FORM_DY overrides it.
_FORM_DY_WINDOWS = 40


def client_height(hwnd: int) -> int:
    from ctypes import wintypes

    r = wintypes.RECT()
    ctypes.windll.user32.GetClientRect(wintypes.HWND(hwnd), ctypes.byref(r))
    return r.bottom - r.top


def form_to_client(state, rect: list[int], r: list[int]) -> tuple[int, int, int, int]:
    """A rect in BrowserForm coordinates -> Traktor's client area in Qt units (top-down).

    The browser form sits at the bottom of Traktor's window (as on macOS), so its top is the
    client height minus its height and the border. TK_WIN_FORM_DX / TK_WIN_FORM_DY move it by
    hand while the first runs measure where it really is.
    """
    h = client_height(state["hwnd"])
    form_top = h - _BORDER - r[9] + int(os.environ.get("TK_WIN_FORM_DY", str(_FORM_DY_WINDOWS)))
    dx = _BORDER + int(os.environ.get("TK_WIN_FORM_DX", "0"))
    left, top, right, bottom = rect
    dpr = state["foreign"].devicePixelRatio() or 1.0
    return (
        round((dx + left) / dpr),
        round((form_top + top) / dpr),
        round((right - left) / dpr),
        round((bottom - top) / dpr),
    )


def band_hint(state, button: str, text: str, ms: int = 1500) -> None:
    """*text* under band button *button* for *ms* (ui.traktor_hint)."""
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtWidgets import QLabel

    rect = (ctypes.c_int * 4)()
    fn = lib().tk_band_button_rect
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
    x, y, w, hh = form_to_client(state, [rect[0], rect[3] + 3, rect[2], rect[3] + 3], r)
    hint.setGeometry(x, y, max(hint.width(), w), hint.height())
    hint.show()
    hint.raise_()
    state["hint_gen"] = gen = state.get("hint_gen", 0) + 1
    QTimer.singleShot(ms, lambda: hint.hide() if state.get("hint_gen") == gen else None)


def close_popups_on_outside_click(state) -> None:
    """A TK popup closes when the mouse goes down outside it: libtk reports every mouse-down
    Traktor's UI thread takes (a click on Traktor's own window never reaches TK's Qt)."""
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QApplication

    def seen(x: int, y: int) -> None:
        try:
            popup = QApplication.activePopupWidget()
            if popup is None:
                return
            dpr = popup.devicePixelRatioF() or 1.0
            if not popup.frameGeometry().contains(QPoint(round(x / dpr), round(y / dpr))):
                popup.close()
        except Exception:  # a callback from libtk: never let an error escape into it
            log(f"popup closer failed:\n{traceback.format_exc()}")

    state["mousedown_cb"] = ctypes.CFUNCTYPE(None, ctypes.c_int, ctypes.c_int)(seen)  # kept alive
    setter = lib().tk_set_mousedown_callback
    setter.argtypes = [ctypes.c_void_p]
    setter(ctypes.cast(state["mousedown_cb"], ctypes.c_void_p))


def boot_tk():
    safe_child_processes()  # first: before anything can start a worker
    from PySide6.QtCore import QCoreApplication, Qt
    from PySide6.QtWidgets import QApplication

    QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_PluginApplication, True)
    app = QApplication.instance() or QApplication(["Track Kommander"])

    from core import migrate, profile
    from core.constants import APP_NAME

    # As ui.app does: TK_PROFILE=beta (tk-embed.conf) keeps its own data folder and port, so a
    # test inside Traktor never touches the installed TK's library on this PC.
    QCoreApplication.setApplicationName(profile.app_name())
    QCoreApplication.setOrganizationDomain("trackkommander.app")
    if not profile.is_beta():  # the beta starts clean, with its own folder
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
    app.setStyleSheet(STYLE + TRAKTOR_MENU_STYLE)
    apply_dark_tooltip_palette()
    from ui.main_window import MainWindow

    win = MainWindow(integrated=True)
    from PySide6.QtCore import QTimer

    QTimer.singleShot(5000, win._start_extensions)  # an add-on's start, as standalone
    QTimer.singleShot(3000, _warm_imports)  # FFmpeg's first load off the first play
    log("TK booted inside Traktor (main window hidden)")
    return app, win


def _warm_imports() -> None:
    t0 = time.perf_counter()
    importlib.import_module("av")  # for its load-time cost only
    log(f"warm imports: av in {(time.perf_counter() - t0) * 1000:.0f} ms")


def make_host(state):
    """A child window inside Traktor's window over the track list, holding TK's pages."""
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtGui import QWindow
    from PySide6.QtWidgets import QStackedWidget, QVBoxLayout, QWidget

    get = lib().tk_main_window
    get.restype = ctypes.c_void_p
    hwnd = get()
    if not hwnd:
        raise RuntimeError("Traktor's main window is not known to libtk yet")
    foreign = QWindow.fromWinId(hwnd)
    host = QWidget()
    host.setAttribute(Qt.WidgetAttribute.WA_NativeWindow)
    lay = QVBoxLayout(host)
    lay.setContentsMargins(0, 0, 0, 0)
    stack = QStackedWidget()
    lay.addWidget(stack)
    host.winId()
    host.windowHandle().setParent(foreign)
    host.hide()
    state.update(host=host, stack=stack, foreign=foreign, hwnd=hwnd, pages={}, current="")
    log(f"host: Traktor window {hwnd:#x}, dpr {foreign.devicePixelRatio()}")

    def place() -> None:
        r = list_rect()
        if not r or not host.isVisible():
            return
        if r[1] < 20:  # the band is hidden: not TK's view any more, give the list back
            show_page(state, "")
            return
        rect = form_to_client(state, r[0:4], r)
        if state.get("placed") != rect:  # moved only when the list did
            state["placed"] = rect
            host.setGeometry(*rect)
            log(f"page placed at {rect} (list rect {r[0:4]}, form height {r[9]})")

    def placed_now() -> None:
        try:
            place()
        except Exception:  # a callback from Traktor's C++: never let an error escape into it
            log(f"place from libtk failed:\n{traceback.format_exc()}")

    state["layout_cb"] = ctypes.CFUNCTYPE(None)(placed_now)  # kept: libtk holds the pointer
    setter = getattr(lib(), "tk_set_layout_callback", None)
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
    fn = lib().tk_band_active_page
    fn.argtypes = [ctypes.c_char_p]
    fn(key.encode())


def show_page(state, key: str) -> None:
    """Show TK page *key* in place of the list; "" = the list (same as re/embed_tk.py)."""
    win, host, stack, pages = state["win"], state["host"], state["stack"], state["pages"]
    if key and key == state["current"]:
        return  # already showing: a selector, not a toggle
    from ui.band_extensions import button_for

    drawn = state.get("native")
    if drawn and drawn != key:
        was = button_for(drawn)
        if was is not None and was.native is not None:
            was.native(win, False)
        state["native"] = None
    button = button_for(key)
    if button is not None and button.native is not None and button.native(win, True):
        host.hide()  # libtk draws this page with Traktor's labels
        state["current"] = state["native"] = key
        band_active(key)
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
        popup.hide()
    from ui.band_pages import page_of

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
        stack.addWidget(page)
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
    run inside Traktor. It is detected by that port being taken."""
    from core import profile

    port = int(os.environ.get("TK_SHOW_PORT", profile.webhook_port()))
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.3):
            return True
    except OSError:
        return False


def on_traktor_quit(state) -> None:
    """When Traktor closes, close TK the normal way (webhook, workers, DB) instead of cutting it."""

    def closing() -> None:
        log("Traktor is closing: closing Track Kommander")
        state["quitting"] = True  # the stall watch stops: closing is no stall
        # Still alive 8 s later: every Python thread's stack to quit-hang.log, every 8 s, so a hang
        # on close says where it is (2026-11-26: Traktor hung "Not Responding" after closing TK,
        # nothing logged after the webhook stopped).
        try:
            import faulthandler

            state["hang_file"] = open(
                LOG.parent / "quit-hang.log", "w", encoding="utf-8"
            )  # kept open: written later
            faulthandler.dump_traceback_later(8, repeat=True, file=state["hang_file"])
        except Exception:
            log("hang dump not armed:\n" + traceback.format_exc())
        try:
            state["win"].close()
        except Exception:
            log("close failed:\n" + traceback.format_exc())

    state["quit_cb"] = ctypes.CFUNCTYPE(None)(closing)  # kept alive
    setter = lib().tk_set_quit_callback
    setter.argtypes = [ctypes.c_void_p]
    setter(ctypes.cast(state["quit_cb"], ctypes.c_void_p))


#: A beat later than this is a stall of Traktor's UI thread (a 60 Hz frame is 17 ms).
_STALL_MS = 100
_BEAT_MS = 50


def start_stall_watch(state) -> None:
    """Log every stall of Traktor's UI thread to embed.log, with what its Python was doing.

    Over RDP nobody can judge smoothness by eye (User, 2026-11-25: "hard to judge smoothness.
    please fix the lags"): a QTimer beats every 50 ms on the UI thread; a background thread
    notices a late beat and takes the UI thread's Python stack at that moment ("not in Python":
    Traktor or libtk C++, whose long operations libtk.log times). One line per stall.
    """
    import sys
    import threading

    from PySide6.QtCore import QTimer

    main_id = threading.get_ident()  # this file runs on Traktor's UI thread
    beat = {"t": time.perf_counter()}

    def tick() -> None:
        beat["t"] = time.perf_counter()

    timer = QTimer(state["host"])
    timer.setInterval(_BEAT_MS)
    timer.timeout.connect(tick)
    timer.start()
    state["stall_timer"] = timer

    def where() -> str:
        frame = sys._current_frames().get(main_id)
        if frame is None:
            return "not in Python"
        stack = traceback.extract_stack(frame)[-4:]
        return " < ".join(f"{Path(f.filename).stem}:{f.lineno} {f.name}" for f in reversed(stack))

    def watch() -> None:
        seen_at = None  # (beat time, stack) of the stall in progress
        while not state.get("quitting"):
            time.sleep(0.025)
            late = (time.perf_counter() - beat["t"]) * 1000 - _BEAT_MS
            if late > _STALL_MS and seen_at is None:
                seen_at = (beat["t"], where())
            elif seen_at is not None and beat["t"] != seen_at[0]:
                ms = (beat["t"] - seen_at[0]) * 1000 - _BEAT_MS
                log(f"STALL {ms:.0f} ms: {seen_at[1]}")
                seen_at = None

    threading.Thread(target=watch, name="tk-stall-watch", daemon=True).start()
    log(f"stall watch on (beat {_BEAT_MS} ms, logs UI stalls over {_STALL_MS} ms)")


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
        state["win"]._toggle_panel_popup = lambda key: show_page(state, key)
        state["win"]._show_list = lambda: show_page(state, "")
        log("host ready; band SETTINGS/LIB/STATS now switch the list area")
        on_traktor_quit(state)
        start_stall_watch(state)
        win = state["win"]
        win._native_deck.resend()
        win._native_auto.resend()
        win._native_cards.resend()
        win._native_list.activate()
    log(f"list rect + form fields: {list_rect()}")


try:
    main()
except BaseException:  # all of it: libtk only sees the exit code
    log("FAILED:\n" + traceback.format_exc())
