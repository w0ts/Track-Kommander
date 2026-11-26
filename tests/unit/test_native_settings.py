"""Traktor's settings from its memory inside Traktor; Traktor Settings.tsi outside it."""

from __future__ import annotations

import ctypes
from pathlib import Path

from core import traktor_folders
from workers import native_settings

INSIDE = "/repo/traktor-native/re/embed_tk.py"


class _Fn:
    """A stand-in for libtk's tk_setting_get."""

    def __init__(self, value: str | None):
        self.value = value
        self.argtypes = None
        self.restype = None

    def __call__(self, name: bytes, buf, cap: int) -> int:
        if self.value is None:
            return -2
        data = self.value.encode()
        ctypes.memmove(buf, data, len(data))
        return len(data)


def test_outside_traktor_there_is_no_native_setting(monkeypatch) -> None:
    monkeypatch.delenv("TK_EMBED", raising=False)
    assert native_settings.setting("Browser.Dir.Music") is None


def test_inside_traktor_a_setting_is_read_from_memory(monkeypatch) -> None:
    monkeypatch.setattr(native_settings, "process_symbol", lambda name: _Fn("a;b"))
    assert native_settings.setting("Browser.Dir.Music") == "a;b"
    monkeypatch.setattr(native_settings, "process_symbol", lambda name: _Fn(None))
    assert native_settings.setting("Browser.Dir.Music") is None


def test_inside_traktor_music_folders_never_read_the_settings_file(monkeypatch) -> None:
    monkeypatch.setenv("TK_EMBED", INSIDE)

    def no_file():
        raise AssertionError("Traktor Settings.tsi looked up inside Traktor")

    monkeypatch.setattr("core.traktor.find_traktor_nml", no_file)
    monkeypatch.setattr(
        traktor_folders, "_native_setting", lambda name: "Macintosh HD:Users:me:Music:Tracks:;"
    )
    assert traktor_folders.traktor_music_folders() == [Path("/Users/me/Music/Tracks")]


def test_inside_traktor_memory_holds_plain_paths(monkeypatch) -> None:
    """Traktor's memory has "/Users/me/Music/Tracks", not the settings file's colon form."""
    monkeypatch.setenv("TK_EMBED", INSIDE)
    monkeypatch.setattr(
        traktor_folders,
        "_native_setting",
        lambda name: "/Users/me/Music/Tracks;/Users/me/Music/Downloads",
    )
    assert traktor_folders.traktor_music_folders() == [
        Path("/Users/me/Music/Tracks"),
        Path("/Users/me/Music/Downloads"),
    ]


def test_inside_traktor_unreadable_folders_are_none(monkeypatch) -> None:
    monkeypatch.setenv("TK_EMBED", INSIDE)
    monkeypatch.setattr(traktor_folders, "_native_setting", lambda name: None)
    assert traktor_folders.traktor_music_folders() == []


def test_inside_traktor_before_the_reader_is_registered_there_are_no_folders(monkeypatch) -> None:
    monkeypatch.setenv("TK_EMBED", INSIDE)
    monkeypatch.setattr(traktor_folders, "_native_setting", None)
    monkeypatch.setattr("core.traktor.find_traktor_nml", lambda: None)
    assert traktor_folders.traktor_music_folders() == []


def test_the_ui_registers_the_native_reader() -> None:
    traktor_folders.set_native_setting(native_settings.setting)
    assert traktor_folders._native_setting is native_settings.setting
    traktor_folders.set_native_setting(None)
