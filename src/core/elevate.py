"""Run one privileged step, on macOS and Windows.

Patching Traktor's QML writes into a root-owned bundle, so it needs a real
elevation step. Three rules shape this module:

**One invocation for the whole job.** A series of individual elevated calls
prompts the user repeatedly and can leave a half-patched folder. So the job is
written to a JSON file, *one* helper run does all of it, and the result comes
back in another JSON file. The helper is idempotent, so a retry after a
failure is safe.

**Declining is not an error.** Cancelling the macOS dialog gives ``-128`` and
dismissing UAC gives ``5``. Both mean the user said no: the app falls back to
the manual instructions and stays fully usable with a clear "not patched"
status. Only the UI can tell the difference if this module reports it, hence
:attr:`ElevationResult.cancelled`.

**The command is quoted for everything that parses it.** On macOS that is
twice — AppleScript, then ``sh`` — so a path containing a space or a quote
would break, or worse, execute. Each word is quoted for ``sh``, and the whole
is then escaped for the AppleScript string it sits in.

**The job is pinned to what the user authorised.** The job file is in a folder
the user owns, and the prompt gives anything running as that user time to
rewrite it. So its SHA-256 goes on the command line, which is fixed at the
moment of asking, and the helper refuses a job that does not match
(:func:`core.d2_patch.run_job`).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from core import applescript, platform_modules

log = logging.getLogger(__name__)

_SHA256_HEX = re.compile(r"[0-9a-f]{64}")

#: ShellExecuteW returns this when the user dismisses the UAC prompt.
_WIN_ACCESS_DENIED = 5

#: ...and anything above 32 on success. Below that is an error code.
_WIN_SUCCESS_FLOOR = 32

#: osascript reports a cancelled authentication dialog with this.
_MAC_CANCELLED = "-128"

#: ...and this when the command it ran exited non-zero, which means the
#: helper started and its own result file holds the reason.
_MAC_NONZERO = "non-zero status"

#: The application's entry point in a source checkout: ``src/TrackKommander.py``,
#: two levels up from this file.
_SOURCE_ENTRY_POINT = Path(__file__).resolve().parents[1] / "TrackKommander.py"


@dataclass
class ElevationResult:
    """What happened, in a form the UI can act on and explain."""

    ok: bool
    step: str = ""
    error: str = ""
    cancelled: bool = False
    """The user declined. Not a failure — fall back to manual steps."""
    needs_full_disk_access: bool = False
    """macOS refused a write into another app's bundle. Full Disk Access is
    what lets the elevated helper through; actionable by the user, so it must
    not be buried."""
    originals_zip: str = ""
    """Where the job archived the files it was about to change. Carried back
    so the UI can name it: "backed up" is a claim, a path you can go and open
    is evidence."""
    tcc: str = ""
    """How the *elevated* process saw its own grants. The app and the helper
    disagreed once — granted in the app, refused as root — and this is the
    only place that disagreement is visible."""
    ran: bool = False
    """The helper actually started. If it did, **its own result file is the
    real answer** — this object only says whether elevation worked. osascript
    reports "exited with a non-zero status" for a helper that ran and failed,
    which is a completely different thing from one that never started."""

    def describe(self) -> str:
        if self.ok:
            return "done"
        if self.cancelled:
            return "cancelled"
        return f"{self.step or 'failed'}: {self.error}" if self.error else (self.step or "failed")


USER_CANCELLED = ElevationResult(
    ok=False, step="authorise", cancelled=True, error="the authentication prompt was cancelled"
)


def helper_command() -> list[str]:
    """The argv prefix that runs this application as its own helper.

    Frozen builds are a single executable, so that binary *is* the helper —
    which also keeps the UAC publisher name ours rather than a bare
    interpreter's. In development the entry point is a ``.py`` file, which is
    not executable and which ``do shell script`` would hand to ``sh``:

        /bin/sh: .../TrackKommander.py: Permission denied (126)

    so the interpreter has to go in front of it.

    The entry point is located from this file, not read from ``sys.argv[0]``:
    that names whatever was launched, which is only the application when the
    application is what was launched, and a relative one resolves against a
    working directory that may have changed since.
    """
    if getattr(sys, "frozen", False):
        return [sys.executable]
    return [sys.executable, str(_SOURCE_ENTRY_POINT)]


def _bundle_name(executable: str) -> str | None:
    """The ``.app`` an executable lives inside, if any."""
    for part in Path(executable).parts:
        if part.endswith(".app"):
            return part[:-4]
    return None


def responsible_app_name() -> str | None:
    """The application macOS attributes this process's TCC requests to.

    Not always us. A development run is ``python`` launched by an IDE, and
    macOS holds the **IDE** responsible — so a permission granted to the
    built app bundle does nothing, and telling the user to grant it to us
    sends them looking for an entry that is not there.

    Walks the parent chain for the nearest enclosing ``.app``. Returns None
    when there is none, in which case the caller should say something
    non-committal rather than name the wrong thing.
    """
    if sys.platform != "darwin":
        return None
    own = _bundle_name(sys.executable)
    if own:
        return own
    try:
        pid = os.getpid()
        for _ in range(8):  # bounded: never walk forever
            out = subprocess.run(
                ["ps", "-o", "ppid=,comm=", "-p", str(pid)],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            line = out.stdout.strip()
            if not line:
                return None
            parent, _, command = line.partition(" ")
            name = _bundle_name(command.strip())
            if name:
                return name
            pid = int(parent)
            if pid <= 1:
                return None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    return None


def is_elevated() -> bool:
    """Whether this process can already write to protected locations."""
    if sys.platform == "win32":
        try:
            return bool(platform_modules.win_ctypes().windll.shell32.IsUserAnAdmin())
        except Exception:
            return False
    return os.geteuid() == 0


# ── The job and its result ──────────────────────────────────────────────────


def write_job(directory: Path, job: dict[str, object]) -> Path:
    """Write the job description the helper will read. Returns its path."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "job.json"
    path.write_text(json.dumps(job, indent=1))
    return path


def job_digest(job: Path) -> str:
    """SHA-256 of the job file as it is now, in hex."""
    return hashlib.sha256(Path(job).read_bytes()).hexdigest()


def _checked(digest: str) -> str:
    """*digest* if it is one. It goes on a command line, unquoted, so it is
    not taken on trust."""
    if not _SHA256_HEX.fullmatch(digest):
        raise ValueError("the job digest is not a SHA-256 in hex")
    return digest


def read_result(path: Path) -> ElevationResult:
    """Read the helper's result file, or explain why there isn't one."""
    try:
        data = json.loads(Path(path).read_text())
    except OSError:
        return ElevationResult(False, "run", "no result file — the helper did not finish")
    except ValueError:
        return ElevationResult(False, "run", "the result file was unreadable")
    return ElevationResult(
        ok=bool(data.get("ok")),
        step=str(data.get("step", "")),
        error=str(data.get("error", "")),
        needs_full_disk_access=bool(data.get("needs_full_disk_access")),
        originals_zip=str(data.get("originals_zip", "")),
        tcc=str(data.get("tcc", "")),
        ran=True,
    )


# ── macOS ───────────────────────────────────────────────────────────────────


def build_macos_command(helper: Path | list[str], job: Path, prompt: str, digest: str) -> list[str]:
    """The ``osascript`` invocation that runs the helper against *job* as root.

    *helper* is an argv prefix — see :func:`helper_command`. A bare path is
    accepted for convenience. *digest* is :func:`job_digest` of *job*.

    *prompt* is shown by the OS dialog. It goes into an AppleScript string
    literal, so a double quote in it would end the literal early — refused
    rather than escaped, because the prompt is ours and should not contain
    one.
    """
    if '"' in prompt or "\\" in prompt:
        raise ValueError("the elevation prompt cannot contain quotes")
    argv = [str(helper)] if isinstance(helper, (str, Path)) else list(helper)
    # Quoted once for sh...
    parts = [shlex.quote(a) for a in argv]
    inner = " ".join(
        [*parts, "--patch-d2", shlex.quote(str(job)), "--job-sha256", _checked(digest)]
    )
    # ...and then for the AppleScript string it goes inside, which is read
    # first: a bare double quote or backslash would end or alter the literal.
    script = (
        f"do shell script {applescript.quoted(inner)} "
        f'with prompt "{prompt}" with administrator privileges'
    )
    return ["osascript", "-e", script]


def interpret_macos(returncode: int, stdout: str, stderr: str) -> ElevationResult:
    if returncode == 0:
        return ElevationResult(True, "done", ran=True)
    if _MAC_CANCELLED in stderr or "User canceled" in stderr:
        return USER_CANCELLED
    if _MAC_NONZERO in stderr:
        # It started, did its work, and reported failure the proper way.
        return ElevationResult(False, "run", stderr.strip(), ran=True)
    return ElevationResult(
        False, "authorise", (stderr or stdout).strip() or f"osascript exited {returncode}"
    )


# ── Windows ─────────────────────────────────────────────────────────────────


def interpret_windows(code: int) -> ElevationResult:
    """``ShellExecuteW`` returns >32 on success, and 5 when UAC is dismissed."""
    if code > _WIN_SUCCESS_FLOOR:
        return ElevationResult(True, "done", ran=True)
    if code == _WIN_ACCESS_DENIED:
        return USER_CANCELLED
    return ElevationResult(False, "authorise", f"could not start the helper (code {code})")


def windows_parameters(helper: list[str], job: Path, digest: str) -> str:
    """What follows the executable on the elevated command line.

    In development ``helper[1]`` is the entry script; frozen, there is no
    ``[1]`` and the executable is the application.
    """
    extra = " ".join(f'"{a}"' for a in helper[1:])
    return (f'{extra} --patch-d2 "{job}" --job-sha256 {_checked(digest)}').strip()


def _run_windows(helper: list[str], job: Path, digest: str) -> ElevationResult:
    import ctypes
    from ctypes import wintypes

    class SHELLEXECUTEINFOW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("fMask", ctypes.c_ulong),
            ("hwnd", wintypes.HANDLE),
            ("lpVerb", wintypes.LPCWSTR),
            ("lpFile", wintypes.LPCWSTR),
            ("lpParameters", wintypes.LPCWSTR),
            ("lpDirectory", wintypes.LPCWSTR),
            ("nShow", ctypes.c_int),
            ("hInstApp", wintypes.HINSTANCE),
            ("lpIDList", ctypes.c_void_p),
            ("lpClass", wintypes.LPCWSTR),
            ("hkeyClass", wintypes.HKEY),
            ("dwHotKey", wintypes.DWORD),
            ("hIconOrMonitor", wintypes.HANDLE),
            ("hProcess", wintypes.HANDLE),
        ]

    SEE_MASK_NOCLOSEPROCESS = 0x00000040
    SEE_MASK_NOASYNC = 0x00000100

    info = SHELLEXECUTEINFOW()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = SEE_MASK_NOCLOSEPROCESS | SEE_MASK_NOASYNC
    info.lpVerb = "runas"
    info.lpFile = helper[0]
    info.lpParameters = windows_parameters(helper, job, digest)
    info.nShow = 0  # SW_HIDE

    if not platform_modules.win_ctypes().windll.shell32.ShellExecuteExW(ctypes.byref(info)):
        return interpret_windows(platform_modules.win_ctypes().get_last_error() or 0)
    # runas is otherwise fire-and-forget: wait, or the result file will not
    # exist yet when it is read.
    if info.hProcess:
        platform_modules.win_ctypes().windll.kernel32.WaitForSingleObject(info.hProcess, 0xFFFFFFFF)
        platform_modules.win_ctypes().windll.kernel32.CloseHandle(info.hProcess)
    return ElevationResult(True, "done", ran=True)


def run_helper(
    helper: Path | list[str], job: Path, prompt: str, timeout: float = 300.0
) -> ElevationResult:
    """Run *helper* elevated against *job*. Never raises.

    Returns the elevation outcome only — whether the *work* succeeded is in
    the helper's own result file, read separately with :func:`read_result`,
    so a refused prompt and a failed patch stay distinguishable.
    """
    argv = [str(helper)] if isinstance(helper, (str, Path)) else list(helper)

    # Taken now, before anyone is asked: this is the job being authorised.
    try:
        digest = job_digest(job)
    except OSError as exc:
        return ElevationResult(False, "run", f"cannot read the job: {exc}")

    if is_elevated():
        # Already root: no prompt, just run it.
        try:
            proc = subprocess.run(
                [*argv, "--patch-d2", str(job), "--job-sha256", digest],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return ElevationResult(False, "run", str(exc))
        return (
            ElevationResult(True, "done", ran=True)
            if proc.returncode == 0
            else ElevationResult(False, "run", proc.stderr.strip() or "helper failed", ran=True)
        )

    if sys.platform == "darwin":
        try:
            proc = subprocess.run(
                build_macos_command(argv, job, prompt, digest),
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except ValueError as exc:
            return ElevationResult(False, "authorise", str(exc))
        except (OSError, subprocess.SubprocessError) as exc:
            return ElevationResult(False, "run", str(exc))
        return interpret_macos(proc.returncode, proc.stdout, proc.stderr)

    if sys.platform == "win32":
        try:
            return _run_windows(argv, job, digest)
        except Exception as exc:
            return ElevationResult(False, "authorise", str(exc))

    return ElevationResult(False, "authorise", f"no elevation method for {sys.platform}")
