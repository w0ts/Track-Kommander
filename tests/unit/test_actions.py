"""Tests for the action/shortcut registry.

Input used to be scattered: QShortcuts in NowPlayingTab, an application event
filter in MainWindow, and hardwired CUE-bit decoding in the S4 watcher. Nothing
could see the whole mapping, which is how `Shift+Left` came to be bound to both
"load into deck A" and "beat jump back 16" — Qt resolves the QShortcut before
delivering the KeyPress, so the beat jump was unreachable.

The registry's job is to make that impossible to hide, so the tests focus on:
defaults not colliding, conflicts being reported, and physical (position-based)
bindings continuing to win over layout-dependent ones.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6.QtWidgets")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, QSettings, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication

from core import actions
from core.actions import MOD_ALT, MOD_CTRL, MOD_SHIFT, Binding


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def store(qapp, tmp_path):
    """QSettings in a temp dir — never the user's real bindings."""
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path))
    QCoreApplication.setApplicationName("tk-actions-test")
    QCoreApplication.setOrganizationDomain("actions.invalid")
    s = QSettings()
    s.clear()
    s.sync()
    yield s
    s.clear()
    s.sync()


def _event(key: int, mods=Qt.KeyboardModifier.NoModifier, native: int = 0) -> QKeyEvent:
    """A key event carrying *native* as the position code.

    QKeyEvent takes nativeScanCode then nativeVirtualKey; macOS reads the
    virtual key and Windows the scan code, so set both to the same value and
    the test works on either.
    """
    return QKeyEvent(QEvent.Type.KeyPress, key, mods, native, native, 0)


# ── Registry shape ──────────────────────────────────────────────────────────


def test_action_ids_are_unique() -> None:
    ids = [a.id for a in actions.ACTIONS]
    assert len(ids) == len(set(ids))


def test_every_action_is_in_a_known_group() -> None:
    for a in actions.ACTIONS:
        assert a.group in actions.GROUPS, f"{a.id} has stray group {a.group!r}"


def test_the_expected_actions_exist() -> None:
    ids = {a.id for a in actions.ACTIONS}
    assert ids == {
        "results.up",
        "results.down",
        "results.page_up",
        "results.page_down",
        "load.free_deck",
        "load.deck_a",
        "load.deck_b",
        "load.deck_c",
        "load.deck_d",
    }


# ── The bug this registry exists to prevent ─────────────────────────────────


def test_default_keyboard_bindings_do_not_collide(store) -> None:
    assert actions.conflicts() == {}, "two actions on one key means one of them never fires"


def test_default_s4_bindings_do_not_collide(store) -> None:
    assert actions.conflicts(s4=True) == {}


def test_beatjump_no_longer_shadows_load_to_deck(store) -> None:
    """Shift+←/→ used to be claimed by both "load into deck A" and "beat jump
    back 16"; the load won and the jump was dead. Beat jump has left the
    registry entirely, so the collision cannot recur."""
    assert actions.binding_for("load.deck_a") is not None
    assert "beatjump.back_16" not in actions.BY_ID


# ── Only browsing and loading are claimed; the rest goes to Traktor ─────────


def test_only_browse_and_load_are_bound_by_default(store) -> None:
    """Kommander holds focus over Traktor, so every key it claims is one
    Traktor never sees. It should claim as few as possible."""
    bound = {a.id for a in actions.ACTIONS if actions.binding_for(a.id)}
    assert bound == {
        "results.up",
        "results.down",
        "results.page_up",
        "results.page_down",
        "load.deck_a",
        "load.deck_b",
        "load.deck_c",
        "load.deck_d",
        "load.free_deck",
    }


def test_grid_capture_is_not_a_shortcut(store) -> None:
    """Removed from Settings → Controls: capturing a grid offset is done from
    the deck button's right-click menu."""
    assert not any(a.id.startswith("grid.") for a in actions.ACTIONS)


def test_transport_is_not_in_the_registry_at_all(store) -> None:
    """Play/pause, hotcues and beat jumps are Traktor's job — Kommander
    forwards the keystroke instead of shipping its own copy."""
    ids = {a.id for a in actions.ACTIONS}
    assert not any(i.startswith(("deck.", "hotcue.", "beatjump.")) for i in ids)


@pytest.mark.parametrize(
    "key",
    [
        Qt.Key.Key_W,
        Qt.Key.Key_S,
        Qt.Key.Key_1,
        Qt.Key.Key_8,
        Qt.Key.Key_Space,
    ],
)
def test_transport_keys_are_not_swallowed(store, key) -> None:
    """These must fall through to the forwarder rather than match an action."""
    assert actions.match(_event(key)) is None


def test_conflicts_are_reported_when_they_exist() -> None:
    same = Binding("key", 0x41, MOD_SHIFT, "Shift + A")
    table = {a.id: None for a in actions.ACTIONS}
    table["load.deck_a"] = same
    table["results.up"] = same

    found = actions.conflicts(table)
    assert list(found) == ["Shift + A"]
    assert set(found["Shift + A"]) == {"load.deck_a", "results.up"}


# ── Matching ────────────────────────────────────────────────────────────────


@pytest.fixture
def physical_codes(monkeypatch) -> None:
    """A platform that reports position codes, whatever the host is.

    Linux reports none (``native_code`` is 0 there), so these tests would
    say nothing about the matcher on the hosted runner. The platform layer
    has its own tests in core.physical_keys; this one is about matching.
    """
    monkeypatch.setattr(actions, "native_code", lambda ev: int(ev.nativeVirtualKey()))


def test_matches_a_physical_binding_by_native_code(physical_codes) -> None:
    table = {a.id: None for a in actions.ACTIONS}
    table["load.deck_a"] = Binding("phys", 13, 0, "W")
    # Qt key is Z (an AZERTY keyboard) but the physical position is W's.
    ev = _event(Qt.Key.Key_Z, native=13)
    assert actions.match(ev, table) == "load.deck_a"


def test_physical_binding_ignores_the_produced_character(physical_codes) -> None:
    table = {a.id: None for a in actions.ACTIONS}
    table["load.deck_a"] = Binding("phys", 13, 0, "W")
    # Same character, different physical key → must NOT match.
    assert actions.match(_event(Qt.Key.Key_W, native=99), table) is None


def test_matches_a_logical_binding() -> None:
    table = {a.id: None for a in actions.ACTIONS}
    table["load.free_deck"] = Binding("key", int(Qt.Key.Key_Return), 0, "Return")
    assert actions.match(_event(Qt.Key.Key_Return), table) == "load.free_deck"


def test_modifiers_must_match_exactly() -> None:
    table = {a.id: None for a in actions.ACTIONS}
    table["load.deck_a"] = Binding("key", int(Qt.Key.Key_Left), MOD_SHIFT, "Shift + ←")
    assert actions.match(_event(Qt.Key.Key_Left), table) is None
    assert (
        actions.match(_event(Qt.Key.Key_Left, Qt.KeyboardModifier.ShiftModifier), table)
        == "load.deck_a"
    )


def test_alt_and_ctrl_are_distinguished() -> None:
    table = {a.id: None for a in actions.ACTIONS}
    table["load.deck_c"] = Binding("key", int(Qt.Key.Key_Left), MOD_ALT, "Alt + ←")
    table["load.deck_d"] = Binding("key", int(Qt.Key.Key_Left), MOD_CTRL, "Ctrl + ←")
    alt = _event(Qt.Key.Key_Left, Qt.KeyboardModifier.AltModifier)
    ctrl = _event(Qt.Key.Key_Left, Qt.KeyboardModifier.ControlModifier)
    assert actions.match(alt, table) == "load.deck_c"
    assert actions.match(ctrl, table) == "load.deck_d"


def test_unbound_key_matches_nothing() -> None:
    table = {a.id: None for a in actions.ACTIONS}
    assert actions.match(_event(Qt.Key.Key_F12), table) is None


def test_s4_matching() -> None:
    table = {a.id: None for a in actions.ACTIONS}
    table["load.deck_b"] = Binding("s4", "cue2", 0, "CH2 CUE")
    assert actions.match_s4("cue2", table) == "load.deck_b"
    assert actions.match_s4("cue1", table) is None


# ── Persistence ─────────────────────────────────────────────────────────────


def test_binding_survives_a_round_trip() -> None:
    b = Binding("phys", 13, MOD_SHIFT, "Shift + W")
    assert Binding.from_json(b.to_json()) == b


def test_malformed_stored_binding_is_ignored_not_fatal() -> None:
    assert Binding.from_json("not json") is None
    assert Binding.from_json('{"nope": 1}') is None


def test_set_and_read_back(store) -> None:
    b = Binding("key", int(Qt.Key.Key_F5), 0, "F5")
    actions.set_binding("load.free_deck", b)
    assert actions.binding_for("load.free_deck") == b


def test_stored_binding_overrides_the_default(store) -> None:
    default = actions.binding_for("results.down")
    actions.set_binding("results.down", Binding("key", 0x5A, 0, "Z"))
    assert actions.binding_for("results.down") != default


def test_clearing_a_binding_does_not_fall_back_to_the_default(store) -> None:
    """Unbinding must actually unbind — silently restoring the default would
    make the key keep working after the user removed it."""
    assert actions.binding_for("load.free_deck") is not None
    actions.set_binding("load.free_deck", None)
    assert actions.binding_for("load.free_deck") is None


def test_reset_restores_one_default(store) -> None:
    default = actions.binding_for("results.up")
    actions.set_binding("results.up", Binding("key", 0x5A, 0, "Z"))
    actions.reset_binding("results.up")
    assert actions.binding_for("results.up") == default


def test_reset_all_restores_everything(store) -> None:
    actions.set_binding("results.up", Binding("key", 0x5A, 0, "Z"))
    actions.set_binding("load.deck_a", None)
    actions.reset_all()
    assert actions.binding_for("results.up") == actions.BY_ID["results.up"].default
    assert actions.binding_for("load.deck_a") == actions.BY_ID["load.deck_a"].default


# ── The binding table is read once, not per keystroke ───────────────────────


def _count_reads(monkeypatch) -> list[str]:
    reads: list[str] = []
    real = actions.binding_for
    monkeypatch.setattr(
        actions, "binding_for", lambda aid, **kw: reads.append(aid) or real(aid, **kw)
    )
    return reads


def test_matching_keys_does_not_reread_the_settings(store, monkeypatch) -> None:
    actions.all_bindings()  # warm
    reads = _count_reads(monkeypatch)
    for _ in range(3):
        actions.match(_event(Qt.Key.Key_Down))
    assert reads == []


def test_every_write_is_seen_by_the_next_match(store) -> None:
    f5 = Binding("key", int(Qt.Key.Key_F5), 0, "F5")
    assert actions.match(_event(Qt.Key.Key_F5)) is None
    actions.set_binding("load.free_deck", f5)
    assert actions.match(_event(Qt.Key.Key_F5)) == "load.free_deck"
    actions.reset_binding("load.free_deck")
    assert actions.match(_event(Qt.Key.Key_F5)) is None
    actions.set_binding("load.free_deck", f5)
    actions.reset_all()
    assert actions.match(_event(Qt.Key.Key_F5)) is None


def test_a_caller_editing_its_table_does_not_change_the_cache(store) -> None:
    table = actions.all_bindings()
    table["load.free_deck"] = None
    assert actions.all_bindings()["load.free_deck"] is not None


def test_keyboard_and_s4_bindings_are_stored_separately(store) -> None:
    actions.set_binding("load.deck_a", Binding("key", 0x41, 0, "A"))
    assert actions.binding_for("load.deck_a", s4=True) == actions.BY_ID["load.deck_a"].default_s4


# ── Capture helpers ─────────────────────────────────────────────────────────


def test_capture_prefers_a_physical_binding(qapp) -> None:
    from ui.physical_keys import supports_physical
    from ui.shortcut_edit import binding_from_event

    b = binding_from_event(_event(Qt.Key.Key_Z, native=13))
    assert b is not None
    assert b.kind == ("phys" if supports_physical() else "key")


def test_capture_refuses_a_bare_modifier(qapp) -> None:
    from ui.shortcut_edit import binding_from_event

    assert binding_from_event(_event(Qt.Key.Key_Shift, Qt.KeyboardModifier.ShiftModifier)) is None


def test_every_action_has_a_handler() -> None:
    """A registry entry with no handler is a shortcut that silently does
    nothing — the one failure mode this indirection introduces."""
    from unittest.mock import MagicMock

    from ui.main_window import build_action_handlers

    handlers = build_action_handlers(MagicMock())
    missing = {a.id for a in actions.ACTIONS} - set(handlers)
    assert not missing, f"actions with no handler: {sorted(missing)}"


def test_handlers_call_through_with_the_right_argument() -> None:
    from unittest.mock import MagicMock

    from ui.main_window import build_action_handlers

    now = MagicMock()
    handlers = build_action_handlers(now)

    handlers["load.deck_b"]()
    now.load_selected_to_deck.assert_called_once_with("B")

    handlers["results.up"]()
    now.navigate_results.assert_called_once_with(-1)


def test_capture_records_the_modifiers(qapp) -> None:
    from ui.shortcut_edit import binding_from_event

    b = binding_from_event(_event(Qt.Key.Key_Left, Qt.KeyboardModifier.ControlModifier))
    assert b.mods == MOD_CTRL
    assert "Ctrl" in b.display()


# ── Forwarding the rest to Traktor ──────────────────────────────────────────


def test_bare_modifiers_are_not_forwarded(qapp) -> None:
    """Posting a lone modifier can leave Traktor's modifier state stuck."""
    from core import keyforward

    for key in (Qt.Key.Key_Shift, Qt.Key.Key_Control, Qt.Key.Key_Alt, Qt.Key.Key_Meta):
        assert not keyforward.should_forward(_event(key))


def test_command_combos_stay_in_the_app(qapp) -> None:
    """Cmd+F / Cmd+, belong to our menu, not to Traktor."""
    from core import keyforward

    assert not keyforward.should_forward(_event(Qt.Key.Key_F, Qt.KeyboardModifier.MetaModifier))


def test_ordinary_keys_are_forwarded(qapp) -> None:
    from core import keyforward

    for key in (Qt.Key.Key_W, Qt.Key.Key_1, Qt.Key.Key_Space, Qt.Key.Key_Left):
        assert keyforward.should_forward(_event(key))


def test_shifted_keys_are_forwarded(qapp) -> None:
    from core import keyforward

    assert keyforward.should_forward(_event(Qt.Key.Key_W, Qt.KeyboardModifier.ShiftModifier))


def test_forwarding_without_a_target_is_a_safe_no_op(qapp, monkeypatch) -> None:
    from core import keyforward

    # Never post to a Traktor that happens to be running on the test machine.
    monkeypatch.setattr(keyforward, "traktor_pid", lambda: None)
    assert keyforward.post_key(13, down=True) is False  # none found
    assert keyforward.post_key(13, 0, down=True, pid=0) is False  # no Traktor
    # Key code 0 is kVK_ANSI_A, a real key — see tests/unit/test_keyforward.py.
