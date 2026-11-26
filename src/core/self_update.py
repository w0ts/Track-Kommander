"""Installing a newer Track Kommander in place, once the user has said yes.

Qt-free; the caller runs :func:`download` off the UI thread and the rest on
it. The download is checked against the digest GitHub computed when the
installer was uploaded, and on macOS the new app must also carry the running
app's Developer ID team — a build signed by anyone else is never installed.

The app cannot replace itself while it runs, so the last step hands over to
a small script and quits: it waits for this process to end, installs, and
opens the new version. Every refusal raises :class:`UpdateError`, and the
caller then opens the release page instead, which is what TK did before.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import shlex
import shutil
import ssl
import subprocess
import sys
import urllib.request
from collections.abc import Callable
from contextlib import AbstractContextManager
from email.message import Message
from pathlib import Path
from typing import Protocol

from core.update_check import DOWNLOADS, Asset

log = logging.getLogger(__name__)

_CHUNK = 256 * 1024
_TIMEOUT_S = 30
_TEAM_RE = re.compile(r"^TeamIdentifier=(\S+)$", re.MULTILINE)

#: Runs a command and returns its result; replaced in tests.
Run = Callable[..., subprocess.CompletedProcess]


class Response(Protocol):
    """What :func:`download` reads: an HTTP response, or a test's stand-in."""

    headers: Message

    def read(self, size: int = -1, /) -> bytes: ...


class UpdateError(Exception):
    """The update cannot go ahead; the message says why, for the log and user."""


class Cancelled(UpdateError):
    """The user stopped the download."""


def supported() -> bool:
    """Installing in place needs a built app, on macOS or Windows."""
    from core.runtime import is_frozen

    return is_frozen() and sys.platform in ("darwin", "win32")


def work_dir() -> Path:
    from core.runtime import user_data_dir

    return user_data_dir() / "update"


def fresh_work_dir() -> Path:
    """An empty work directory: what an earlier update left there goes."""
    work = work_dir()
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)
    return work


# ── Download ──────────────────────────────────────────────────────────────────


def _open(url: str) -> AbstractContextManager[Response]:
    try:
        import certifi

        ctx = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        ctx = ssl.create_default_context()
    req = urllib.request.Request(url, headers={"User-Agent": "TrackKommander-update"})
    return urllib.request.urlopen(req, timeout=_TIMEOUT_S, context=ctx)


def download(
    asset: Asset,
    dest_dir: Path,
    progress: Callable[[int, int], None] = lambda done, total: None,
    cancelled: Callable[[], bool] = lambda: False,
    opener: Callable[[str], AbstractContextManager[Response]] = _open,
) -> Path:
    """Fetch *asset* into *dest_dir* and check its SHA-256. Returns the file.

    *progress* gets (bytes so far, total or 0); *cancelled* is polled
    between chunks. A file that does not match is deleted before raising.
    """
    if not asset.url.startswith(DOWNLOADS):
        raise UpdateError(f"refusing to download from {asset.url}")
    name = Path(asset.name).name
    if not name or name != asset.name or name.startswith("."):
        raise UpdateError(f"unexpected installer name {asset.name!r}")
    target = dest_dir / name
    digest = hashlib.sha256()
    done = 0
    try:
        with opener(asset.url) as resp, target.open("wb") as out:
            total = int(resp.headers.get("Content-Length") or 0)
            while chunk := resp.read(_CHUNK):
                if cancelled():
                    raise Cancelled("download cancelled")
                out.write(chunk)
                digest.update(chunk)
                done += len(chunk)
                progress(done, total)
    except UpdateError:
        target.unlink(missing_ok=True)
        raise
    except OSError as exc:
        target.unlink(missing_ok=True)
        raise UpdateError(f"download failed: {exc}") from exc
    if digest.hexdigest() != asset.sha256:
        target.unlink(missing_ok=True)
        raise UpdateError(f"{name} does not match its published checksum")
    log.info("update: downloaded %s (%d bytes, checksum ok)", name, done)
    return target


# ── macOS ─────────────────────────────────────────────────────────────────────


def running_app_bundle(executable: str | None = None) -> Path | None:
    """The ``.app`` the running executable sits in, or None."""
    exe = Path(executable or sys.executable).resolve()
    for parent in exe.parents:
        if parent.suffix == ".app" and exe.is_relative_to(parent / "Contents" / "MacOS"):
            return parent
    return None


def team_id(app: Path, run: Run = subprocess.run) -> str | None:
    """The Developer ID team that signed *app*; None if ad-hoc or unsigned."""
    res = run(
        ["codesign", "-dv", "--verbose=2", str(app)], capture_output=True, text=True, check=False
    )
    m = _TEAM_RE.search(res.stderr or "")
    if res.returncode != 0 or not m or m.group(1) == "not":
        return None
    return m.group(1)


def stage_mac(dmg: Path, current: Path, run: Run = subprocess.run) -> Path:
    """Copy the app out of *dmg* next to it, after checking who signed it.

    The new bundle must have the running bundle's name, a valid signature,
    and the running app's team. Returns the staged ``.app``.
    """
    mount = dmg.parent / "mnt"
    mount.mkdir(exist_ok=True)
    attach = ["hdiutil", "attach", "-nobrowse", "-readonly", "-noautoopen", "-mountpoint"]
    res = run([*attach, str(mount), str(dmg)], capture_output=True, text=True, check=False)
    if res.returncode != 0:
        raise UpdateError(f"could not open {dmg.name}: {res.stderr.strip()}")
    staged = dmg.parent / current.name
    try:
        found = mount / current.name
        if not found.is_dir():
            raise UpdateError(f"{dmg.name} holds no {current.name}")
        res = run(["ditto", str(found), str(staged)], capture_output=True, text=True, check=False)
        if res.returncode != 0:
            raise UpdateError(f"could not copy the new app: {res.stderr.strip()}")
    finally:
        run(["hdiutil", "detach", str(mount), "-force"], capture_output=True, check=False)
    verify = ["codesign", "--verify", "--deep", "--strict", str(staged)]
    res = run(verify, capture_output=True, text=True, check=False)
    if res.returncode != 0:
        raise UpdateError(f"the new app's signature does not verify: {res.stderr.strip()}")
    ours, theirs = team_id(current, run), team_id(staged, run)
    if ours is None:
        raise UpdateError("this build is not Developer ID signed; install the new one by hand")
    if theirs != ours:
        raise UpdateError(f"the new app is signed by {theirs!r}, not {ours!r}")
    return staged


def mac_script(pid: int, staged: Path, target: Path, work: Path) -> str:
    """Wait for *pid* to end, put *staged* in place of *target*, reopen it.

    The old bundle is moved aside first and put back if the copy fails, so
    a failed update leaves the old version, not none.
    """
    q = shlex.quote
    old = target.with_name(target.name + ".previous")
    return f"""#!/bin/sh
# Track Kommander update: written and started by the app as it quit.
while kill -0 {pid} 2>/dev/null; do sleep 0.5; done
rm -rf {q(str(old))}
if mv {q(str(target))} {q(str(old))}; then
  if ditto {q(str(staged))} {q(str(target))}; then
    rm -rf {q(str(old))}
    echo "updated {q(str(target))}"
  else
    echo "copy failed, putting the old version back"
    rm -rf {q(str(target))}
    mv {q(str(old))} {q(str(target))}
  fi
else
  echo "could not move the old version aside"
fi
open {q(str(target))}
rm -rf {q(str(work))}
"""


# ── Windows ───────────────────────────────────────────────────────────────────


def _ps(text: str) -> str:
    """*text* as a PowerShell single-quoted string."""
    return "'" + text.replace("'", "''") + "'"


def windows_script(pid: int, msi: Path, exe: Path, log_file: Path) -> str:
    """Wait for *pid* to end, run the MSI (a major upgrade), reopen TK.

    ``/passive`` shows progress and the elevation prompt, nothing else; a
    refused prompt leaves the old version installed, which is reopened.
    """
    return f"""$ErrorActionPreference = 'Continue'
Wait-Process -Id {pid} -ErrorAction SilentlyContinue
$msiArgs = @('/i', '"{msi}"', '/passive', '/norestart')
$p = Start-Process -FilePath 'msiexec.exe' -ArgumentList $msiArgs -Wait -PassThru
"msiexec exit code: $($p.ExitCode)" | Out-File -FilePath {_ps(str(log_file))} -Append
Remove-Item -LiteralPath {_ps(str(msi))} -ErrorAction SilentlyContinue
Start-Process -FilePath {_ps(str(exe))}
"""


# ── Handing over ──────────────────────────────────────────────────────────────


def hand_over(installer: Path, popen: Callable[..., object] = subprocess.Popen) -> None:
    """Prepare the installer and start the script that finishes the job once
    this process has quit. The caller quits right after; raises
    :class:`UpdateError` (with nothing started) when it cannot go ahead."""
    work = installer.parent
    pid = os.getpid()
    log_file = work / "update.log"
    if sys.platform == "darwin":
        current = running_app_bundle()
        if current is None:
            raise UpdateError("not running from an app bundle")
        if not os.access(current.parent, os.W_OK):
            raise UpdateError(f"no permission to replace the app in {current.parent}")
        staged = stage_mac(installer, current)
        script = work / "update.sh"
        script.write_text(mac_script(pid, staged, current, work), encoding="utf-8")
        with log_file.open("ab") as out:
            popen(
                ["/bin/sh", str(script)],
                stdin=subprocess.DEVNULL,
                stdout=out,
                stderr=out,
                start_new_session=True,
            )
    elif sys.platform == "win32":
        script = work / "update.ps1"
        exe = Path(sys.executable).resolve()
        script.write_text(windows_script(pid, installer, exe, log_file), encoding="utf-8-sig")
        detached = 0x00000200 | 0x00000008  # CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS
        popen(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=detached,
        )
    else:
        raise UpdateError(f"no in-place update on {sys.platform}")
    log.info("update: handed over to %s; quitting", script)
