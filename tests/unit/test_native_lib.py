"""workers.native_lib: libtk's symbols only inside Traktor."""

from __future__ import annotations

import ctypes

import pytest

from workers import native_lib


def test_outside_traktor_there_is_no_process_lib(monkeypatch):
    monkeypatch.delenv("TK_EMBED", raising=False)
    called = []
    monkeypatch.setattr(ctypes, "CDLL", lambda *a: called.append(a))
    assert native_lib.process_lib() is None
    assert native_lib.process_symbol("tk_load_path") is None
    assert called == []  # standalone TK never even looks (CDLL(None) raises on Windows)


@pytest.mark.parametrize("error", [OSError("no"), TypeError("Windows")])
def test_inside_traktor_a_failing_lookup_is_none(monkeypatch, error):
    monkeypatch.setenv("TK_EMBED", "/repo/traktor-native/re/embed_tk.py")

    def boom(*_a):
        raise error

    monkeypatch.setattr(ctypes, "CDLL", boom)
    assert native_lib.process_lib() is None


def test_inside_traktor_a_symbol_is_looked_up(monkeypatch):
    monkeypatch.setenv("TK_EMBED", "/repo/traktor-native/re/embed_tk.py")

    class FakeLib:
        tk_load_path = object()

    # macOS opens CDLL(None); Windows opens libtk.dll by its loaded module's handle.
    monkeypatch.setattr(native_lib, "_loaded_module_handle", lambda _name: 1)
    monkeypatch.setattr(ctypes, "CDLL", lambda *_a, **_k: FakeLib())
    assert native_lib.process_symbol("tk_load_path") is FakeLib.tk_load_path
    assert native_lib.process_symbol("tk_missing") is None


def test_on_windows_libtk_is_found_by_its_loaded_module(monkeypatch):
    """Windows has no CDLL(None): libtk.dll, loaded into Traktor by tk_launch.exe, is opened by
    its module handle (it is never loaded again from a path)."""
    monkeypatch.setenv("TK_EMBED", r"C:\tk\embed_tk_win.py")
    monkeypatch.setattr(native_lib.sys, "platform", "win32")
    monkeypatch.setattr(
        native_lib, "_loaded_module_handle", lambda name: 0x7FF0 if name == "libtk.dll" else 0
    )
    seen = []

    def fake_cdll(name, handle=None):
        seen.append((name, handle))
        return "lib"

    monkeypatch.setattr(ctypes, "CDLL", fake_cdll)
    assert native_lib.process_lib() == "lib"
    assert seen == [("libtk.dll", 0x7FF0)]


def test_on_windows_without_libtk_there_is_no_process_lib(monkeypatch):
    monkeypatch.setenv("TK_EMBED", r"C:\tk\embed_tk_win.py")
    monkeypatch.setattr(native_lib.sys, "platform", "win32")
    monkeypatch.setattr(native_lib, "_loaded_module_handle", lambda _name: 0)
    monkeypatch.setattr(ctypes, "CDLL", lambda *a, **k: pytest.fail("must not open anything"))
    assert native_lib.process_lib() is None
