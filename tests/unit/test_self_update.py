"""Installing a newer Track Kommander in place.

The download must match GitHub's digest; on macOS the new app must carry the
running app's Developer ID team; the script that finishes the job after TK
quits must put the old version back when the copy fails.
"""

from __future__ import annotations

import hashlib
import io
import subprocess
from email.message import Message
from pathlib import Path

import pytest

from core import self_update as su
from core.update_check import DOWNLOADS, Asset

_PAYLOAD = b"x" * 600_000


class _Resp(io.BytesIO):
    def __init__(self, data: bytes) -> None:
        super().__init__(data)
        self.headers = Message()
        self.headers["Content-Length"] = str(len(data))


def _asset(data: bytes = _PAYLOAD, name: str = "Track.Kommander.dmg", url: str = "") -> Asset:
    return Asset(name, url or f"{DOWNLOADS}v0.0.9/{name}", hashlib.sha256(data).hexdigest())


def test_a_download_matching_its_digest_is_kept(tmp_path: Path) -> None:
    seen: list[tuple[int, int]] = []
    path = su.download(
        _asset(),
        tmp_path,
        progress=lambda d, t: seen.append((d, t)),
        opener=lambda u: _Resp(_PAYLOAD),
    )
    assert path.read_bytes() == _PAYLOAD
    assert seen[-1] == (len(_PAYLOAD), len(_PAYLOAD))


def test_a_download_not_matching_its_digest_is_deleted(tmp_path: Path) -> None:
    with pytest.raises(su.UpdateError, match="checksum"):
        su.download(_asset(b"other"), tmp_path, opener=lambda u: _Resp(_PAYLOAD))
    assert list(tmp_path.iterdir()) == []


def test_a_cancelled_download_is_deleted(tmp_path: Path) -> None:
    with pytest.raises(su.Cancelled):
        su.download(_asset(), tmp_path, cancelled=lambda: True, opener=lambda u: _Resp(_PAYLOAD))
    assert list(tmp_path.iterdir()) == []


def test_a_network_failure_is_an_update_error(tmp_path: Path) -> None:
    def boom(url: str) -> _Resp:
        raise OSError("offline")

    with pytest.raises(su.UpdateError, match="offline"):
        su.download(_asset(), tmp_path, opener=boom)


@pytest.mark.parametrize(
    "asset",
    [
        _asset(url="https://evil.example/Track.Kommander.dmg"),
        _asset(name="../Track.Kommander.dmg"),
        _asset(name=".hidden"),
    ],
)
def test_an_unexpected_address_or_name_is_refused(tmp_path: Path, asset: Asset) -> None:
    def never(url: str) -> _Resp:
        raise AssertionError("must not be fetched")

    with pytest.raises(su.UpdateError):
        su.download(asset, tmp_path, opener=never)


# ── macOS ─────────────────────────────────────────────────────────────────────


def test_the_running_bundle_is_found_from_its_executable() -> None:
    app = Path("/Applications/Track Kommander.app").resolve()  # resolve: C: on Windows
    exe = str(app / "Contents" / "MacOS" / "TrackKommander")
    assert su.running_app_bundle(exe) == app
    assert su.running_app_bundle("/usr/bin/python3") is None


def _codesign(teams: dict[str, str], verify_ok: bool = True):
    """A fake ``run``: hdiutil and ditto succeed, codesign answers from *teams*."""

    def run(cmd, **_kw) -> subprocess.CompletedProcess:
        if cmd[0] == "ditto":
            Path(cmd[2]).mkdir(parents=True)
        if cmd[:2] == ["codesign", "--verify"]:
            return subprocess.CompletedProcess(cmd, 0 if verify_ok else 1, "", "invalid")
        if cmd[:2] == ["codesign", "-dv"]:
            team = teams.get(Path(cmd[-1]).parent.name, "not set")
            return subprocess.CompletedProcess(cmd, 0, "", f"Identifier=x\nTeamIdentifier={team}\n")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    return run


@pytest.fixture
def dmg(tmp_path: Path) -> tuple[Path, Path]:
    """A downloaded dmg whose mount holds the app, and the running app."""
    work = tmp_path / "update"
    (work / "mnt" / "Track Kommander.app").mkdir(parents=True)
    current = tmp_path / "Applications" / "Track Kommander.app"
    current.mkdir(parents=True)
    return work / "Track.Kommander.dmg", current


def test_a_new_app_from_the_same_team_is_staged(dmg) -> None:
    file, current = dmg
    run = _codesign({"update": "TEAM1", "Applications": "TEAM1"})
    assert su.stage_mac(file, current, run=run) == file.parent / "Track Kommander.app"


def test_a_new_app_from_another_team_is_refused(dmg) -> None:
    file, current = dmg
    run = _codesign({"update": "EVIL", "Applications": "TEAM1"})
    with pytest.raises(su.UpdateError, match="EVIL"):
        su.stage_mac(file, current, run=run)


def test_an_ad_hoc_running_build_is_not_updated_in_place(dmg) -> None:
    file, current = dmg
    run = _codesign({"update": "TEAM1"})
    with pytest.raises(su.UpdateError, match="not Developer ID"):
        su.stage_mac(file, current, run=run)


def test_a_new_app_whose_signature_fails_is_refused(dmg) -> None:
    file, current = dmg
    run = _codesign({"update": "TEAM1", "Applications": "TEAM1"}, verify_ok=False)
    with pytest.raises(su.UpdateError, match="does not verify"):
        su.stage_mac(file, current, run=run)


def test_a_dmg_without_the_app_is_refused(tmp_path: Path) -> None:
    current = tmp_path / "Other.app"
    with pytest.raises(su.UpdateError, match="holds no"):
        su.stage_mac(tmp_path / "x.dmg", current, run=_codesign({}))


def _run_mac_script(tmp_path: Path, ditto_works: bool) -> Path:
    target = tmp_path / "Apps" / "Track Kommander.app"
    target.mkdir(parents=True)
    (target / "old").write_text("old")
    staged = tmp_path / "work" / "Track Kommander.app"
    staged.mkdir(parents=True)
    (staged / "new").write_text("new")
    script = su.mac_script(999_999_999, staged, target, tmp_path / "work")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "open").write_text("#!/bin/sh\nexit 0\n")
    ditto = 'cp -R "$1" "$2"' if ditto_works else "exit 1"
    (bin_dir / "ditto").write_text(f"#!/bin/sh\n{ditto}\n")
    for f in bin_dir.iterdir():
        f.chmod(0o755)
    env = {"PATH": f"{bin_dir}:/bin:/usr/bin"}
    subprocess.run(["/bin/sh", "-c", script], env=env, check=True, capture_output=True)
    return target


@pytest.mark.skipif(not Path("/bin/sh").exists(), reason="needs a POSIX shell")
def test_the_mac_script_swaps_the_bundle(tmp_path: Path) -> None:
    target = _run_mac_script(tmp_path, ditto_works=True)
    assert (target / "new").is_file() and not (target / "old").exists()
    assert not target.with_name(target.name + ".previous").exists()


@pytest.mark.skipif(not Path("/bin/sh").exists(), reason="needs a POSIX shell")
def test_the_mac_script_puts_the_old_version_back_when_the_copy_fails(tmp_path: Path) -> None:
    target = _run_mac_script(tmp_path, ditto_works=False)
    assert (target / "old").read_text() == "old"


# ── Windows ───────────────────────────────────────────────────────────────────


def test_the_windows_script_quotes_paths_for_powershell() -> None:
    script = su.windows_script(
        42,
        Path("C:/Users/O'Neil/update/TrackKommanderSetup.msi"),
        Path("C:/Program Files/Track Kommander/TrackKommander.exe"),
        Path("C:/Users/O'Neil/update/update.log"),
    )
    assert "Wait-Process -Id 42" in script
    assert "O''Neil" in script  # a quote in a path does not end the string
    assert "/passive" in script and "Start-Process -FilePath 'C:" in script
