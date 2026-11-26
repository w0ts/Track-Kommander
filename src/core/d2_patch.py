"""Add our controller module to Traktor's own D2 mapping, without replacing it.

The app used to ship a `D2.qml` that replaced NI's wholesale — 320 bytes in
place of their 10 KB mapping — so anyone who owns real Kontrol D2 hardware
lost the controller the moment they installed. `INSTALL.txt` even documented
it. This does the additive thing instead:

* copy our ``Api/`` module in beside NI's files, where it collides with
  nothing they ship;
* add two lines to their ``D2.qml`` — ``import "Api"`` and one
  ``ApiModule { … }`` inside the existing ``Mapping`` block.

Everything NI wrote stays, so the D2 keeps working.

Back up first — non-negotiable
------------------------------

Nothing is written until a **verified** copy of the original folder exists:
copy out → verify (file count and per-file SHA-256) → only then touch the
original. If verification fails, abort; never "patch anyway".

:func:`back_up` refuses to overwrite an existing backup for the same Traktor
version. The first copy is the pristine one, and a second run would otherwise
back up our own patched files over it — which is exactly how the only clean
copy on the development machine was lost once.

Elevation
---------

The target is root-owned, so the writing half runs elevated; see
:mod:`core.elevate`. Everything here is ordinary file work so it can be tested
without privileges, and :func:`apply` is the single operation the elevated
helper performs, so one authorisation covers the whole job.
"""

from __future__ import annotations

import errno
import hashlib
import hmac
import json
import logging
import os
import re
import shutil
import sys
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from core.atomic_write import replacing, write_text_atomic

log = logging.getLogger(__name__)

#: The helper's report, always written beside the job it was given.
RESULT_NAME = "result.json"

#: The QML element name our module is instantiated under.
MODULE_NAME = "track_kommander_api"

#: Written into Traktor's own ``CSI/D2`` folder, holding that folder exactly
#: as Native Instruments shipped it. The app's per-version copy lives in its
#: own data directory, which proves nothing to someone looking at Traktor —
#: this one sits beside the files it is the backup of, so listing it is all it
#: takes to know the originals were kept.
ORIGINALS_ZIP = "original-files.zip"

#: Bumped when the patch itself changes, so an old patch can be recognised
#: and redone from the backup rather than patched twice.
PATCH_VERSION = 1

#: Marks our lines so they can be found, recognised and removed again.
PATCH_SENTINEL = f"// track-kommander:patch v{PATCH_VERSION}"

_IMPORT_LINE = f'import "Api"   {PATCH_SENTINEL}'
_MODULE_LINE = f'  ApiModule {{ name: "{MODULE_NAME}" }}   {PATCH_SENTINEL}'

#: ``Mapping`` followed by its opening brace, which NI puts on the next line.
_MAPPING = re.compile(r"^(\s*Mapping\s*)(\{)", re.MULTILINE)
_MAPPING_NEXT_LINE = re.compile(r"^(\s*Mapping\s*)\n(\s*)(\{)", re.MULTILINE)


class BackupError(RuntimeError):
    """The backup is missing, unverifiable, or would be overwritten."""


# ── The patch itself, as a pure text transform ──────────────────────────────


def is_patched(text: str) -> bool:
    return PATCH_SENTINEL in text


def patch_qml(text: str) -> str:
    """Return *text* with our import and module added. Idempotent.

    Raises ``ValueError`` if there is no ``Mapping`` block to add to — better
    to refuse than to write a QML file Traktor cannot load.
    """
    if not text.strip():
        raise ValueError("empty QML file")
    if is_patched(text):
        return text

    match = _MAPPING_NEXT_LINE.search(text) or _MAPPING.search(text)
    if match is None:
        raise ValueError("no Mapping block found — refusing to patch")

    # The module goes immediately after the opening brace, so it does not
    # depend on anything else in the file staying where it is.
    insert_at = match.end()
    out = text[:insert_at] + "\n" + _MODULE_LINE + text[insert_at:]

    # The import goes after NI's last one, so their relative imports still
    # resolve in the order they wrote them.
    lines = out.splitlines(keepends=True)
    last_import = max(
        (i for i, line in enumerate(lines) if line.lstrip().startswith("import ")),
        default=-1,
    )
    if last_import < 0:
        raise ValueError("no import block found — refusing to patch")
    lines.insert(last_import + 1, _IMPORT_LINE + "\n")
    return "".join(lines)


def unpatch_qml(text: str) -> str:
    """Remove our lines, leaving the file exactly as NI shipped it."""
    if not is_patched(text):
        return text
    kept = [line for line in text.splitlines(keepends=True) if PATCH_SENTINEL not in line]
    return "".join(kept)


# ── Backup, verification, restore ───────────────────────────────────────────


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


#: Never Traktor's, so never part of a copy, a comparison or an archive of
#: Traktor's files: ``.DS_Store`` is Finder's, and the originals archive is
#: ours. Including the archive would also nest each run's copy inside the
#: next one.
_NOT_TRAKTORS = {".DS_Store", ORIGINALS_ZIP}


def _files_under(root: Path) -> dict[str, Path]:
    """Relative path → file, for the real files under *root*.

    Links are left out. Traktor ships none, and one found in a folder the
    user can write to is a way to make the elevated helper read from, or
    write to, somewhere it was never asked to go.
    """
    return {
        str(p.relative_to(root)): p
        for p in sorted(root.rglob("*"))
        if not p.is_symlink() and p.is_file() and p.name not in _NOT_TRAKTORS
    }


def _write_new_file(path: Path, text: str) -> None:
    """Create *path* with *text*, never through a link.

    Whatever has that name is removed first — removing a link removes the
    link, not what it points at — and the file is then created on the
    condition that nothing is there. If a link reappears in between, the
    create fails instead of following it.
    """
    path.unlink(missing_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o644)
    with os.fdopen(fd, "w", encoding="utf-8") as out:
        out.write(text)


def backup_dir(root: Path, version: str) -> Path:
    """Where the pristine copy for one Traktor version lives."""
    return Path(root) / version / "D2"


def has_originals_zip(csi_d2: Path) -> bool:
    """Is the originals archive already beside Traktor's files?"""
    return (Path(csi_d2) / ORIGINALS_ZIP).is_file()


def zip_entries(zip_path: Path) -> dict[str, str]:
    """``{name: sha256}`` for every entry — what "inspect it" needs.

    Hashes rather than the sizes ``unzip -l`` prints, because a truncated
    entry of the right length is the failure worth catching.
    """
    entries: dict[str, str] = {}
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            digest = hashlib.sha256()
            with zf.open(info) as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    digest.update(chunk)
            entries[info.filename] = digest.hexdigest()
    return entries


def write_originals_zip(csi_d2: Path) -> Path:
    """Archive Traktor's ``CSI/D2`` into ``original-files.zip`` inside it.

    Call this **before** anything of ours goes in: a zip labelled "original
    files" that contains our module is worse than no zip at all.

    Never overwrites an existing one. The first archive holds what NI
    shipped; a second run would replace it with the patched files, which is
    precisely how the only clean copy on this machine was lost once.

    Verified before it is moved into place — written to a temporary name,
    read back, and every entry hash-compared against the file it came from.
    A backup that was not checked is not a backup.
    """
    csi_d2 = Path(csi_d2)
    target = csi_d2 / ORIGINALS_ZIP
    if target.exists():
        raise BackupError(
            f"{target} already exists — refusing to overwrite it, because "
            "the first archive is the one holding Traktor's originals"
        )

    # _files_under leaves our own archive out, or each run would nest the
    # last one inside it.
    originals = _files_under(csi_d2)
    if not originals:
        raise BackupError(f"nothing to archive in {csi_d2}")

    # Moved into place only once it verifies; a failure leaves no partial zip.
    with replacing(target) as tmp:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
            for rel, path in originals.items():
                zf.write(path, arcname=rel.replace("\\", "/"))

        written = zip_entries(tmp)
        expected = {rel.replace("\\", "/"): _hash(path) for rel, path in originals.items()}
        if written != expected:
            missing = sorted(set(expected) - set(written))
            wrong = sorted(k for k in written.keys() & expected.keys() if written[k] != expected[k])
            raise BackupError(
                f"the archive did not verify: {', '.join(missing + wrong) or 'entry list differs'}"
            )
    log.info("archived %d original file(s) to %s", len(originals), target)
    return target


def has_backup(root: Path, version: str) -> bool:
    """Is there a backup for *version* that could actually be restored from?

    The single definition of "a backup is there", because three subtly
    different ones is how the backup-first rule gets skipped. A shallow
    ``any(dir.iterdir())`` counts a stray ``.DS_Store`` — which Finder writes
    just by looking at the folder, and which a backup that died between
    ``mkdir`` and the first copy leaves behind — and the caller then believes
    a pristine copy exists and patches Traktor without one.
    """
    return bool(_files_under(backup_dir(root, version)))


def backed_up_versions(root: Path) -> tuple[str, ...]:
    """Traktor versions with a backup that could actually be restored from.

    Most recently backed up first — which is the Traktor this machine was
    using before the current one, and so the answer to "updated from what?".
    Ordered by mtime rather than by parsing the version out of the label: the
    label is a display string, and the order backups were *made* in is a fact
    already on disk.

    An empty directory does not count. A half-finished or hand-cleared backup
    holds nothing, and treating it as one would offer a restore that fails.
    """
    root = Path(root)
    try:
        entries = sorted(root.iterdir())
    except OSError:
        return ()  # never installed, or root is gone

    found: list[tuple[float, str]] = []
    for entry in entries:
        try:
            if not has_backup(root, entry.name):
                continue
            found.append((entry.stat().st_mtime, entry.name))
        except OSError:
            log.debug("could not stat backup %s", entry, exc_info=True)
    # Newest first; name breaks a tie so the order is at least deterministic
    # on filesystems with coarse timestamps.
    found.sort(key=lambda item: (-item[0], item[1]))
    return tuple(name for _mtime, name in found)


def back_up(source: Path, root: Path, version: str) -> Path:
    """Copy *source* to the backup for *version* and verify it.

    Refuses if a backup for that version already exists: the first copy is the
    pristine one. Returns the directory it wrote.
    """
    source = Path(source)
    if not source.is_dir():
        raise BackupError(f"nothing to back up at {source}")

    dest = backup_dir(root, version)
    if has_backup(root, version):
        raise BackupError(
            f"a backup for {version} already exists at {dest} — refusing to "
            "overwrite it, because the first copy is the pristine one"
        )

    # The backup folder is the user's, and this may be running as root. A
    # link in the way would send the copy wherever it points.
    for folder in (dest.parent, dest):
        if folder.is_symlink():
            raise BackupError(f"{folder} is a link — refusing to write a backup through it")

    dest.mkdir(parents=True, exist_ok=True)
    for rel, path in _files_under(source).items():
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_symlink() or target.exists():
            raise BackupError(
                f"{target} is already there, as a link or a leftover — refusing to write over it"
            )
        shutil.copy2(path, target)

    bad = verify_copy(source, dest)
    if bad:
        raise BackupError(f"backup did not verify: {', '.join(bad)}")

    manifest = {
        "version": version,
        "source": str(source),
        "created": datetime.now(UTC).isoformat(timespec="seconds"),
        "patch_version": PATCH_VERSION,
        "files": {rel: _hash(p) for rel, p in _files_under(dest).items()},
    }
    _write_new_file(dest.parent / MANIFEST_NAME, json.dumps(manifest, indent=1))
    log.info("backed up %s to %s (%d files)", source, dest, len(manifest["files"]))
    return dest


#: Beside each backup: what was copied, and the hash of every file.
MANIFEST_NAME = "manifest.json"


def backup_problems(root: Path, version: str) -> list[str]:
    """What is wrong with the backup for *version*; empty if it is sound.

    Checked against the hashes recorded when it was made. A backup from
    before there was a manifest has nothing to be checked against, so it is
    only required to hold a ``D2.qml`` that can be read.
    """
    source = backup_dir(root, version)
    held = _files_under(source)
    try:
        recorded = json.loads((source.parent / MANIFEST_NAME).read_text())["files"]
    except (OSError, ValueError, KeyError, TypeError):
        recorded = None
    if not isinstance(recorded, dict):
        qml = held.get("D2.qml")
        if qml is None or not qml.read_bytes().strip():
            return ["D2.qml: missing or empty"]
        return []

    problems = [f"{rel}: missing" for rel in sorted(set(recorded) - set(held))]
    problems += [
        f"{rel}: not what was backed up"
        for rel, path in sorted(held.items())
        if rel in recorded and _hash(path) != recorded[rel]
    ]
    return problems


def verify_copy(source: Path, copy: Path) -> list[str]:
    """Relative paths that differ between *source* and *copy*, or are missing.

    Compared by content hash, not size — a truncated copy of the right length
    is exactly the failure this has to catch.
    """
    originals = _files_under(Path(source))
    copies = _files_under(Path(copy))
    bad: list[str] = []
    for rel, path in originals.items():
        other = copies.get(rel)
        if other is None or _hash(other) != _hash(path):
            bad.append(rel)
    return sorted(bad)


def restore(target: Path, root: Path, version: str) -> None:
    """Put the pristine files back and remove ours.

    A first-class action, not just an error path: it is what "undo this" and
    "uninstall" both do.

    The backup is checked before anything is removed. Traktor's folder is
    emptied first and filled second, so a backup that turns out damaged
    half-way would leave Traktor with no D2 mapping at all.
    """
    target = Path(target)
    source = backup_dir(root, version)
    if not has_backup(root, version):
        raise BackupError(f"no backup for {version} at {source}")
    problems = backup_problems(root, version)
    if problems:
        raise BackupError(
            f"the backup at {source} is damaged, so nothing was changed: " + ", ".join(problems)
        )

    for path in sorted(target.rglob("*"), reverse=True):
        if path.is_file():
            path.unlink()
        elif path.is_dir():
            path.rmdir()
    for rel, path in _files_under(source).items():
        dest = target / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest)
    log.info("restored %s from %s", target, source)


# ── The whole job, as one operation ─────────────────────────────────────────

#: What macOS returns when it blocks a write into another application's
#: bundle. It is EPERM rather than EACCES, and it is returned **to root** —
#: the directory is drwxrwxr-x root:wheel, so no mode-based denial is
#: possible. That distinction is the whole signal.
BUNDLE_WRITE_ERRNO = errno.EPERM

BUNDLE_WRITE_HELP = (
    "macOS blocked the write. Since Ventura, changing another application's "
    "bundle is refused even to an administrator, which is why your password "
    "was not enough — the write is done by a helper running as root, and "
    "Full Disk Access is the grant that covers it.\n\n"
    "Add the app in System Settings → Privacy & Security → Full Disk Access, "
    "relaunch it, then try again."
)


@dataclass
class PatchResult:
    """What :func:`apply` did, in a form the UI can explain."""

    ok: bool
    step: str
    error: str = ""
    backed_up: str = ""
    originals_zip: str = ""
    """Where Traktor's own files were archived, beside them. The one piece of
    evidence a user can check without leaving the folder they are worried
    about."""
    files_written: int = 0
    needs_full_disk_access: bool = False
    """macOS refused the bundle write. Worth its own flag: the user can fix
    it, but only if told which grant it is."""
    tcc: str = ""
    """What the *elevated* process sees of its own grants. The unprivileged
    side can only report its own view, and the two turned out to disagree —
    granted in the app, refused as root. Carrying the helper's own reading
    back is the only way to tell "you haven't granted it" from "you granted
    it and macOS ignored it here"."""

    def as_json(self) -> str:
        return json.dumps(
            {
                "ok": self.ok,
                "step": self.step,
                "error": self.error,
                "backed_up": self.backed_up,
                "files_written": self.files_written,
                "originals_zip": self.originals_zip,
                "needs_full_disk_access": self.needs_full_disk_access,
                "tcc": self.tcc,
            }
        )


def apply(csi_d2: Path, api_source: Path, backup_root: Path, version: str) -> PatchResult:
    """Back up, copy our ``Api/`` in, and patch ``D2.qml``. Idempotent.

    This is the single operation the elevated helper runs, so one
    authorisation covers the whole job — a series of individual elevated calls
    would prompt repeatedly and could leave a half-patched folder.
    """
    csi_d2, api_source = Path(csi_d2), Path(api_source)
    qml = csi_d2 / "D2.qml"

    if not qml.is_file():
        return PatchResult(False, "locate", f"no D2.qml at {qml} — is this a Traktor install?")

    step = "back up"
    try:
        if not has_backup(backup_root, version):
            dest = back_up(csi_d2, backup_root, version)
        else:
            dest = backup_dir(backup_root, version)
            log.info("backup for %s already exists at %s", version, dest)

        # Beside Traktor's files, and before a single one of ours goes in:
        # this archive is only worth anything if it holds the folder as NI
        # shipped it. An existing one is left alone — it is the older, and
        # therefore the pristine, copy.
        step = "archive the originals"
        zip_path = csi_d2 / ORIGINALS_ZIP
        if not has_originals_zip(csi_d2):
            zip_path = write_originals_zip(csi_d2)

        step = "copy the Api module"
        written = 0
        target_api = csi_d2 / "Api"
        for path in sorted(api_source.rglob("*")):
            if path.is_symlink() or not path.is_file() or path.name == ".DS_Store":
                continue
            out = target_api / path.relative_to(api_source)
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, out)
            written += 1

        step = "patch D2.qml"
        original = qml.read_text(encoding="utf-8")
        patched = patch_qml(original)
        if patched != original:
            _write_atomic(qml, patched)

        step = "verify"
        if not is_patched(qml.read_text(encoding="utf-8")):
            return PatchResult(False, step, "the patch did not survive the write")
    except OSError as exc:
        if exc.errno == BUNDLE_WRITE_ERRNO:
            return PatchResult(
                False, step, f"{BUNDLE_WRITE_HELP}\n\n({exc})", needs_full_disk_access=True
            )
        return PatchResult(False, step, str(exc))
    except (ValueError, BackupError) as exc:
        return PatchResult(False, step, str(exc))

    return PatchResult(
        True, "done", backed_up=str(dest), originals_zip=str(zip_path), files_written=written
    )


def is_traktor_d2(csi_d2: Path) -> bool:
    """Is *csi_d2* a ``CSI/D2`` folder inside a Traktor installation?

    A wrong or derived path handed to something running as root is the
    failure worth guarding against, so this is about the shape of the whole
    path: it has to *end* in ``CSI/D2``, under something named after Traktor.
    Having both words somewhere in it is not enough — ``~/traktor/csi``
    passed that, and is a folder anyone can make.

    Judged on the path as given. Pass a resolved one, and use that same one
    afterwards: a link checked here and followed later can point somewhere
    else by then.
    """
    parts = [p.lower() for p in Path(csi_d2).parts]
    return parts[-2:] == ["csi", "d2"] and any("traktor" in p for p in parts[:-2])


def _is_label(version: str) -> bool:
    """A Traktor version as a folder name — one path component, no more."""
    return (
        bool(version.strip())
        and version not in (".", "..")
        and not any(c in version for c in "/\\\x00")
    )


#: Qt's compiled-QML cache for Traktor, which is what made a correct patch
#: invisible at runtime. Under the *user's* Library, so clearing it needs no
#: privileges and belongs on the unprivileged side of the job.
_QML_CACHE = Path.home() / "Library/Caches/Traktor Pro 4/qmlcache"


def clear_qml_cache() -> int:
    """Drop Traktor's compiled-QML cache so the patched mapping is recompiled.

    Belt and braces. :func:`_write_atomic` now moves the mtime forward, which
    is what Qt actually checks, but the exact validation rule is Qt's to
    change and the cost of being wrong is the failure this whole function is
    named after: a patch that is present on disk and never runs. Deleting a
    cache is free — Qt rebuilds it on the next launch.

    Returns how many files were removed; 0 when there was no cache, which is
    not an error. Never raises: this runs after a job that already succeeded.
    """
    removed = 0
    try:
        if not _QML_CACHE.is_dir():
            return 0
        for entry in _QML_CACHE.iterdir():
            if entry.is_file():
                entry.unlink()
                removed += 1
    except OSError:
        log.exception("could not clear the QML cache at %s", _QML_CACHE)
    else:
        log.info("cleared %d file(s) from %s", removed, _QML_CACHE)
    return removed


def _elevated_tcc_view() -> str:
    """One line describing who this elevated process is, to macOS.

    The grant is read in the GUI process, the write happens in this one, and
    a permission that is present in the first and absent in the second is
    invisible from either side alone. Never raises and never blocks the job:
    it is a note for the log, not a check.
    """
    try:
        from core.permissions import full_disk_access_granted

        fda = full_disk_access_granted()
    except Exception as exc:
        fda = f"unreadable ({exc})"
    # geteuid is POSIX-only; the helper runs on Windows too.
    who = os.geteuid() if sys.platform != "win32" else "n/a"
    return f"euid={who} full-disk-access={fda} exe={sys.executable}"


_SHA256_HEX = re.compile(r"[0-9a-f]{64}")


def helper_main(argv: list[str]) -> int:
    """The elevated helper's command line: ``--patch-d2 <job> --job-sha256
    <digest>``. Returns the process exit code; 2 for a command line that is
    not all there."""
    try:
        job = argv[argv.index("--patch-d2") + 1]
        digest = argv[argv.index("--job-sha256") + 1]
    except (ValueError, IndexError):
        log.error("helper: expected --patch-d2 <job> --job-sha256 <digest>")
        return 2
    return run_job(Path(job), digest)


def run_job(job_path: Path, expected_sha256: str) -> int:
    """Execute a job file. The entry point of the **elevated** helper.

    Returns a process exit code and always writes the result file, so the
    unprivileged side can tell *which* stage failed rather than seeing a bare
    non-zero exit. Never raises: this runs as root, and an unhandled
    traceback there tells the user nothing.

    **The job is only run if it is the one that was authorised.** It sits in
    a folder the user owns, written before the password prompt and read
    after it, so anything running as that user has the length of the prompt
    to rewrite it. *expected_sha256* comes from the command line, which was
    fixed when the user was asked; the file is read once, and those same
    bytes are both hashed and parsed.

    **The job does not get to say where the result goes.** It is written
    beside the job, under a fixed name, and never through a link.
    """
    job_path = Path(job_path)
    result_path = job_path.parent / RESULT_NAME
    seen = _elevated_tcc_view()

    def finish(result: PatchResult) -> int:
        result.tcc = seen
        try:
            _write_new_file(result_path, result.as_json())
        except OSError:
            log.exception("could not write the result file")
        return 0 if result.ok else 1

    def refuse(step: str, why: str) -> int:
        log.error("helper: %s: %s", step, why)
        return finish(PatchResult(False, step, why))

    try:
        raw = job_path.read_bytes()
    except OSError as exc:
        log.error("cannot read job %s: %s", job_path, exc)
        return 2

    expected = str(expected_sha256).strip().lower()
    found = hashlib.sha256(raw).hexdigest()
    if not (_SHA256_HEX.fullmatch(expected) and hmac.compare_digest(found, expected)):
        refuse(
            "read the job",
            "the job file changed after it was authorised, or came "
            "without its digest — nothing was done",
        )
        return 2

    try:
        job = json.loads(raw)
    except ValueError as exc:
        refuse("read the job", f"the job file is not JSON: {exc}")
        return 2
    if not isinstance(job, dict):
        refuse("read the job", "the job file is not a job")
        return 2

    action = job.get("action")
    if action not in ("patch-d2", "restore-d2"):
        refuse("read the job", f"unknown action {action!r}")
        return 2

    # Resolved once, checked, and that same path used from here on.
    csi_d2 = Path(str(job.get("csi_d2", ""))).resolve()
    if not is_traktor_d2(csi_d2):
        return refuse(
            "locate", f"{csi_d2} is not a CSI/D2 folder inside a Traktor installation — refusing"
        )

    version = str(job.get("version", ""))
    if not _is_label(version):
        return refuse(
            "read the job",
            f"the version {version!r} is not a plain name — refusing to use it as a folder",
        )

    backup_root = Path(str(job.get("backup_root", ""))).resolve()

    if action == "restore-d2":
        try:
            restore(csi_d2, backup_root, version)
        except (OSError, BackupError) as exc:
            return finish(PatchResult(False, "restore", str(exc)))
        return finish(PatchResult(True, "done"))

    api = Path(str(job.get("api", ""))).resolve()
    return finish(apply(csi_d2, api, backup_root, version))


def _write_atomic(path: Path, text: str) -> None:
    """Write via a temp file on the same volume, then move into place.

    An interrupted write must not leave Traktor with a truncated QML file.

    The file's **mode** is carried over — Traktor's QML is ``root:wheel``
    ``775`` and must stay that way — but deliberately *not* its timestamps.
    This used to call :func:`shutil.copystat`, which restores the original
    mtime, and that quietly defeated the whole patch: Qt keeps a compiled QML
    cache (``~/Library/Caches/Traktor Pro 4/qmlcache``) and validates it
    against the source mtime. An unchanged mtime means the stale, pre-patch
    ``.qmlc`` stays valid, so Traktor went on running the *old* mapping while
    the patched file sat on disk. No error, no log line, nothing sent — it
    looked like a permissions bug for an afternoon.
    """
    # Text mode used to turn "\n" into the platform's line ending; the bytes
    # writer does not, so it is done here to leave the file as it was written.
    write_text_atomic(path, text.replace("\n", os.linesep), keep_mode=True)
