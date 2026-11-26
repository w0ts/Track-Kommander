"""Who a keystroke belongs to, and what a bound action does.

Both are plain functions that take what they need, so they can be tested
without building the main window.
"""

from __future__ import annotations

from PySide6.QtWidgets import QApplication, QDialog, QLineEdit, QTextEdit, QWidget

from ui.shortcut_edit import capturing_shortcut


def owns_key_input(focus_widget) -> bool:
    """True when the focused widget must get the raw keystroke itself.

    The application-wide event filter below sees every KeyPress before the
    focused widget does, and it either runs a bound action or posts the key on
    to Traktor. Two cases have to escape that: a text field being typed into
    (search box, NML path, …), and a shortcut capture button, which is
    listening for exactly the keys the filter would otherwise claim.
    """
    if isinstance(focus_widget, (QLineEdit, QTextEdit)):
        return True
    # A panel with keys of its own (``OWNS_KEYS = True``, e.g. an add-on's
    # player with Space/Enter) gets them while focus is inside it; they
    # were sent on to Traktor instead (2026-10-04).
    widget = focus_widget
    while widget is not None:
        if getattr(widget, "OWNS_KEYS", False):
            return True
        widget = widget.parentWidget()
    # A modal dialog owns the keyboard outright: Enter is its default button
    # and Escape its cancel, not a deck load or a key for Traktor. Checked by
    # the focused widget's window, and by the active modal, since a message
    # box can be up while focus has not settled on one of its buttons yet.
    # Only *modal*: the Settings panel is a dialog too, and its shortcuts
    # and capture button must keep working.
    if focus_widget is not None:
        win = focus_widget.window()
        if isinstance(win, QDialog) and win.isModal():
            return True
    if QApplication.activeModalWidget() is not None:
        return True
    # The capture button also grabs the keyboard, and the grab is the thing
    # that actually routes the key — so trust it over focus, which some
    # platforms hand to buttons more reluctantly than others.
    return capturing_shortcut(focus_widget) or capturing_shortcut(QWidget.keyboardGrabber())


def build_action_handlers(now) -> dict:
    """Action id → what to call, for every action in :mod:`core.actions`.

    The single dispatch table: keyboard and S4 input both resolve to an action
    id and land here, so changing a binding needs no new wiring. Kept at module
    level, and taking the NOW PLAYING panel as an argument, so a test can check
    the table covers the registry without building a whole MainWindow.
    """
    handlers: dict = {
        "load.free_deck": now._load_selected_to_free_deck,
        "results.up": lambda: now.navigate_results(-1),
        "results.down": lambda: now.navigate_results(1),
        "results.page_up": lambda: now.navigate_results_page(-1),
        "results.page_down": lambda: now.navigate_results_page(1),
    }
    for letter in ("A", "B", "C", "D"):
        handlers[f"load.deck_{letter.lower()}"] = lambda d=letter: now.load_selected_to_deck(d)
    return handlers
