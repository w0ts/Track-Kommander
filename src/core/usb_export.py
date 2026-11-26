"""Copy tracks to a USB drive, named the way players need.

The audio half of the device export (:mod:`core.device_export` writes the
database and analysis over these copies).

Filenames are flattened into one folder and sanitised for FAT32/exFAT, which
is what USB drives for players are formatted as: those filesystems reject
``\\ / : * ? " < > |``, dislike trailing dots and spaces, and have no useful
notion of a deep path.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from core.anlz_path import anlz_dir

log = logging.getLogger(__name__)

#: Where audio lands on the drive.
AUDIO_DIRNAME = "Contents"

#: Characters FAT32/exFAT refuse, plus control characters. Only used for
#: the spelling an older build gave a file (see :func:`sanitise`).
_ILLEGAL = re.compile(r'[\\/:*?"<>|\x00-\x1f]')

#: Everything a name on the stick may hold: plain ASCII letters, digits,
#: space and - _ . ( ) &. A player shows these names only in its folder
#: view — the database carries the real title — so there is nothing to gain
#: from anything else, and an old player has been seen to choke on names.
_NOT_ALLOWED = re.compile(r"[^A-Za-z0-9 ._()&-]")
_RUNS = re.compile(r"_{2,}| {2,}")
_HAS_ALNUM = re.compile(r"[A-Za-z0-9]")

#: Longest folder or file name written, extension included. Rekordbox 7.2.18
#: cuts at 48 ("IGLO - 5 Years Backhaus Fundraiser Compilati.mp3"). An
#: XDJ-1000 froze on a stick of ours with the full names, loaded a one-track
#: stick at 48, and froze again on larger ones. 20 is the user's call, well
#: below anything a player has been seen to choke on: a whole path stays
#: under 75 characters.
MAX_NAME = 20

#: Longest whole path on the stick, e.g. ``/Contents/a/b/c.mp3``. Rekordbox
#: 7.2.18 wrote a 110-character path for the one track an XDJ-1000 was seen
#: to load with full analysis; 100 keeps a margin below that. Three names of
#: :data:`MAX_NAME` stay far below it; :func:`conforms` enforces it anyway.
MAX_PATH = 100


#: Letters that decompose to nothing useful in NFKD but have an ASCII form.
_FOLD = str.maketrans(
    {
        "ß": "ss",
        "æ": "ae",
        "Æ": "AE",
        "ø": "o",
        "Ø": "O",
        "œ": "oe",
        "Œ": "OE",
        "ð": "d",
        "Ð": "D",
        "þ": "th",
        "Þ": "Th",
        "ł": "l",
        "Ł": "L",
        "đ": "d",
        "Đ": "D",
        "ı": "i",
        "ħ": "h",
        "Ħ": "H",
    }
)


def fold_ascii(text: str) -> str:
    """*text* with Latin accents folded away: ``Kölsch`` → ``Kolsch``.

    macOS writes decomposed names to exFAT while the database row keeps the
    composed form, and whether a player matches the two is not a thing to
    find out at a gig. Anything with an ASCII form gets it; scripts without
    one (Japanese, Cyrillic) are kept, composed, rather than turned into
    underscores.
    """
    decomposed = unicodedata.normalize("NFKD", text.translate(_FOLD))
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return unicodedata.normalize("NFC", stripped)


def _plain(text: str) -> str:
    """*text* reduced to the characters allowed on the stick."""
    cleaned = _NOT_ALLOWED.sub("_", fold_ascii(text))
    cleaned = _RUNS.sub(lambda m: m.group(0)[0], cleaned)
    # No leading dot (a hidden file) and no trailing dot or space, which is
    # legal to create but confuses Windows tooling.
    return cleaned.strip().lstrip(".").rstrip(". ")


def sanitise(name: str, *, fold: bool = True) -> str:
    """A filename for the stick: plain ASCII, at most :data:`MAX_NAME` long.

    A name with no letter or digit left — a title in a script with no ASCII
    form — becomes ``track``; a clash in its folder is resolved later with
    a numeric suffix.

    *fold* off gives the name an older build would have used — accents kept,
    only FAT-illegal characters replaced — which is how a file already on
    such a key is found again.
    """
    if not fold:
        return _ILLEGAL.sub("_", name).strip().rstrip(". ") or "track"
    stem, dot, ext = name.rpartition(".")
    if not dot or not 0 < len(ext) <= 5 or not ext.isalnum():
        stem, ext = name, ""
    stem, ext = _plain(stem), _plain(ext).lower()
    if not _HAS_ALNUM.search(stem):
        stem = "track"
    tail = "." + ext if ext else ""
    return fit_name(stem, tail) if len(stem) + len(tail) > MAX_NAME else stem + tail


def fit_name(stem: str, tail: str) -> str:
    """*stem* cut so that *stem* + *tail* fits :data:`MAX_NAME`.

    *tail* is the part that must survive whole — the extension, or a
    collision suffix and the extension. The cut never leaves a trailing
    space or dot on the stem.
    """
    room = max(1, MAX_NAME - len(tail))
    return stem[:room].rstrip(". ") + tail


#: Where a track with no artist or album is filed.
UNKNOWN_ARTIST = "Unknown Artist"
UNKNOWN_ALBUM = "Unknown Album"


def conforms(device_path: str) -> bool:
    """True when *device_path* is at most :data:`MAX_PATH` long and every
    folder and file name in it already obeys the stick's naming rules — what :func:`folder_name` and :func:`sanitise`
    would leave unchanged. ``/Contents/`` itself is ours and always passes.
    """
    parts = [p for p in device_path.split("/") if p]
    if len(device_path) > MAX_PATH or not parts or parts[0] != AUDIO_DIRNAME:
        return False
    *folders, name = parts[1:] or [""]
    return bool(name) and sanitise(name) == name and all(folder_name(f, "") == f for f in folders)


def folder_name(raw: str, fallback: str) -> str:
    """A folder name for the stick, or *fallback* when nothing usable is left.

    Cut plainly at :data:`MAX_NAME`: a dot in an album name ("Vol. 2") is
    not an extension to keep.
    """
    cleaned = _plain(raw)[:MAX_NAME].rstrip(". _")
    return cleaned if _HAS_ALNUM.search(cleaned) else fallback


@dataclass(frozen=True)
class Source:
    """A file to export and what it is filed under on the drive."""

    path: str
    artist: str = ""
    album: str = ""


@dataclass
class Copy:
    """One file to place on the drive."""

    source: Path
    target: Path
    size: int = 0
    missing: bool = False

    @property
    def already_there(self) -> bool:
        return not self.missing and self.target.exists() and self.target.stat().st_size == self.size


@dataclass
class UsbPlan:
    """Everything the export would do, before it does any of it."""

    root: Path
    copies: list[Copy]

    @property
    def to_copy(self) -> list[Copy]:
        return [c for c in self.copies if not c.missing and not c.already_there]

    @property
    def missing(self) -> list[Copy]:
        return [c for c in self.copies if c.missing]

    @property
    def bytes_needed(self) -> int:
        return sum(c.size for c in self.to_copy)

    @property
    def mapping(self) -> dict[str, str]:
        """Original path → on-drive path, for rewriting the XML."""
        return {str(c.source): str(c.target) for c in self.copies if not c.missing}


def _existing_spelling(parent: Path, name: str) -> str:
    """*name* as *parent* already spells it, ignoring case; else *name*."""
    try:
        for entry in parent.iterdir():
            if entry.name.casefold() == name.casefold():
                return entry.name
    except OSError:
        pass
    return name


def _names_by_normal_form(folder: Path) -> dict[str, str]:
    """Composed form → the name as written, for what is in *folder*."""
    try:
        return {unicodedata.normalize("NFC", entry.name): entry.name for entry in folder.iterdir()}
    except OSError:
        return {}


def plan_export(
    items: list[str | Source], dest_root: Path, *, pinned: dict[str, str] | None = None
) -> UsbPlan:
    """Work out where each track goes. Touches nothing.

    *pinned* maps a source path to the on-drive path the key already holds
    it at. Those tracks stay put whatever their tags say now: filing is by
    artist and album, and a retag must not turn into a recopy plus an orphan.

    Tracks are filed under ``Contents/<artist>/<album>/``, as Rekordbox does:
    one flat directory runs out of FAT32 directory entries somewhere in the
    thousands of tracks and gets slow on exFAT before that. A bare path has
    no metadata and lands under the "Unknown" folders.

    A file the key already holds flat in ``Contents/`` — a key written before
    the folders — stays there: moving it would recopy the audio and orphan
    the old copy, for no gain.

    Collisions within a folder are resolved with a numeric suffix rather than
    by overwriting: two different tracks can easily share a filename, and
    silently keeping one of them would lose the other.
    """
    root = Path(dest_root)
    audio_dir = root / AUDIO_DIRNAME
    copies: list[Copy] = []
    used: set[str] = set()
    # Analysis folders taken, by the hash of each track's path on the stick
    # (see core.anlz_path). A player derives the folder from the path, so two
    # tracks in one folder would read each other's analysis.
    folders: set[str] = set()

    def _key(target: Path) -> str:
        return str(target.relative_to(audio_dir)).lower()

    def _folder(target: Path) -> str:
        return anlz_dir("/" + target.relative_to(root).as_posix())

    listed: dict[str, str] | None = None

    def _flat(name: str) -> Path:
        """Where *name* is, flat in ``Contents/``, as the key spells it.

        An accented name is on an exFAT key in the decomposed form macOS
        wrote, and is asked for here in the composed form the source has.
        macOS finds the file under either; Windows only under the one that
        is written, so there it is looked up in the folder's own listing —
        read once, and only for a name that has an accent to differ in.
        """
        nonlocal listed
        direct = audio_dir / name
        if name.isascii() or direct.is_file():
            return direct
        if listed is None:
            listed = _names_by_normal_form(audio_dir)
        return audio_dir / listed.get(unicodedata.normalize("NFC", name), name)

    for item in items:
        src = item if isinstance(item, Source) else Source(str(item))
        source = Path(src.path)
        if not source.is_file():
            copies.append(Copy(source=source, target=audio_dir / source.name, missing=True))
            continue

        size = source.stat().st_size
        name = sanitise(source.name)
        held = (pinned or {}).get(str(source))
        # A track the key already holds under a name that breaks today's
        # rules is not kept there: it is copied again under a conforming one.
        if held and not conforms("/" + Path(held).relative_to(root).as_posix()):
            held = None
        # Already on the key: pinned by the manifest, or flat in Contents/
        # under this name or the accented one an older build gave it.
        candidates = (
            [Path(held)] if held else [_flat(name), _flat(sanitise(source.name, fold=False))]
        )
        placed = next(
            (
                c
                for c in candidates
                if c.is_file() and _key(c) not in used and (held or c.stat().st_size == size)
            ),
            None,
        )
        if placed is not None:
            used.add(_key(placed))
            folders.add(_folder(placed))
            copies.append(Copy(source=source, target=placed, size=size))
            continue

        # The key does not distinguish case, so "Benza" and "BENZA" are one
        # folder there. Spell it the way the drive already does, or the row
        # says one thing and the directory listing another.
        artist = _existing_spelling(audio_dir, folder_name(src.artist, UNKNOWN_ARTIST))
        album = _existing_spelling(audio_dir / artist, folder_name(src.album, UNKNOWN_ALBUM))
        folder = Path(artist) / album
        stem, dot, ext = name.rpartition(".")
        if not dot:
            stem, ext = name, ""
        candidate, n = name, 2
        while (
            _key(audio_dir / folder / candidate) in used
            or _folder(audio_dir / folder / candidate) in folders
        ):
            candidate = fit_name(stem, f" ({n}){'.' + ext if ext else ''}")
            n += 1
        used.add(_key(audio_dir / folder / candidate))
        folders.add(_folder(audio_dir / folder / candidate))

        copies.append(Copy(source=source, target=audio_dir / folder / candidate, size=size))
    return UsbPlan(root=root, copies=copies)


#: Directories the OS owns on a removable volume. They are left alone: they
#: are not ours to delete, and players ignore them.
OS_MANAGED = frozenset(
    {".Spotlight-V100", ".Trashes", ".fseventsd", ".TemporaryItems", "System Volume Information"}
)


def strip_macos_metadata(root: Path) -> int:
    """Delete the sidecar files macOS leaves on a FAT/exFAT stick.

    AppleDouble files (``._name``) hold the extended attributes the
    filesystem cannot, and ``.DS_Store`` holds Finder's view settings. Neither
    is audio or analysis, and a player that walks the tree can list them as
    tracks. Returns how many were removed.

    Only regular files matching those two patterns are touched — a track
    legitimately named ``_intro.mp3`` is not one of them, and the OS-managed
    directories in :data:`OS_MANAGED` are skipped entirely.
    """
    root = Path(root)
    if not root.is_dir():
        return 0

    removed = 0
    # os.walk takes each entry's type from the directory listing; rglob plus
    # is_file() cost a stat per entry, thousands of them over USB.
    for folder, subdirs, files in os.walk(root):
        subdirs[:] = [d for d in subdirs if d not in OS_MANAGED]
        for name in files:
            if not (name.startswith("._") or name == ".DS_Store"):
                continue
            path = Path(folder) / name
            try:
                path.unlink()
                removed += 1
            except FileNotFoundError:
                # macOS drops the sidecar itself once the attributes it holds
                # are gone, so it can disappear between the listing and the
                # unlink. Already gone is the outcome we wanted.
                removed += 1
            except OSError as exc:
                log.warning("could not remove %s: %s", path, exc)
    if removed:
        log.info("removed %d macOS sidecar files from %s", removed, root)
    return removed


def write_if_changed(
    path: Path, data: bytes, *, ignore: Callable[[bytes], bytes] | None = None
) -> bool:
    """Write *data* to *path* unless the file already holds it. True if written.

    A re-export mostly writes what the stick already has. Leaving those files
    alone saves a create on FAT for each, and the ``._`` sidecar macOS adds to
    every file it writes there, which the sweep would then have to delete.
    *ignore* maps both sides before comparing, for a part that is expected to
    differ every time (an ID generated per render).
    """
    path = Path(path)
    try:
        old = path.read_bytes()
    except FileNotFoundError:
        old = None
    if old is not None:
        same = (ignore(old) == ignore(data)) if ignore else old == data
        if same:
            return False
    path.write_bytes(data)
    return True


def free_space(path: Path) -> int:
    """Bytes free on the volume holding *path* (0 if it can't be determined)."""
    try:
        target = path
        while not target.exists() and target != target.parent:
            target = target.parent
        return shutil.disk_usage(target).free
    except OSError:
        return 0


def run_export(
    plan: UsbPlan,
    progress: Callable[[int, int, str], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> tuple[int, list[str]]:
    """Copy the files. Returns (copied, errors).

    Each file is written to a temporary name and renamed once complete, so an
    interrupted export leaves no half-copied track that a player would choke
    on mid-set.
    """
    todo = plan.to_copy
    audio_dir = plan.root / AUDIO_DIRNAME
    audio_dir.mkdir(parents=True, exist_ok=True)
    made: set[Path] = {audio_dir}

    copied, errors = 0, []
    for i, c in enumerate(todo, start=1):
        if should_stop is not None and should_stop():
            break
        if progress is not None:
            progress(i, len(todo), c.source.name)
        tmp = c.target.with_name(c.target.name + ".part")
        try:
            if c.target.parent not in made:
                c.target.parent.mkdir(parents=True, exist_ok=True)
                made.add(c.target.parent)
            # copyfile, not copy2: copy2 carries extended attributes, and on
            # exFAT/FAT32 macOS spills those into an AppleDouble "._name"
            # sidecar beside every track. A player walking the tree can list
            # those as tracks. The audio is all a player wants here anyway.
            shutil.copyfile(c.source, tmp)
            tmp.replace(c.target)
            copied += 1
        except OSError as exc:
            tmp.unlink(missing_ok=True)
            errors.append(f"{c.source.name}: {exc}")
    log.info("copied %d/%d tracks to %s", copied, len(todo), plan.root)
    return copied, errors
