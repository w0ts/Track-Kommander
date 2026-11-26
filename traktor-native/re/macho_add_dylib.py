"""Add an LC_LOAD_WEAK_DYLIB load command to a thin arm64 Mach-O executable.

Used by make_slim_app.sh so "Traktor Pro 4 TK.app" loads libtk by itself (a Finder or Dock launch
has no DYLD_INSERT_LIBRARIES). Weak: Traktor still starts when the library is missing. The file
must be unsigned first (`codesign --remove-signature`) and signed again after: the new command is
written into the free space after the existing ones, before the first section's data.

Usage: python macho_add_dylib.py <executable> <install name, e.g. @executable_path/libtk.dylib>
       python macho_add_dylib.py --thin-arm64 <universal executable> <out>
The second form is `lipo -thin arm64` (the slice's bytes, as they are): the Mac that runs the
installer may have no Xcode tools, and lipo is one of them.
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

MH_MAGIC_64 = 0xFEEDFACF
FAT_MAGIC = 0xCAFEBABE  # big-endian fat header; FAT_MAGIC_64 has 64-bit offsets
FAT_MAGIC_64 = 0xCAFEBABF
CPU_TYPE_ARM64 = 0x0100000C
LC_SEGMENT_64 = 0x19
LC_LOAD_WEAK_DYLIB = 0x80000018
LC_LOAD_DYLIB = 0xC
LC_CODE_SIGNATURE = 0x1D
HEADER = struct.Struct("<IiiIIIII")  # magic cputype cpusubtype filetype ncmds sizeofcmds flags rsv
SEGMENT = struct.Struct("<II16sQQQQiiII")
SECTION = struct.Struct("<16s16sQQIIIIIIII")


def first_data_offset(data: bytes, ncmds: int) -> int:
    """Where the first section's bytes start: the load commands must end before it."""
    off, lowest = HEADER.size, len(data)
    for _ in range(ncmds):
        cmd, size = struct.unpack_from("<II", data, off)
        if cmd == LC_SEGMENT_64:
            seg = SEGMENT.unpack_from(data, off)
            nsects = seg[9]
            for i in range(nsects):
                sect = SECTION.unpack_from(data, off + SEGMENT.size + i * SECTION.size)
                sect_off, sect_size = sect[4], sect[3]
                if sect_size and sect_off:
                    lowest = min(lowest, sect_off)
        off += size
    return lowest


def existing_dylibs(data: bytes, ncmds: int) -> list[str]:
    names, off = [], HEADER.size
    for _ in range(ncmds):
        cmd, size = struct.unpack_from("<II", data, off)
        if cmd in (LC_LOAD_DYLIB, LC_LOAD_WEAK_DYLIB):
            (name_off,) = struct.unpack_from("<I", data, off + 8)
            raw = data[off + name_off : off + size]
            names.append(raw.split(b"\0", 1)[0].decode())
        if cmd == LC_CODE_SIGNATURE:
            raise SystemExit("still signed: run codesign --remove-signature first")
        off += size
    return names


def thin_arm64(fat: bytes) -> bytes:
    """The arm64 slice of a universal Mach-O, byte for byte what `lipo -thin arm64` writes."""
    magic, nfat = struct.unpack_from(">II", fat, 0)
    if magic not in (FAT_MAGIC, FAT_MAGIC_64):
        raise SystemExit("not a universal Mach-O")
    entry = struct.Struct(">iiQQII" if magic == FAT_MAGIC_64 else ">iiIII")
    for i in range(nfat):
        cputype, _sub, offset, size, *_ = entry.unpack_from(fat, 8 + i * entry.size)
        if cputype == CPU_TYPE_ARM64:
            if offset + size > len(fat):
                raise SystemExit("the arm64 slice runs past the end of the file")
            return fat[offset : offset + size]
    raise SystemExit("no arm64 slice")


def main() -> None:
    if sys.argv[1] == "--thin-arm64":
        src, out = Path(sys.argv[2]), Path(sys.argv[3])
        out.write_bytes(thin_arm64(src.read_bytes()))
        # lipo keeps the mode; a plain write does not, and a non-executable main executable
        # cannot be launched ("can't be opened", launchd error 111; 2026-11-26).
        out.chmod(src.stat().st_mode & 0o777)
        print(f"arm64 slice -> {sys.argv[3]}")
        return
    path, name = Path(sys.argv[1]), sys.argv[2]
    data = bytearray(path.read_bytes())
    magic, cputype, cpusub, ftype, ncmds, sizeofcmds, flags, rsv = HEADER.unpack_from(data, 0)
    if magic != MH_MAGIC_64:
        raise SystemExit("not a thin 64-bit Mach-O (lipo -thin it first)")
    if name in existing_dylibs(data, ncmds):
        print(f"already loads {name}")
        return
    path_bytes = name.encode() + b"\0"
    cmdsize = (24 + len(path_bytes) + 7) // 8 * 8
    command = struct.pack("<IIIIII", LC_LOAD_WEAK_DYLIB, cmdsize, 24, 2, 0x10000, 0x10000)
    command += path_bytes.ljust(cmdsize - 24, b"\0")
    at = HEADER.size + sizeofcmds
    room = first_data_offset(data, ncmds) - at
    if room < cmdsize:
        raise SystemExit(f"no room for the load command ({room} bytes free, {cmdsize} needed)")
    if any(data[at : at + cmdsize]):
        raise SystemExit("the space after the load commands is not empty: refusing")
    data[at : at + cmdsize] = command
    HEADER.pack_into(
        data, 0, magic, cputype, cpusub, ftype, ncmds + 1, sizeofcmds + cmdsize, flags, rsv
    )
    path.write_bytes(bytes(data))
    print(f"added {name} ({cmdsize} bytes; {room - cmdsize} bytes still free)")


if __name__ == "__main__":
    main()
