"""Traktor NML helpers — find collection, parse paths, find history dir."""

from __future__ import annotations

import os
import sys
import xml.etree.ElementTree as ET
from collections.abc import Callable
from pathlib import Path

from core import traktor_process
from core.collection_write import write_collection
from core.constants import AUDIO_EXTS
from core.cue_points import Cue, TrackCues
from core.ni_paths import ni_documents_root

# Prefix used on every hotcue name written by Track Kommander.
# Presence of this prefix is how we distinguish our cues from user-created ones.
_TK_PREFIX = "tk: "

#: AppKit's NSApplicationActivateIgnoringOtherApps (1 << 1). The pyobjc stubs
#: leave the deprecated constant out, so it is spelled here.
_ACTIVATE_IGNORING_OTHER_APPS = 1 << 1


def is_traktor_running() -> bool:
    """Return True if a Traktor process is currently running.

    macOS ``pgrep``, Windows ``tasklist``, matched by the one rule in
    :mod:`core.traktor_process`. False on any error, so a missing tool
    can't block the user.
    """
    return traktor_process.running()


def _traktor_app_candidates() -> list[Path]:
    """Installed-Traktor launch targets for the current OS, newest first.

    * macOS: the ``.app`` bundle, either under ``/Applications/Native
      Instruments/Traktor Pro N/`` (the stock installer layout) or directly
      in ``/Applications``.
    * Windows: any ``Traktor*.exe`` inside ``<ProgramFiles>\\Native
      Instruments\\Traktor Pro N`` — globbed rather than hardcoded so a
      renamed executable in a future point release still matches.
    """
    if sys.platform == "darwin":
        return [
            Path("/Applications/Native Instruments/Traktor Pro 4/Traktor Pro 4.app"),
            Path("/Applications/Native Instruments/Traktor Pro 3/Traktor Pro 3.app"),
            Path("/Applications/Traktor Pro 4.app"),
            Path("/Applications/Traktor Pro 3.app"),
        ]
    if sys.platform == "win32":
        roots: list[Path] = []
        for env in ("ProgramW6432", "ProgramFiles"):
            v = os.environ.get(env)
            if v and Path(v) not in roots:
                roots.append(Path(v))
        if not roots:
            roots.append(Path(r"C:\Program Files"))
        return [
            exe
            for root in roots
            for ver in ("Traktor Pro 4", "Traktor Pro 3")
            for exe in sorted((root / "Native Instruments" / ver).glob("Traktor*.exe"))
        ]
    return []


def find_traktor_app() -> Path | None:
    """Return the Traktor app/exe to work with, or None if not found.

    The user's explicit choice (Settings → Traktor installation) wins when it
    still exists; otherwise this falls back to the newest of the known install
    locations. See :mod:`core.traktor_install` for why the choice exists — on a
    machine with several Traktors, every subsystem picking its own "first
    match" is how they end up disagreeing.
    """
    chosen = _selected_app_path()
    if chosen is not None:
        return chosen
    for candidate in _traktor_app_candidates():
        if candidate.exists():
            return candidate
    return None


def _selected_app_path() -> Path | None:
    """The stored Traktor choice, if it is set and still on disk.

    Reads the setting directly rather than running a full discovery: this is
    called from hot paths, and an unset choice must cost nothing.
    """
    try:
        from PySide6.QtCore import QSettings

        from core.traktor_install import SETTINGS_KEY

        stored = QSettings().value(SETTINGS_KEY, "", type=str)
    except Exception:
        return None
    if not isinstance(stored, str) or not stored:
        return None
    path = Path(stored)
    return path if path.exists() else None


def launch_traktor() -> bool:
    """Start Traktor without stealing focus from Kommander.

    * macOS: ``open -g`` launches the app **in the background**, so
      Kommander's window stays on top and keeps focus.
    * Windows: plain ``Popen`` of the exe (with its install dir as cwd);
      Windows may still foreground Traktor briefly — the caller re-raises
      Kommander once the snap-over-library succeeds.

    Returns True when a launch was started, False when Traktor isn't
    installed or the spawn failed.
    """
    import subprocess

    app = find_traktor_app()
    if app is None:
        return False
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", "-g", str(app)])
        else:
            subprocess.Popen([str(app)], cwd=str(app.parent))
        return True
    except OSError:
        return False


#: How long a graceful quit is given before the caller gives up on it.
#: Traktor writes its collection on the way out, so this is generous.
QUIT_TIMEOUT_S = 30.0


def _traktor_images_windows() -> list[str]:
    """Running Traktor executable names, spelled as ``tasklist`` spells them.

    Read rather than guessed: the image is ``Traktor Pro 4.exe`` on one
    machine and something else after a point release renames it, and
    ``taskkill`` needs the name it actually has.
    """
    return traktor_process.windows_images()


def _ask_traktor_to_quit() -> bool:
    """Send Traktor a *polite* quit. True if a request went out.

    Never ``kill``/``/F``: Traktor writes its collection when it quits, so a
    forced exit costs the session's edits — exactly what someone patching a
    QML file did not sign up for.
    """
    import subprocess

    if sys.platform == "win32":
        # No /F: taskkill then asks the window to close, as clicking the X
        # would.
        asked = False
        for image in _traktor_images_windows():
            try:
                out = subprocess.run(
                    ["taskkill", "/IM", image],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                # The close request is asynchronous, so this exit code is
                # only about whether it was delivered — whether Traktor
                # actually went is settled by polling, not by taskkill.
                if out.returncode:
                    _log().debug(
                        "taskkill %s: exit %d (%s)", image, out.returncode, out.stderr.strip()
                    )
                asked = True
            except (OSError, subprocess.SubprocessError):
                _log().debug("taskkill %s failed", image, exc_info=True)
        return asked

    if sys.platform != "darwin":
        return False

    try:
        apps = traktor_process.running_apps()
        for app in apps:
            app.terminate()  # -terminate is the polite one
        if apps:
            return True
    except Exception:
        _log().debug("AppKit quit failed", exc_info=True)

    app = find_traktor_app()
    name = app.stem if app is not None else ""
    # The name goes into an AppleScript string literal; refused rather than
    # escaped, because a real Traktor is not called that.
    if not name or '"' in name or "\\" in name:
        return False
    try:
        subprocess.run(
            ["osascript", "-e", f'tell application "{name}" to quit'],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        _log().debug("osascript quit failed", exc_info=True)
        return False
    return True


def _wait_until_gone(
    timeout_s: float,
    *,
    poll_s: float = 0.5,
    running: Callable[[], bool] | None = None,
    sleep: Callable[[float], None] | None = None,
    now: Callable[[], float] | None = None,
) -> bool:
    """Poll until Traktor is gone, or *timeout_s* passes. True if it went."""
    import time

    running = running or is_traktor_running
    sleep = sleep or time.sleep
    now = now or time.monotonic

    deadline = now() + timeout_s
    while True:
        if not running():
            return True
        left = deadline - now()
        if left <= 0:
            return False
        sleep(min(poll_s, left))


def quit_traktor(
    timeout_s: float = QUIT_TIMEOUT_S, *, sleep: Callable[[float], None] | None = None
) -> bool:
    """Ask Traktor to quit and wait until it has. True once it is gone.

    Traktor reads the D2 mapping once, at startup, so it has to be closed
    while those files change — and "quit Traktor first, then come back" is a
    wall across the middle of a two-click job. The app offers to do it and
    starts Traktor again afterwards, on both platforms.

    False means Traktor is **still running** — it may be holding a modal, or
    asking about unsaved changes. The caller must then leave Traktor's files
    alone rather than push harder: nothing here escalates to a force-kill.

    *sleep* replaces the wait between polls. A GUI caller passes one that
    keeps its event loop turning, because this blocks for as long as Traktor
    takes to write its collection and a frozen window is indistinguishable
    from a crashed one.
    """
    if not is_traktor_running():
        return True
    if not _ask_traktor_to_quit():
        return not is_traktor_running()
    return _wait_until_gone(timeout_s, sleep=sleep)


def _log():
    import logging

    return logging.getLogger(__name__)


def bring_traktor_forward() -> bool:
    """Unhide/restore Traktor and bring its window back to the front.

    Used when Kommander itself is brought back from hidden/minimized, so
    both apps reappear together (the caller re-raises Kommander on top
    afterwards).

    * macOS: undo a Cmd+H hide via ``NSRunningApplication.unhide()`` and
      activate Traktor so its windows order in above other apps. A window
      minimized to the Dock stays there — programmatic activation does not
      de-miniaturize on macOS.
    * Windows: restore a minimized Traktor window (``SW_RESTORE``), or
      raise an already-restored one without activating it.

    Best-effort: returns True when a running Traktor was found and
    poked, False otherwise (including on any error).
    """
    try:
        if sys.platform == "win32":
            from core import drag_win

            return drag_win.restore_traktor_window()
        if sys.platform == "darwin":
            for app in traktor_process.running_apps():
                if app.isHidden():
                    app.unhide()
                app.activateWithOptions_(_ACTIVATE_IGNORING_OTHER_APPS)
                return True
        return False
    except Exception:
        import logging

        logging.getLogger(__name__).debug("bring_traktor_forward failed", exc_info=True)
        return False


def find_traktor_nml() -> Path | None:
    """The ``collection.nml`` belonging to the Traktor we're working with.

    Traktor keeps one library folder per version and upgrades leave the old
    ones behind, so "newest by mtime" — the original rule, still the fallback
    below — can select a library Traktor no longer opens. Matching the
    installed app's version makes the choice exact instead of incidental.
    """
    from core.traktor_install import major_of, resolve_nml, version_of

    app = find_traktor_app()
    if app is not None:
        matched = resolve_nml(version_of(app), major_of(app))
        if matched is not None:
            return matched

    ni = ni_documents_root()
    if ni.exists():
        files = sorted(ni.rglob("collection.nml"), key=lambda p: p.stat().st_mtime, reverse=True)
        if files:
            return files[0]
    return None


def _is_drive_letter(volume: str) -> bool:
    """True if *volume* is a Windows drive-letter token like ``C:``."""
    return len(volume) == 2 and volume[1] == ":" and volume[0].isalpha()


def _nml_build_path(loc) -> str:
    """Reconstruct an absolute file path from a Traktor LOCATION element.

    Traktor encodes paths differently per platform:
      * macOS:   DIR="/:Users/:me/:Music/:" FILE="track.mp3" VOLUME="Macintosh HD"
                 — VOLUME is the *name* of the boot/external volume; for the boot
                 volume DIR is already an absolute POSIX path under "/", so we
                 ignore VOLUME and use DIR directly.
      * Windows: DIR="/:Users/:me/:Music/:" FILE="track.mp3" VOLUME="C:"
                 — VOLUME is the drive letter and DIR is relative to the drive
                 root, so the absolute path is VOLUME + DIR + FILE
                 → "C:/Users/me/Music/track.mp3".
    """
    file_ = loc.get("FILE", "")
    if not file_:
        return ""
    dir_clean = loc.get("DIR", "").replace("/:", "/")
    # Traktor encodes a literal ":" in a filename as "//" (double slash), since
    # ":" is the HFS path separator. Decode back so open() finds the file
    # (e.g. NML "Re//elected" → "Re:elected"). macOS-only encoding; harmless on
    # Windows where ":" can't appear in a filename anyway.
    file_ = file_.replace("//", ":")
    volume = loc.get("VOLUME", "")
    if _is_drive_letter(volume):
        full = volume + dir_clean.rstrip("/") + "/" + file_
    else:
        full = dir_clean.rstrip("/") + "/" + file_
    return os.path.normpath(full)


def parse_traktor_nml(nml_path: Path) -> list[str]:
    try:
        tree = ET.parse(str(nml_path))
    except ET.ParseError:
        return []
    # Traktor sometimes keeps multiple <ENTRY> elements with the same AUDIO_ID
    # — e.g. after a file is renamed, the old reference lingers. Dedupe by
    # AUDIO_ID, preferring an entry whose path resolves on disk.
    by_audio_id: dict[str, str] = {}
    extras: list[str] = []
    for entry in tree.getroot().findall(".//ENTRY"):
        loc = entry.find("LOCATION")
        if loc is None:
            continue
        full = _nml_build_path(loc)
        if not full or Path(full).suffix.lower() not in AUDIO_EXTS:
            continue
        audio_id = entry.get("AUDIO_ID", "")
        if not audio_id:
            extras.append(full)
            continue
        existing = by_audio_id.get(audio_id)
        if existing is None:
            by_audio_id[audio_id] = full
        elif not Path(existing).exists() and Path(full).exists():
            by_audio_id[audio_id] = full
    return list(by_audio_id.values()) + extras


def parse_traktor_nml_metadata(nml_path: Path) -> dict[str, dict]:
    """Parse collection.nml and return path → {artist, title, album, genre, rating, …} dict.

    Uses the same path-building logic as parse_traktor_nml so lookup keys always
    match the file paths returned to the analysis worker.

    Empty if the file cannot be read — cut short because Traktor is writing
    it, or not there. That is "nothing is known", not "every track is
    blank": see :mod:`core.metadata_refresh` for what depends on the
    difference.
    """
    try:
        tree = ET.parse(str(nml_path))
    except (ET.ParseError, OSError) as exc:
        _log().warning("cannot read %s (%s) — no metadata from Traktor this time", nml_path, exc)
        return {}
    result: dict[str, dict] = {}
    for entry in tree.getroot().findall(".//ENTRY"):
        loc = entry.find("LOCATION")
        if loc is None:
            continue
        full = _nml_build_path(loc)
        if not full:
            continue

        album_el = entry.find("ALBUM")
        info_el = entry.find("INFO")
        mk_el = entry.find("MUSICAL_KEY")
        tempo_el = entry.find("TEMPO")

        musical_key = -1
        if mk_el is not None:
            try:
                musical_key = int(mk_el.get("VALUE", -1))
            except (ValueError, TypeError):
                pass

        traktor_bpm = 0.0
        if tempo_el is not None:
            try:
                q = float(tempo_el.get("BPM_QUALITY", 0))
                if q > 0:
                    traktor_bpm = float(tempo_el.get("BPM", 0))
            except (ValueError, TypeError):
                pass

        rating = 0
        if info_el is not None:
            try:
                # Traktor stores RANKING as 0–255; divide by 51 → 0–5 stars
                rating = int(info_el.get("RANKING", 0)) // 51
            except (ValueError, TypeError):
                pass

        def _info(info: ET.Element[str] | None, attr: str) -> str:
            return info.get(attr, "") if info is not None else ""

        def _info_int(info: ET.Element[str] | None, attr: str):
            if info is None:
                return None
            try:
                return int(info.get(attr, "")) if info.get(attr) else None
            except (ValueError, TypeError):
                return None

        result[full] = {
            "artist": entry.get("ARTIST", ""),
            "title": entry.get("TITLE", ""),
            "album": album_el.get("TITLE", "") if album_el is not None else "",
            "genre": _info(info_el, "GENRE"),
            "musical_key": musical_key,
            "traktor_bpm": traktor_bpm,
            "rating": rating,
            # Display-only extras (INFO element attributes).
            "release_date": _info(info_el, "RELEASE_DATE"),
            "import_date": _info(info_el, "IMPORT_DATE"),
            "label": _info(info_el, "LABEL"),
            "bitrate": _info_int(info_el, "BITRATE"),
            "play_count": _info_int(info_el, "PLAYCOUNT"),
        }
    return result


def get_hotcue_candidates(
    nml_path: Path, include_existing: bool = False
) -> list[tuple[str, float, float]]:
    """``(path, bpm, beat_phase_ms)`` for tracks eligible for hotcue detection.

    See :func:`hotcue_eligibility`, which also says why the rest were skipped.
    """
    return hotcue_eligibility(nml_path, include_existing)[0]


def hotcue_eligibility(
    nml_path: Path, include_existing: bool = False
) -> tuple[list[tuple[str, float, float]], dict[str, int]]:
    """Eligible tracks for auto hotcue detection, and why the others are not.

    Returns ``(candidates, skipped)``: *candidates* as ``(path, bpm,
    beat_phase_ms)``; *skipped* counts each reason a track was left out
    (``"no BPM"``, ``"no beat grid"``, ``"already has hotcues"``).

    Eligible means:
    - Traktor has a beat grid for it: the TYPE=4 grid marker written by
      analysis, or the slot-0 autocue added when it was loaded in a deck
    - No hotcues in slots 1–7 are set yet, ours or the user's
      (``include_existing=True`` re-processes those too)
    - BPM is present in the TEMPO element (BPM_QUALITY > 0)

    ``beat_phase_ms`` is the START of the TYPE=4 grid-marker cue, which is
    the timestamp of beat 1 according to Traktor's beat grid.  Falls back to
    0 if no grid marker is found.
    """
    from core.collection_source import collection_cues
    from core.cue_points import eligibility

    return eligibility(collection_cues(nml_path), include_existing)


def nml_cues(nml_path: Path) -> list[TrackCues]:
    """Every audio track's BPM and cue points, from ``CUE_V2`` / ``TEMPO`` in the NML.

    BPM is 0 unless the TEMPO element has one with BPM_QUALITY > 0. Empty if
    the file cannot be read.
    """
    try:
        tree = ET.parse(str(nml_path))
    except (OSError, ET.ParseError):
        return []
    out: list[TrackCues] = []
    for entry in tree.getroot().findall(".//ENTRY"):
        loc = entry.find("LOCATION")
        if loc is None:
            continue
        full = _nml_build_path(loc)
        if not full or Path(full).suffix.lower() not in AUDIO_EXTS:
            continue
        bpm = 0.0
        tempo = entry.find("TEMPO")
        if tempo is not None:
            try:
                quality = float(tempo.get("BPM_QUALITY", 0))
                bpm = float(tempo.get("BPM", 0)) if quality > 0 else 0.0
            except (ValueError, TypeError):
                bpm = 0.0
        cues = []
        for c in entry.findall("CUE_V2"):
            try:
                start = float(c.get("START", 0))
            except (ValueError, TypeError):
                start = 0.0
            cues.append(
                Cue(
                    _safe_int(c.get("TYPE")),
                    _safe_int(c.get("HOTCUE", "-1")),
                    start,
                    c.get("NAME", ""),
                )
            )
        out.append(TrackCues(full, bpm, tuple(cues)))
    return out


def get_tracks_without_hotcues(nml_path: Path) -> list[str]:
    """Return a list of track paths from *nml_path* that have no hotcue slots set.

    A track is considered to have hotcues when it contains at least one
    ``CUE_V2`` element whose ``HOTCUE`` attribute is >= 0 (i.e. a named
    hotcue slot A–H, not a plain memory cue).
    """
    try:
        tree = ET.parse(str(nml_path))
    except ET.ParseError:
        return []
    result: list[str] = []
    for entry in tree.getroot().findall(".//ENTRY"):
        loc = entry.find("LOCATION")
        if loc is None:
            continue
        full = _nml_build_path(loc)
        if not full or Path(full).suffix.lower() not in AUDIO_EXTS:
            continue
        cues = entry.findall("CUE_V2")
        hotcue_slots = {_safe_int(c.get("HOTCUE", "-1")) for c in cues}
        # Only process tracks where Traktor's autocue (slot 0) exists
        # but no user hotcues (slots 1–7) have been set yet.
        if 0 in hotcue_slots and not hotcue_slots.intersection(range(1, 8)):
            result.append(full)
    return result


def _safe_int(value: str | None, default: int = -1) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def write_hotcues_to_nml(
    nml_path: Path,
    hotcues_by_path: dict[str, list[tuple[float, str, int]]],
    force: bool = False,
) -> int:
    """Insert ``CUE_V2`` hotcue elements into *nml_path* for each path in
    *hotcues_by_path* that currently has no hotcues.

    *hotcues_by_path* maps ``path → [(start_ms, label, cue_type), …]``
    where ``cue_type`` is the Traktor TYPE integer (0=hotcue, 2=fade-out, …).

    Only entries that still have no hotcue slots are modified (idempotent).
    Makes a ``.bak`` backup before writing.

    Returns the number of tracks that were updated.
    """
    try:
        tree = ET.parse(str(nml_path))
    except ET.ParseError as exc:
        raise RuntimeError(f"Cannot parse NML: {exc}") from exc

    # Build path → ENTRY element index for fast lookup
    path_to_entry: dict[str, ET.Element] = {}
    for entry in tree.getroot().findall(".//ENTRY"):
        loc = entry.find("LOCATION")
        if loc is None:
            continue
        full = _nml_build_path(loc)
        if full:
            path_to_entry[full] = entry

    updated = 0
    for path, cues in hotcues_by_path.items():
        entry = path_to_entry.get(path)
        if entry is None:
            continue

        existing = entry.findall("CUE_V2")

        # Determine whether user-created cues (non-TK) occupy any slots 1–7
        has_user_cues = any(
            _safe_int(c.get("HOTCUE", "-1")) >= 1 and not c.get("NAME", "").startswith(_TK_PREFIX)
            for c in existing
        )

        if has_user_cues and not force:
            continue

        # Always remove our own TK cues before writing the new analysis.
        # User cues are never removed regardless of the force flag.
        for c in list(existing):
            if _safe_int(c.get("HOTCUE", "-1")) >= 1 and c.get("NAME", "").startswith(_TK_PREFIX):
                entry.remove(c)

        # Recompute after removal; find free slots (1–7) to write into
        existing = entry.findall("CUE_V2")
        occupied = {_safe_int(c.get("HOTCUE", "-1")) for c in existing}
        free_slots = [s for s in range(1, 8) if s not in occupied]

        # Skip generated cues that land within one beat of an existing cue
        # (autocue, user hotcues, memory cues — but not grid markers TYPE=4).
        beat_ms = 0.0
        tempo = entry.find("TEMPO")
        if tempo is not None:
            try:
                bpm = float(tempo.get("BPM", 0))
                if bpm > 0:
                    beat_ms = 60_000.0 / bpm
            except (ValueError, TypeError):
                pass

        existing_starts: list[float] = []
        for c in existing:
            if c.get("TYPE") == "4":
                continue
            try:
                existing_starts.append(float(c.get("START", 0)))
            except (ValueError, TypeError):
                pass

        filtered_cues = [
            (s, label, t)
            for s, label, t in cues
            if beat_ms <= 0 or all(abs(s - es) > beat_ms for es in existing_starts)
        ]

        existing_count = len(existing)
        for i, (start_ms, label, cue_type) in enumerate(filtered_cues[: len(free_slots)]):
            slot = free_slots[i]
            cue = ET.SubElement(entry, "CUE_V2")
            cue.set("NAME", label)
            cue.set("DISPL_ORDER", str(existing_count + i))
            cue.set("TYPE", str(cue_type))
            cue.set("START", f"{start_ms:.6f}")
            cue.set("LEN", "0.000000")
            cue.set("REPEATS", "-1")
            cue.set("HOTCUE", str(slot))
        updated += 1

    if updated == 0:
        return 0

    write_collection(tree, nml_path)
    return updated


def cleanup_tk_hotcues(nml_path: Path) -> tuple[int, int]:
    """Remove every CUE_V2 hotcue we generated (NAME starts with ``_TK_PREFIX``).

    Walks every ENTRY in *nml_path* and deletes any CUE_V2 whose HOTCUE slot
    is >= 1 and whose NAME starts with the TK prefix. User-created cues and
    Traktor's autocue (slot 0) / grid markers (TYPE=4) are never touched.

    Makes a ``.bak`` backup before writing.

    Returns ``(tracks_modified, cues_removed)``.
    """
    try:
        tree = ET.parse(str(nml_path))
    except ET.ParseError as exc:
        raise RuntimeError(f"Cannot parse NML: {exc}") from exc

    tracks_modified = 0
    cues_removed = 0
    for entry in tree.getroot().findall(".//ENTRY"):
        removed_here = 0
        for cue in list(entry.findall("CUE_V2")):
            slot = _safe_int(cue.get("HOTCUE", "-1"))
            if slot >= 1 and cue.get("NAME", "").startswith(_TK_PREFIX):
                entry.remove(cue)
                removed_here += 1
        if removed_here:
            tracks_modified += 1
            cues_removed += removed_here

    if cues_removed == 0:
        return 0, 0

    write_collection(tree, nml_path)
    return tracks_modified, cues_removed


def find_traktor_history_dir() -> Path | None:
    ni = ni_documents_root()
    if ni.exists():
        for d in sorted(ni.iterdir(), reverse=True):
            if d.is_dir():
                hist = d / "History"
                if hist.is_dir():
                    return hist
    return None
