"""The log bundle a user sends when something goes wrong on their machine."""

from __future__ import annotations

import tomllib
import zipfile
from datetime import datetime
from pathlib import Path, PureWindowsPath

import core.log_bundle as lb
from core.log_bundle import diagnostics, write_bundle

NOW = datetime(2026, 9, 25, 18, 30, 5)


def test_the_bundle_holds_the_logs_and_the_report(tmp_path) -> None:
    logs = tmp_path / "logs"
    logs.mkdir()
    # As bytes: the bundle holds a log as it is on disk, and written as text
    # it is not the same file on Windows, where every line ends CR LF.
    (logs / "log.log").write_bytes(b"scan started\n")
    (logs / "crash.log").write_bytes(b"fault\n")

    z = write_bundle(logs, tmp_path / "out", now=NOW, report=lambda: "report\n")

    assert z.name == "TrackKommander-log-2026-09-25_183005.zip"
    with zipfile.ZipFile(z) as zf:
        assert sorted(zf.namelist()) == ["crash.log", "diagnostics.txt", "log.log"]
        assert zf.read("log.log") == b"scan started\n"
        assert zf.read("diagnostics.txt") == b"report\n"


def test_a_missing_crash_log_is_not_an_error(tmp_path) -> None:
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "log.log").write_text("x", encoding="utf-8")
    z = write_bundle(logs, tmp_path, now=NOW, report=lambda: "")
    with zipfile.ZipFile(z) as zf:
        assert "crash.log" not in zf.namelist()


# ── Whose machine it was ────────────────────────────────────────────────────
# The bundle is sent to someone else. Every path under the home folder names
# the account it came from, and none of what it is sent for needs that.


def test_the_home_folder_is_not_named_in_the_logs(tmp_path, monkeypatch) -> None:
    home = tmp_path / "Users" / "jane.doe"
    monkeypatch.setattr(lb.Path, "home", staticmethod(lambda: home))
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "log.log").write_bytes(
        f"analysing {home}/Music/Techno/a.mp3\nwrote {home}/Documents/x\n".encode()
    )

    z = write_bundle(logs, tmp_path / "out", now=NOW, report=lambda: "")

    with zipfile.ZipFile(z) as zf:
        text = zf.read("log.log").decode("utf-8")
    assert "jane.doe" not in text
    assert text == "analysing ~/Music/Techno/a.mp3\nwrote ~/Documents/x\n"


def test_the_home_folder_is_not_named_in_the_report(tmp_path, monkeypatch) -> None:
    home = tmp_path / "Users" / "jane.doe"
    monkeypatch.setattr(lb.Path, "home", staticmethod(lambda: home))
    logs = tmp_path / "logs"
    logs.mkdir()

    z = write_bundle(
        logs,
        tmp_path / "out",
        now=NOW,
        report=lambda: f"traktor: collection {home}/Documents/c.nml\n",
    )

    with zipfile.ZipFile(z) as zf:
        assert zf.read("diagnostics.txt") == (b"traktor: collection ~/Documents/c.nml\n")


def test_a_windows_path_is_found_with_its_backslashes_doubled(monkeypatch) -> None:
    """How it reads after repr() or JSON, which is how a path often gets
    into a log line. Neither plain spelling is in that text, so the account
    name went out with the bundle."""
    home = PureWindowsPath(r"C:\Users\jane.doe")
    monkeypatch.setattr(lb.Path, "home", staticmethod(lambda: home))

    assert lb.without_home(r"scanning 'C:\\Users\\jane.doe\\Music\\a.mp3'") == (
        r"scanning '~\\Music\\a.mp3'"
    )
    assert lb.without_home(r"scanning C:\Users\jane.doe\Music\a.mp3") == (r"scanning ~\Music\a.mp3")
    assert lb.without_home("scanning C:/Users/jane.doe/Music/a.mp3") == ("scanning ~/Music/a.mp3")


def test_a_path_outside_the_home_folder_is_left_alone(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(lb.Path, "home", staticmethod(lambda: tmp_path / "Users" / "jane.doe"))
    assert lb.without_home("/Volumes/Music/a.mp3") == "/Volumes/Music/a.mp3"


def test_a_log_that_is_not_text_still_goes_in(tmp_path) -> None:
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "log.log").write_bytes(b"ok \xff\xfe not utf-8\n")

    z = write_bundle(logs, tmp_path / "out", now=NOW, report=lambda: "")

    with zipfile.ZipFile(z) as zf:
        assert zf.read("log.log").startswith(b"ok ")


def test_the_report_survives_a_failing_probe(monkeypatch) -> None:
    """One broken probe — no database yet, say — must not cost the rest."""

    def _boom():
        raise RuntimeError("no db")

    monkeypatch.setattr(lb, "_analysis_state", _boom)
    text = diagnostics()
    assert "version:" in text and "os:" in text
    assert "analysis: unavailable (RuntimeError: no db)" in text


def test_a_frozen_build_reads_its_version_from_the_bundled_pyproject(monkeypatch, tmp_path) -> None:
    """The bug: a Nuitka build has no package metadata and Qt was never told
    the version, so a tester's diagnostics said "version: unknown"."""
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "9.8.7"\n')
    monkeypatch.setattr(lb, "bundle_data_dir", lambda: tmp_path)
    monkeypatch.setattr(lb, "_qt_version", lambda: "")
    assert lb.app_version() == "9.8.7"


def test_from_source_the_version_is_the_repo_pyproject() -> None:
    root = Path(__file__).resolve().parents[2]
    expected = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"][
        "version"
    ]
    assert lb.app_version() == expected


def test_the_version_qt_makes_up_is_not_the_applications(monkeypatch, tmp_path) -> None:
    """Nobody tells Qt the version, so it answers with what it finds in the
    executable. On Windows, run from source, that is the interpreter's:
    About and the diagnostics said "3.13.12150.1013", and the update check
    compared releases against it."""
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "9.8.7"\n', encoding="utf-8")
    monkeypatch.setattr(lb, "bundle_data_dir", lambda: tmp_path)
    monkeypatch.setattr(lb, "_qt_version", lambda: "3.13.12150.1013")
    assert lb.app_version() == "9.8.7"


def test_without_a_pyproject_qt_is_asked(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(lb, "bundle_data_dir", lambda: tmp_path)
    monkeypatch.setattr(lb, "_qt_version", lambda: "1.2.3")
    assert lb.app_version() == "1.2.3"


# ── What another layer adds to the report ──────────────────────────────────
# The UI watchdog is the UI's; core cannot import it. It hands the report a
# probe instead.


def test_a_probe_another_layer_added_is_in_the_report(monkeypatch) -> None:
    monkeypatch.setattr(lb, "_extra_probes", ())
    lb.add_probe("UI lag logging", lambda: "on, 2 stall(s)")
    assert "UI lag logging: on, 2 stall(s)\n" in lb.diagnostics()


def test_a_failing_added_probe_costs_only_its_own_line(monkeypatch) -> None:
    def _boom():
        raise RuntimeError("no widget")

    monkeypatch.setattr(lb, "_extra_probes", ())
    lb.add_probe("UI lag logging", _boom)
    report = lb.diagnostics()
    assert "UI lag logging: unavailable (RuntimeError: no widget)" in report
    assert "version:" in report
