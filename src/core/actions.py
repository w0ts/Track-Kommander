"""The action registry — one place that says what can be triggered and how.

Before this, every input path carried its own hardcoded mapping: `QShortcut`s
for load-to-deck in `ui/now_playing.py`, an application event filter for
play/pause and hotcues in `ui/main_window.py`, and four CUE-button bits
decoded inside `workers/s4mk2_watcher.py`. Nothing could see the whole picture,
so nothing could tell the user what was bound — or notice that two things were
bound to the same key.

They were. `Shift+Left` was both "load selected into deck A" (a `QShortcut`)
and "beat jump back 16" (the event filter). Qt resolves a `QShortcut` before
delivering the `KeyPress`, so the load won and the beat jump was unreachable —
measured, not assumed. A registry makes that kind of thing a visible conflict
instead of dead code.

Bindings live in ``QSettings`` under ``shortcuts/<action id>``; an absent key
means "use the default", so a fresh install and a reset behave identically.

Keyboard bindings are **position-based** wherever the platform provides a
native code (see :mod:`core.physical_keys`): the key in the QWERTY "W" position
stays play/pause on AZERTY, where it types ``Z``. That property predates this
module and is preserved — a captured binding records the physical code, not
the character.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from core.physical_keys import native_code

if TYPE_CHECKING:  # pragma: no cover — typing only
    from PySide6.QtGui import QKeyEvent

log = logging.getLogger(__name__)

SETTINGS_PREFIX = "shortcuts"

# Modifiers we distinguish. Qt reports others (keypad, groupswitch) that would
# make an otherwise-identical chord fail to match.
MOD_SHIFT = 0x02000000
MOD_CTRL = 0x04000000
MOD_ALT = 0x08000000
MOD_META = 0x10000000
MOD_MASK = MOD_SHIFT | MOD_CTRL | MOD_ALT | MOD_META

_MOD_NAMES = ((MOD_META, "Cmd"), (MOD_CTRL, "Ctrl"), (MOD_ALT, "Alt"), (MOD_SHIFT, "Shift"))


@dataclass(frozen=True)
class Binding:
    """One input that triggers an action.

    ``kind`` is ``"phys"`` for a position-based key, ``"key"`` for a
    layout-dependent Qt key, or ``"s4"`` for an S4 MK2 control.
    """

    kind: str
    code: int | str
    mods: int = 0
    label: str = ""

    @property
    def is_keyboard(self) -> bool:
        return self.kind in ("phys", "key")

    def display(self) -> str:
        """What to show the user."""
        if self.label:
            return self.label
        parts = [name for bit, name in _MOD_NAMES if self.mods & bit]
        parts.append(str(self.code))
        return " + ".join(parts)

    def to_json(self) -> str:
        return json.dumps(
            {"kind": self.kind, "code": self.code, "mods": self.mods, "label": self.label}
        )

    @staticmethod
    def from_json(raw: str) -> Binding | None:
        try:
            d = json.loads(raw)
            return Binding(
                kind=str(d["kind"]),
                code=d["code"],
                mods=int(d.get("mods", 0)),
                label=str(d.get("label", "")),
            )
        except (TypeError, ValueError, KeyError):
            log.warning("ignoring malformed binding %r", raw)
            return None


@dataclass(frozen=True)
class Action:
    """Something the user can trigger, and what triggers it by default."""

    id: str
    label: str
    group: str
    default: Binding | None = None
    default_s4: Binding | None = None


def _key(qt_key: int, mods: int, label: str) -> Binding:
    return Binding("key", qt_key, mods, label)


def _s4(control: str, label: str) -> Binding:
    return Binding("s4", control, 0, label)


# Qt key values used in the defaults, spelled out so this module does not need
# to import QtCore at module scope.
_K_LEFT, _K_RIGHT = 0x01000012, 0x01000014
_K_UP, _K_DOWN = 0x01000013, 0x01000015
_K_RETURN = 0x01000004
_K_PAGEUP, _K_PAGEDOWN = 0x01000016, 0x01000017


def _build_actions() -> tuple[Action, ...]:
    """The registry.

    Only browsing and loading are bound by default. Kommander sits on top of
    Traktor and holds keyboard focus, so every key it claims is a key Traktor
    never sees — and Traktor already has the user's own mapping for transport,
    hotcues and beat jumps. Anything unbound is forwarded to Traktor instead
    (see :mod:`core.keyforward`), so the default is: this app takes the few
    keys it genuinely needs and gets out of the way.

    Transport — play/pause, hotcues, beat jumps — is deliberately absent: those
    are Traktor's job, reached by forwarding the keystroke rather than by
    Kommander implementing its own copy.
    """
    return (
        # ── Browsing — the list is ours, so these are bound ──────────────────
        Action(
            "results.up",
            "Select previous result",
            "Browse",
            _key(_K_UP, 0, "↑"),
            _s4("browse_ccw", "R.Shift + Browse ↺"),
        ),
        Action(
            "results.down",
            "Select next result",
            "Browse",
            _key(_K_DOWN, 0, "↓"),
            _s4("browse_cw", "R.Shift + Browse ↻"),
        ),
        Action(
            "results.page_up",
            "Select a page of results back",
            "Browse",
            _key(_K_PAGEUP, 0, "Page Up"),
        ),
        Action(
            "results.page_down",
            "Select a page of results forward",
            "Browse",
            _key(_K_PAGEDOWN, 0, "Page Down"),
        ),
        # ── Loading — also ours: Traktor can't load from our list ────────────
        Action(
            "load.deck_a",
            "Load selected into deck A",
            "Load",
            _key(_K_LEFT, MOD_SHIFT, "Shift + ←"),
            _s4("cue1", "R.Shift + CH1 CUE"),
        ),
        Action(
            "load.deck_b",
            "Load selected into deck B",
            "Load",
            _key(_K_RIGHT, MOD_SHIFT, "Shift + →"),
            _s4("cue2", "R.Shift + CH2 CUE"),
        ),
        Action(
            "load.deck_c",
            "Load selected into deck C",
            "Load",
            _key(_K_LEFT, MOD_ALT, "Alt + ←"),
            _s4("cue3", "R.Shift + CH3 CUE"),
        ),
        Action(
            "load.deck_d",
            "Load selected into deck D",
            "Load",
            _key(_K_RIGHT, MOD_ALT, "Alt + →"),
            _s4("cue4", "R.Shift + CH4 CUE"),
        ),
        Action(
            "load.free_deck",
            "Load into the first free deck",
            "Load",
            _key(_K_RETURN, 0, "Return"),
            _s4("browse_push", "R.Shift + Browse push"),
        ),
        # Capturing a deck's grid offset is not here: it is a deliberate act,
        # done from the deck button's right-click menu (core.grid_fix).
    )


ACTIONS: tuple[Action, ...] = _build_actions()
BY_ID: dict[str, Action] = {a.id: a for a in ACTIONS}

#: Group order for the settings UI.
GROUPS: tuple[str, ...] = ("Browse", "Load")


# ── Stored bindings ─────────────────────────────────────────────────────────


def _settings():
    from PySide6.QtCore import QSettings

    return QSettings()


def _key_for(action_id: str, s4: bool) -> str:
    return f"{SETTINGS_PREFIX}/{'s4/' if s4 else ''}{action_id}"


def binding_for(action_id: str, *, s4: bool = False) -> Binding | None:
    """The active binding for an action — stored if set, else the default."""
    action = BY_ID.get(action_id)
    if action is None:
        return None
    raw = _settings().value(_key_for(action_id, s4), "", type=str)
    if isinstance(raw, str) and raw:
        stored = Binding.from_json(raw)
        # An explicitly cleared binding is stored as "none" so it does not
        # silently fall back to the default the user just removed.
        if stored is not None:
            return None if stored.kind == "none" else stored
    return action.default_s4 if s4 else action.default


def set_binding(action_id: str, binding: Binding | None, *, s4: bool = False) -> None:
    """Bind an action, or unbind it with ``None``."""
    s = _settings()
    value = (binding or Binding("none", 0)).to_json()
    s.setValue(_key_for(action_id, s4), value)
    s.sync()
    _bindings_cache.clear()


def reset_binding(action_id: str, *, s4: bool = False) -> None:
    """Drop the stored binding so the default applies again."""
    s = _settings()
    s.remove(_key_for(action_id, s4))
    s.sync()
    _bindings_cache.clear()


def reset_all() -> None:
    """Return every action to its default binding."""
    s = _settings()
    s.beginGroup(SETTINGS_PREFIX)
    s.remove("")
    s.endGroup()
    s.sync()
    _bindings_cache.clear()


#: The binding table per (settings file, s4). Every key press is matched
#: against it, and building it reads and decodes one setting per action.
#: The functions above are the only writers and clear it; keyed by the
#: settings file so a different store (tests point QSettings elsewhere)
#: never sees another's table.
_bindings_cache: dict[tuple[str, bool], dict[str, Binding | None]] = {}


def all_bindings(*, s4: bool = False) -> dict[str, Binding | None]:
    """Active binding per action id. A fresh dict the caller may change."""
    key = (_settings().fileName(), s4)
    table = _bindings_cache.get(key)
    if table is None:
        table = {a.id: binding_for(a.id, s4=s4) for a in ACTIONS}
        _bindings_cache[key] = table
    return dict(table)


# ── Matching ────────────────────────────────────────────────────────────────


def event_signature(event: QKeyEvent) -> tuple[tuple[str, int | str, int], ...]:
    """The binding identities a key event could match, most specific first.

    A physical match is preferred: it is the one that survives a layout change.
    The logical key is offered as a fallback for platforms with no native
    codes, and for bindings captured there.
    """
    mods = int(event.modifiers().value) & MOD_MASK
    out: list[tuple[str, int | str, int]] = []
    code = native_code(event)
    if code:
        out.append(("phys", code, mods))
    out.append(("key", int(event.key()), mods))
    return tuple(out)


def match(event: QKeyEvent, bindings: dict[str, Binding | None] | None = None) -> str | None:
    """The action id a key event triggers, or None.

    Ties are impossible to resolve sensibly, so the first action in registry
    order wins and :func:`conflicts` is what surfaces the problem to the user.
    """
    table = all_bindings() if bindings is None else bindings
    for kind, code, mods in event_signature(event):
        for action in ACTIONS:
            b = table.get(action.id)
            if b is not None and b.kind == kind and b.code == code and b.mods == mods:
                return action.id
    return None


def match_s4(control: str, bindings: dict[str, Binding | None] | None = None) -> str | None:
    """The action id an S4 control triggers, or None."""
    table = all_bindings(s4=True) if bindings is None else bindings
    for action in ACTIONS:
        b = table.get(action.id)
        if b is not None and b.kind == "s4" and b.code == control:
            return action.id
    return None


def conflicts(
    bindings: dict[str, Binding | None] | None = None, *, s4: bool = False
) -> dict[str, list[str]]:
    """Bindings claimed by more than one action, as display text → action ids.

    Two actions on one key is not an error — the user may be mid-edit — but it
    always means one of them will never fire, so it has to be visible.
    """
    table = all_bindings(s4=s4) if bindings is None else bindings
    seen: dict[tuple, list[str]] = {}
    labels: dict[tuple, str] = {}
    for action in ACTIONS:
        b = table.get(action.id)
        if b is None:
            continue
        sig = (b.kind, b.code, b.mods)
        seen.setdefault(sig, []).append(action.id)
        labels[sig] = b.display()
    return {labels[sig]: ids for sig, ids in seen.items() if len(ids) > 1}
