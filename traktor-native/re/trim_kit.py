#!/usr/bin/env python3
"""Trim a Track Kommander kit (make_kit.sh) to what TK inside Traktor loads.

The kit is TK's runtime packages as uv installs them: the whole PySide6 (Qt WebEngine, Quick,
3D, Designer...), every package's test suite, Python's tkinter and idle. TK imports QtCore,
QtGui and QtWidgets; the add-on adds QtMultimedia (audio preview) and QtNetwork. Everything
else goes:

- PySide6: Python modules not in KEEP_QT; Qt frameworks/dylibs nothing kept links to (the
  closure is followed with otool -L from the kept modules and plugins); qml, translations,
  metatypes, libexec, the tools (Designer, qmlls...), headers and type stubs; the plugin
  folders not in KEEP_PLUGINS; Qt's ffmpeg (the media backend is AVFoundation on macOS,
  embed_tk.py sets QT_MEDIA_BACKEND=darwin).
- site-packages: tests/ folders, __pycache__ (type stubs stay: librosa loads its .pyi through lazy_loader).
- python/: tkinter + Tcl/Tk, idle, ensurepip, the test suite, headers, the static library.

macOS here (otool); a Windows kit is trimmed by trim_kit_win.py. Usage: trim_kit.py <kit-dir>.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

KEEP_QT = {"QtCore", "QtGui", "QtWidgets", "QtNetwork", "QtMultimedia"}
# plugin folder -> files kept in it (None = the whole folder)
KEEP_PLUGINS: dict[str, set[str] | None] = {
    "platforms": {"libqcocoa.dylib", "libqoffscreen.dylib", "libqminimal.dylib"},
    "styles": None,
    "imageformats": {
        "libqgif.dylib",
        "libqico.dylib",
        "libqjpeg.dylib",
        "libqmacheif.dylib",
        "libqmacjp2.dylib",
        "libqicns.dylib",
    },
    "tls": None,
    "networkinformation": None,
    "multimedia": {"libdarwinmediaplugin.dylib"},
}
PY_DROP = [
    "lib/python3.13/test",
    "lib/python3.13/idlelib",
    "lib/python3.13/tkinter",
    "lib/python3.13/turtledemo",
    "lib/python3.13/turtle.py",
    "lib/python3.13/ensurepip",
    "lib/python3.13/lib-dynload/_tkinter.cpython-313-darwin.so",
    "include",
    "share",
    "lib/pkgconfig",
    "lib/tcl8",
    "lib/tcl8.6",
    "lib/tk8.6",
    "lib/itcl4.2.4",
    "lib/thread2.8.11",
    "lib/libtcl8.6.dylib",
    "lib/libtk8.6.dylib",
    "lib/libtclstub8.6.a",
    "lib/libtkstub8.6.a",
]
_RPATH_DEP = re.compile(r"@rpath/([^/\s]+?)(?:\.framework/|\s|$)")


def size(p: Path) -> int:
    if p.is_symlink() or p.is_file():
        return p.lstat().st_size
    return sum(f.lstat().st_size for f in p.rglob("*") if f.is_file() and not f.is_symlink())


def remove(p: Path) -> int:
    if not (p.exists() or p.is_symlink()):
        return 0
    n = size(p)
    if p.is_dir() and not p.is_symlink():
        shutil.rmtree(p)
    else:
        p.unlink()
    return n


def rpath_deps(binary: Path) -> set[str]:
    """Names (framework or dylib file name) a Mach-O loads through @rpath."""
    out = subprocess.run(["otool", "-L", str(binary)], capture_output=True, text=True, check=True)
    return {m.group(1) for line in out.stdout.splitlines()[1:] if (m := _RPATH_DEP.search(line))}


def qt_closure(qt_lib: Path, roots: list[Path]) -> set[str]:
    """Every Qt/lib entry (QtCore.framework, libfoo.dylib) the roots load, transitively."""
    seen: set[str] = set()
    todo = [d for r in roots for d in rpath_deps(r)]
    while todo:
        name = todo.pop()
        entry = (
            qt_lib / f"{name}.framework"
            if (qt_lib / f"{name}.framework").exists()
            else qt_lib / name
        )
        if entry.name in seen or not entry.exists():
            continue
        seen.add(entry.name)
        binary = entry / "Versions/A" / name if entry.suffix == ".framework" else entry
        todo += rpath_deps(binary)
    return seen


def trim_pyside(pyside: Path) -> int:
    saved = 0
    qt = pyside / "Qt"
    for f in list(pyside.iterdir()):
        stem = f.name.split(".")[0]
        if f.name.endswith((".abi3.so", ".pyi")) and stem.startswith("Qt"):
            if stem not in KEEP_QT:
                saved += remove(f)
        elif f.name.endswith(".pyi"):
            saved += remove(f)
        elif f.suffix == ".app" or (f.is_file() and f.stat().st_mode & 0o111 and not f.suffix):
            saved += remove(f)  # Designer.app, qmlls, uic, rcc, lupdate...
        elif f.name in {"include", "typesystems", "glue", "scripts", "examples", "doc"}:
            saved += remove(f)
    for name in ("qml", "translations", "metatypes", "libexec"):
        saved += remove(qt / name)
    plugins = qt / "plugins"
    for folder in list(plugins.iterdir()):
        keep = KEEP_PLUGINS.get(folder.name, set())
        if folder.name not in KEEP_PLUGINS:
            saved += remove(folder)
        elif keep is not None:
            for f in list(folder.iterdir()):
                if f.name not in keep:
                    saved += remove(f)
    roots = [pyside / f"{m}.abi3.so" for m in KEEP_QT] + [
        f for f in plugins.rglob("*.dylib") if f.is_file()
    ]
    roots += [f for f in pyside.glob("*.so") if not f.name.startswith("Qt")]  # pyside6.abi3.so...
    needed = qt_closure(qt / "lib", roots)
    for entry in list((qt / "lib").iterdir()):
        if entry.name not in needed:
            saved += remove(entry)
    return saved


def trim_site_packages(site: Path) -> int:
    saved = 0
    for d in sorted(site.rglob("tests"), key=lambda p: len(p.parts)):
        if d.is_dir() and d.exists():
            saved += remove(d)
    for pattern in ("__pycache__",):
        for f in list(site.rglob(pattern)):
            if f.exists():
                saved += remove(f)
    return saved


def trim_python(py: Path) -> int:
    saved = sum(remove(py / rel) for rel in PY_DROP)
    for config in (py / "lib/python3.13").glob("config-3.13-*"):
        saved += remove(config)
    return saved


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: trim_kit.py <kit-dir>", file=sys.stderr)
        return 2
    kit = Path(sys.argv[1])
    if not (kit / "KIT-VERSION").is_file():
        print(f"{kit} is not a kit (no KIT-VERSION): not touched", file=sys.stderr)
        return 1
    before = size(kit)
    if (kit / "python" / "python.exe").is_file():  # a Windows kit (win/make_kit.ps1)
        from trim_kit_win import trim_pyside_win, trim_python_win

        site = kit / ".venv" / "Lib" / "site-packages"
        parts = {
            "PySide6": trim_pyside_win(site / "PySide6"),
            "site-packages": trim_site_packages(site),
            "python": trim_python_win(kit / "python"),
        }
    else:
        site = kit / ".venv/lib/python3.13/site-packages"
        parts = {
            "PySide6": trim_pyside(site / "PySide6"),
            "site-packages": trim_site_packages(site),
            "python": trim_python(kit / "python"),
        }
    for name, n in parts.items():
        print(f"trim {name}: -{n / 1e6:.0f} MB")
    print(f"kit: {before / 1e6:.0f} MB -> {size(kit) / 1e6:.0f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
