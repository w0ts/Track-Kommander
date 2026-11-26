"""Show a file in Finder / Explorer / the desktop's file manager."""

from __future__ import annotations

from pathlib import Path

from core import reveal


def test_macos_selects_the_file_in_finder(monkeypatch, tmp_path) -> None:
    f = tmp_path / "a.mp3"
    f.write_bytes(b"x")
    calls: list[list[str]] = []
    monkeypatch.setattr(reveal.sys, "platform", "darwin")
    monkeypatch.setattr(reveal.subprocess, "Popen", lambda cmd, **kw: calls.append(cmd))
    assert reveal.reveal_in_file_manager(str(f)) is True
    assert calls == [["open", "-R", str(f)]]


def test_windows_selects_the_file_in_explorer(monkeypatch, tmp_path) -> None:
    f = tmp_path / "a.mp3"
    f.write_bytes(b"x")
    calls: list[list[str]] = []
    monkeypatch.setattr(reveal.sys, "platform", "win32")
    monkeypatch.setattr(reveal.subprocess, "Popen", lambda cmd, **kw: calls.append(cmd))
    assert reveal.reveal_in_file_manager(str(f)) is True
    assert calls == [["explorer", f"/select,{Path(f)}"]]


def test_elsewhere_opens_the_folder(monkeypatch, tmp_path) -> None:
    f = tmp_path / "a.mp3"
    f.write_bytes(b"x")
    opened: list[str] = []
    monkeypatch.setattr(reveal.sys, "platform", "linux")
    monkeypatch.setattr(reveal, "_open_url", lambda url: opened.append(url) or True)
    assert reveal.reveal_in_file_manager(str(f)) is True
    # As a URL, which is what is opened: on Windows the folder's path and
    # its URL do not even share their separators.
    assert opened == [tmp_path.as_uri()]


def test_a_missing_file_is_reported_not_revealed(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        reveal.subprocess, "Popen", lambda *a, **k: (_ for _ in ()).throw(AssertionError("ran"))
    )
    assert reveal.reveal_in_file_manager(str(tmp_path / "gone.mp3")) is False
