"""The Windows half of trim_kit.py: the same trim for a Windows kit (traktor-native/win/make_kit.ps1).

PySide6 on Windows keeps its Qt DLLs beside its modules (PySide6\\Qt6Core.dll ...) and its
plugins in PySide6\\plugins. Which DLLs stay is followed through the PE import tables (normal and
delay-loaded) of the kept modules and plugins: no tool needed (no dumpbin on a build PC).
Qt plays audio through Windows' own media backend there (tk_launch sets QT_MEDIA_BACKEND=windows),
so Qt's ffmpeg copy goes too.
"""

from __future__ import annotations

import struct
from pathlib import Path

from trim_kit import KEEP_QT, remove

KEEP_PLUGINS_WIN: dict[str, set[str] | None] = {
    "platforms": {"qwindows.dll", "qoffscreen.dll", "qminimal.dll"},
    "styles": None,
    "imageformats": {"qgif.dll", "qico.dll", "qjpeg.dll"},
    "tls": None,
    "networkinformation": None,
    "multimedia": {"windowsmediaplugin.dll"},
}
PY_DROP_WIN = [
    "Lib/test",
    "Lib/idlelib",
    "Lib/tkinter",
    "Lib/turtledemo",
    "Lib/turtle.py",
    "Lib/ensurepip",
    "tcl",
    "include",
    "libs",
    "DLLs/_tkinter.pyd",
    "DLLs/tcl86t.dll",
    "DLLs/tk86t.dll",
]
#: Never needed by TK: Qt's software OpenGL (a fallback for machines without a GPU driver,
#: 20 MB) and the tools.
DROP_WIN = {"opengl32sw.dll"}


def _rva_to_offset(sections: list[tuple[int, int, int]], rva: int) -> int | None:
    for va, size, raw in sections:
        if va <= rva < va + size:
            return rva - va + raw
    return None


def _cstr(data: bytes, off: int | None) -> str:
    if off is None or off >= len(data):
        return ""
    return data[off : data.index(b"\0", off)].decode("ascii", "replace")


def pe_imports(path: Path) -> set[str]:
    """Lower-cased names of the DLLs a PE file imports, normal and delay-loaded."""
    data = path.read_bytes()
    if data[:2] != b"MZ":
        return set()
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    if data[pe : pe + 4] != b"PE\0\0":
        return set()
    nsect, opt_size = (
        struct.unpack_from("<H", data, pe + 6)[0],
        struct.unpack_from("<H", data, pe + 20)[0],
    )
    opt = pe + 24
    magic = struct.unpack_from("<H", data, opt)[0]
    dirs = opt + (112 if magic == 0x20B else 96)  # PE32+ or PE32
    sec = opt + opt_size
    sections = []
    for i in range(nsect):
        vsize, va, rawsize, raw = struct.unpack_from("<IIII", data, sec + i * 40 + 8)
        sections.append((va, max(vsize, rawsize), raw))
    names: set[str] = set()
    for index, entry_size, name_at in ((1, 20, 12), (13, 32, 4)):  # import, delay import
        rva = struct.unpack_from("<I", data, dirs + index * 8)[0]
        off = _rva_to_offset(sections, rva) if rva else None
        while off is not None and off + entry_size <= len(data):
            entry = data[off : off + entry_size]
            if not any(entry):
                break
            name_rva = struct.unpack_from("<I", entry, name_at)[0]
            name = _cstr(data, _rva_to_offset(sections, name_rva))
            if name:
                names.add(name.lower())
            off += entry_size
    return names


def _dll_closure(folder: Path, roots: list[Path]) -> set[str]:
    """Lower-cased names of the DLLs in *folder* the roots load, transitively."""
    here = {f.name.lower(): f for f in folder.glob("*.dll")}
    seen: set[str] = set()
    todo = [n for r in roots for n in pe_imports(r)]
    while todo:
        name = todo.pop()
        if name in seen or name not in here:
            continue
        seen.add(name)
        todo += pe_imports(here[name])
    return seen


def trim_pyside_win(pyside: Path) -> int:
    saved = 0
    for f in list(pyside.iterdir()):
        stem = f.name.split(".")[0]
        if f.suffix in (".pyd", ".pyi") and stem.startswith("Qt") and stem not in KEEP_QT:
            saved += remove(f)
        elif f.suffix == ".pyi" or f.suffix == ".exe" or f.name.lower() in DROP_WIN:
            saved += remove(f)
        elif f.is_dir() and f.name in {
            "qml",
            "translations",
            "resources",
            "metatypes",
            "include",
            "typesystems",
            "glue",
            "scripts",
            "examples",
            "doc",
        }:
            saved += remove(f)
    plugins = pyside / "plugins"
    for folder in list(plugins.iterdir()) if plugins.is_dir() else []:
        keep = KEEP_PLUGINS_WIN.get(folder.name, set())
        if folder.name not in KEEP_PLUGINS_WIN:
            saved += remove(folder)
        elif keep is not None:
            for f in list(folder.iterdir()):
                if f.name not in keep:
                    saved += remove(f)
    roots = [f for f in pyside.glob("*.pyd")]
    roots += [f for f in plugins.rglob("*.dll")] if plugins.is_dir() else []
    needed = _dll_closure(pyside, roots)
    for dll in list(pyside.glob("*.dll")):
        if dll.name.lower() not in needed:
            saved += remove(dll)
    return saved


def trim_python_win(py: Path) -> int:
    return sum(remove(py / rel) for rel in PY_DROP_WIN)
