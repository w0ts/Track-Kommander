"""Traktor's own app properties, read, written and watched by direct calls (integrated mode).

When Track Kommander runs inside Traktor (the patched copy with libtk injected), libtk
exports a small C API over Traktor's property registry: the C++ objects behind the
``AppProperty { path: "app.traktor..." }`` items the D2 QML bridge uses. This module
binds that API with ctypes, so deck state arrives as Traktor's change callbacks and
commands are plain calls: no QML bridge, no HTTP.

Every call must run on Traktor's main thread, which is where TK's Qt code runs in
integrated mode; libtk refuses calls from any other thread. A watched property's
callbacks are delivered on the main thread too, one call per burst of changes, so a
callback should read the value it needs (``get``) rather than count notifications.

Private and Mac-only, like everything under ``traktor-native/``.
"""

from __future__ import annotations

import ctypes
import logging
import math
from collections.abc import Callable
from typing import Protocol

from workers.native_lib import process_lib

log = logging.getLogger(__name__)

# libtk's property types (tk_props.mm, TKPropType).
_FLOAT, _BOOL, _INT, _STRING = 1, 2, 3, 4
_STR_CAP = 2048

_CALLBACK = ctypes.CFUNCTYPE(None, ctypes.c_int)

Value = float | bool | int | str


class PropertyBus(Protocol):
    """What the native deck bridge needs: the test fakes implement the same."""

    def get(self, path: str) -> Value | None: ...

    def set(self, path: str, value: Value) -> bool: ...

    def watch(self, path: str, fn: Callable[[], None]) -> bool: ...

    def load_path(self, deck_index: int, path: str) -> bool: ...


def _bind(lib: ctypes.CDLL) -> None:
    lib.tk_prop_handle.argtypes = [ctypes.c_char_p]
    lib.tk_prop_handle.restype = ctypes.c_int
    lib.tk_prop_type.argtypes = [ctypes.c_int]
    lib.tk_prop_type.restype = ctypes.c_int
    lib.tk_prop_get_num.argtypes = [ctypes.c_int]
    lib.tk_prop_get_num.restype = ctypes.c_double
    lib.tk_prop_get_str.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
    lib.tk_prop_get_str.restype = ctypes.c_int
    lib.tk_prop_set_num.argtypes = [ctypes.c_int, ctypes.c_double]
    lib.tk_prop_set_num.restype = ctypes.c_int
    lib.tk_prop_set_str.argtypes = [ctypes.c_int, ctypes.c_char_p]
    lib.tk_prop_set_str.restype = ctypes.c_int
    lib.tk_prop_watch.argtypes = [ctypes.c_int]
    lib.tk_prop_watch.restype = ctypes.c_int
    lib.tk_prop_set_callback.argtypes = [_CALLBACK]
    lib.tk_prop_set_callback.restype = None
    lib.tk_prop_list.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int]
    lib.tk_prop_list.restype = ctypes.c_int
    lib.tk_load_path.argtypes = [ctypes.c_int, ctypes.c_char_p]
    lib.tk_load_path.restype = ctypes.c_int


def _process_lib() -> ctypes.CDLL | None:
    """This process's symbols, if libtk is loaded into it (i.e. we run inside Traktor)."""
    lib = process_lib()
    if lib is None:
        return None
    return lib if hasattr(lib, "tk_prop_handle") and hasattr(lib, "tk_load_path") else None


class NativeProps:
    """libtk's property API. Build with :meth:`attach`; ``None`` outside Traktor."""

    def __init__(self, lib: ctypes.CDLL) -> None:
        _bind(lib)
        self._lib = lib
        self._ids: dict[str, int] = {}
        self._types: dict[int, int] = {}
        self._watchers: dict[int, list[Callable[[], None]]] = {}
        # Kept on the instance: ctypes frees a callback object that nothing references.
        self._c_callback = _CALLBACK(self._on_change)
        lib.tk_prop_set_callback(self._c_callback)

    @classmethod
    def attach(cls) -> NativeProps | None:
        lib = _process_lib()
        if lib is None:
            return None
        return cls(lib)

    def detach(self) -> None:
        """Stop the callbacks (before this object goes away)."""
        self._lib.tk_prop_set_callback(_CALLBACK())
        self._watchers.clear()

    def _id(self, path: str) -> int | None:
        pid = self._ids.get(path)
        if pid is None:
            pid = int(self._lib.tk_prop_handle(path.encode()))
            if pid < 0:
                log.warning("Traktor has no property %s", path)
                return None
            self._ids[path] = pid
            self._types[pid] = int(self._lib.tk_prop_type(pid))
        return pid

    def get(self, path: str) -> Value | None:
        pid = self._id(path)
        if pid is None:
            return None
        kind = self._types[pid]
        if kind == _STRING:
            buf = ctypes.create_string_buffer(_STR_CAP)
            if self._lib.tk_prop_get_str(pid, buf, _STR_CAP) < 0:
                return None
            return buf.value.decode("utf-8", errors="replace")
        number = float(self._lib.tk_prop_get_num(pid))
        if math.isnan(number):
            return None
        if kind == _BOOL:
            return number != 0.0
        if kind == _INT:
            return int(number)
        return number

    def set(self, path: str, value: Value) -> bool:
        pid = self._id(path)
        if pid is None:
            return False
        if self._types[pid] == _STRING:
            return self._lib.tk_prop_set_str(pid, str(value).encode()) == 0
        return self._lib.tk_prop_set_num(pid, float(value)) == 0

    def watch(self, path: str, fn: Callable[[], None]) -> bool:
        pid = self._id(path)
        if pid is None or self._lib.tk_prop_watch(pid) != 0:
            return False
        self._watchers.setdefault(pid, []).append(fn)
        return True

    def load_path(self, deck_index: int, path: str) -> bool:
        """Load the collection track at *path* onto deck *deck_index* (0 = A). libtk answers -2
        for a path not in the collection in that spelling: then the decomposed (NFD) one, as
        Traktor may store it."""
        import unicodedata

        for spelled in dict.fromkeys((path, unicodedata.normalize("NFD", path))):
            if self._lib.tk_load_path(deck_index, spelled.encode()) == 0:
                return True
        return False

    def list(self, prefix: str = "app.") -> list[tuple[str, str]]:
        """(path, type) of every property under *prefix*: discovery, not the hot path."""
        need = int(self._lib.tk_prop_list(prefix.encode(), None, 0))
        if need <= 0:
            return []
        buf = ctypes.create_string_buffer(need + 1)
        self._lib.tk_prop_list(prefix.encode(), buf, need + 1)
        rows = []
        for line in buf.value.decode("utf-8", errors="replace").splitlines():
            path, _, kind = line.partition("\t")
            rows.append((path, kind))
        return rows

    def _on_change(self, pid: int) -> None:
        # Called from C: an exception must not escape into libtk.
        for fn in self._watchers.get(pid, ()):
            try:
                fn()
            except Exception:
                log.exception("property callback failed (#%d)", pid)
