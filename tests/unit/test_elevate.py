"""Running one privileged step, on macOS and Windows.

Patching Traktor's QML needs root/Administrator. The rules this encodes:

* **One elevated invocation for the whole job.** A series of individual
  elevated calls prompts repeatedly and can leave a half-patched folder, so
  the job is written to a file and a single helper run does all of it.
* **Declining is not a failure.** Cancelling the macOS dialog gives error
  ``-128`` and declining UAC gives ``5``; both mean "the user said no", and
  the app stays usable with a clear "not patched" status.
* **Arguments are never interpolated into the shell string.** On macOS it is
  parsed twice — by AppleScript, then by ``sh`` — so a path with a space or a
  quote in it would break or, worse, execute. The job is passed as a file
  path and quoted once, properly.

Nothing here actually elevates; the command construction and result handling
are what can go wrong silently, so those are what is tested.
"""

from __future__ import annotations

import json
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from core.elevate import (
    USER_CANCELLED,
    ElevationResult,
    _bundle_name,
    build_macos_command,
    helper_command,
    interpret_macos,
    interpret_windows,
    read_result,
    responsible_app_name,
    write_job,
)

#: Shaped like a job's digest; these tests are about the rest of the command.
_DIGEST = "a" * 64

#: For the tests that read paths back out of the ``osascript`` command. It is
#: built from the paths of the machine it runs on and quoted for ``sh``; a
#: Windows path has backslashes, which that quoting doubles, and there is no
#: ``osascript`` or ``sh`` there to hand it to. Windows elevates through
#: ``ShellExecuteW``, which has its own tests.
macos_only = pytest.mark.skipif(
    sys.platform != "darwin", reason="an osascript command line, built from and for macOS paths"
)

# ── The job file ────────────────────────────────────────────────────────────


def test_job_round_trips(tmp_path) -> None:
    job = write_job(tmp_path, {"action": "patch-d2", "csi": "/a/b"})
    assert json.loads(job.read_text())["action"] == "patch-d2"


def test_job_and_result_are_separate_files(tmp_path) -> None:
    job = write_job(tmp_path, {"action": "patch-d2"})
    assert job.name != "result.json"


def test_reading_a_missing_result_is_a_failure_not_a_crash(tmp_path) -> None:
    got = read_result(tmp_path / "result.json")
    assert not got.ok
    assert "no result" in got.error.lower()


def test_reading_a_corrupt_result_is_a_failure(tmp_path) -> None:
    path = tmp_path / "result.json"
    path.write_text("{not json")
    assert not read_result(path).ok


def test_a_good_result_is_read_back(tmp_path) -> None:
    path = tmp_path / "result.json"
    path.write_text(json.dumps({"ok": True, "step": "done", "error": ""}))
    got = read_result(path)
    assert got.ok and got.step == "done"


# ── macOS ───────────────────────────────────────────────────────────────────


@macos_only
def test_macos_command_passes_the_job_as_a_path(tmp_path) -> None:
    job = tmp_path / "job.json"
    cmd = build_macos_command(Path("/usr/bin/true"), job, "Patch Traktor", _DIGEST)
    joined = " ".join(cmd)
    assert "with administrator privileges" in joined
    assert str(job) in joined


@macos_only
def test_macos_command_quotes_a_path_with_spaces(tmp_path) -> None:
    """Double-parsed by AppleScript then sh — a bare space would split it."""
    job = tmp_path / "a folder with spaces" / "job.json"
    job.parent.mkdir()
    job.write_text("{}")
    cmd = build_macos_command(Path("/usr/bin/true"), job, "Patch Traktor", _DIGEST)
    script = cmd[-1]
    # The path must survive as one shell word: it is quoted, not bare.
    assert f"'{job}'" in script or f'\\"{job}\\"' in script


def test_macos_command_rejects_a_quote_in_the_prompt() -> None:
    """The prompt goes into an AppleScript string literal."""
    with pytest.raises(ValueError):
        build_macos_command(Path("/usr/bin/true"), Path("/tmp/j.json"), 'Patch "Traktor"', _DIGEST)


def test_macos_cancel_is_reported_as_declined() -> None:
    got = interpret_macos(1, "", "User canceled. (-128)")
    assert got is USER_CANCELLED or got.cancelled
    assert not got.ok


def test_macos_other_failures_are_not_declines() -> None:
    got = interpret_macos(1, "", "something else went wrong")
    assert not got.ok and not got.cancelled


def test_macos_success() -> None:
    assert interpret_macos(0, "", "").ok


# ── Windows ─────────────────────────────────────────────────────────────────


def test_windows_access_denied_is_a_decline() -> None:
    """ShellExecuteW returns 5 when the user dismisses UAC."""
    got = interpret_windows(5)
    assert got.cancelled and not got.ok


def test_windows_success_codes_are_above_32() -> None:
    """ShellExecuteW returns >32 on success — a quirk worth encoding once."""
    assert interpret_windows(42).ok
    assert not interpret_windows(31).ok


def test_windows_other_errors_are_failures_not_declines() -> None:
    got = interpret_windows(2)
    assert not got.ok and not got.cancelled
    assert got.error


# ── The shape callers rely on ───────────────────────────────────────────────


def test_a_declined_elevation_is_distinguishable() -> None:
    """The UI falls back to manual instructions on a decline, and shows an
    error on anything else — so they must not look the same."""
    declined = interpret_windows(5)
    failed = interpret_windows(2)
    assert declined.cancelled is True
    assert failed.cancelled is False


def test_result_carries_a_step_for_the_ui() -> None:
    got = ElevationResult(ok=False, step="patch D2.qml", error="boom")
    assert "patch D2.qml" in got.describe()
    assert "boom" in got.describe()


# ── The helper has to be something sh can actually execute ─────────────────
# Real failure: the command was built from the entry point alone, which in
# development is a non-executable `.py`. `do shell script` hands it to sh:
#     /bin/sh: .../TrackKommander.py: Permission denied (126)


def test_helper_command_runs_through_the_interpreter_in_development() -> None:
    argv = helper_command()
    assert argv, "must not be empty"
    if not getattr(sys, "frozen", False):
        assert argv[0] == sys.executable, (
            "a .py entry point is not executable; sh needs the interpreter"
        )
        assert len(argv) >= 2 and argv[1].endswith(".py")


# Real failure: the entry point was read from sys.argv[0], which is only the
# application when the application is what was launched. Under pytest it named
# pytest, and a relative one resolved against whatever the working directory
# had become by the time the user clicked Install.


def test_the_helper_is_the_application_whatever_launched_it(monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["somewhere/else/launcher"])

    entry = Path(helper_command()[1])

    assert entry.name == "TrackKommander.py"
    assert entry.is_absolute() and entry.is_file()


@macos_only
def test_the_built_command_is_executable_by_sh(tmp_path) -> None:
    """Build the real command and check sh can start it — short of the
    elevation, which is what the test cannot do."""

    job = tmp_path / "job.json"
    job.write_text("{}")
    cmd = build_macos_command(helper_command(), job, "Test", _DIGEST)
    script = cmd[-1]
    inner = script[script.index('"') + 1 : script.rindex('" with prompt')]

    # Same words sh would get, but run directly and harmlessly.
    argv = shlex.split(inner)
    assert argv[0] == sys.executable
    proc = subprocess.run(
        [argv[0], "-c", "print('ok')"], capture_output=True, text=True, check=False
    )
    assert proc.returncode == 0 and "ok" in proc.stdout


def test_an_argv_prefix_is_quoted_element_by_element(tmp_path) -> None:

    job = tmp_path / "job.json"
    cmd = build_macos_command(["/usr/bin/python3", "/a path/app.py"], job, "T", _DIGEST)
    assert "'/a path/app.py'" in cmd[-1]


@macos_only
def test_a_bare_path_still_works(tmp_path) -> None:
    """Convenience: callers that have one executable can pass it directly."""

    cmd = build_macos_command(Path("/usr/bin/true"), tmp_path / "j.json", "T", _DIGEST)
    assert "/usr/bin/true" in cmd[-1]


# ── "It ran and failed" is not "it would not start" ────────────────────────
# osascript returns non-zero for both, and they were reported the same, so a
# helper that failed at a known step surfaced as "could not start the
# helper" — throwing away the result file that named the step:
#     authorise: 0:320: execution error: The command exited with a
#     non-zero status. (1)


def test_a_helper_that_ran_and_failed_says_so() -> None:
    got = interpret_macos(
        1, "", "0:320: execution error: The command exited with a non-zero status. (1)"
    )
    assert not got.ok
    assert not got.cancelled
    assert got.ran, "the helper started; its result file is the real answer"


def test_a_cancelled_prompt_did_not_run() -> None:
    got = interpret_macos(1, "", "User canceled. (-128)")
    assert got.cancelled and not got.ran


def test_a_helper_that_could_not_start_did_not_run() -> None:
    got = interpret_macos(1, "", "some other AppleScript problem")
    assert not got.ran


def test_success_counts_as_having_run() -> None:
    assert interpret_macos(0, "", "").ran


def test_windows_success_counts_as_having_run() -> None:
    assert interpret_windows(42).ran
    assert not interpret_windows(5).ran


# ── Who macOS actually attributes the permission request to ───────────────
# Real confusion: the user granted App Management to the built app bundle,
# but the running process was python launched from IntelliJ, so the grant
# never applied. The dialog told them to switch it on for "Track Kommander",
# which was not even in the list. Naming the responsible process is the
# difference between an actionable message and a wrong one.


def test_responsible_app_finds_an_enclosing_bundle() -> None:
    assert _bundle_name("/Applications/IntelliJ IDEA.app/Contents/MacOS/idea") == "IntelliJ IDEA"
    assert (
        _bundle_name("/Applications/Track Kommander.app/Contents/MacOS/Track Kommander")
        == "Track Kommander"
    )


def test_a_plain_executable_has_no_bundle() -> None:
    assert _bundle_name("/usr/bin/python3") is None
    assert _bundle_name("/Users/x/.venv/bin/python3.13") is None
    assert _bundle_name("") is None


def test_responsible_app_returns_a_name_or_none() -> None:
    """Whatever this process is really running under; must not raise."""
    got = responsible_app_name()
    assert got is None or (isinstance(got, str) and got)
