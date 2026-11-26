<!--
  This file is the public repo's README (github.com/w0ts/Track-Kommander), exported as README.md
  by scripts/export_public.py. Developers and licences only: the user text is SITE.md, rendered
  as the site.
-->
# Track Kommander

> ## [→ w0ts.github.io/Track-Kommander](https://w0ts.github.io/Track-Kommander/)
> **What Track Kommander is, how to install it, matching and the CDJ export: on the site.**
> This README is for developers.

Track Kommander runs inside Traktor Pro 4: it ranks your library against the deck you choose and
shows the matches in Traktor's own list, and it writes CDJ-ready USB sticks from your Traktor
collection. Installers are on the
[releases page](https://github.com/w0ts/Track-Kommander/releases/latest).

## What is where

| | |
|---|---|
| `src/core/` | analysis, matching, the library database, the USB export — no Qt |
| `src/ui/`, `src/workers/` | the Qt pages and background work TK shows inside Traktor |
| `traktor-native/src/` | **libtk** for macOS: the library inside Traktor that draws TK's band, its tree entry and list, and talks to TK's Python (Objective-C++, frida-gum) |
| `traktor-native/win/` | libtk.dll and `tk_launch.exe` for Windows (C++, MSVC) |
| `traktor-native/re/` | the kit (Python + TK for inside Traktor), the macOS installer and the release disk image |
| `tests/` | the unit tests |

libtk works with one exact Traktor build at a time (4.5.1, build 21): it uses that build's
function addresses (`traktor-native/src/addrs_4_5_1_21.h`, `traktor-native/win/src/addrs_win_*`)
and refuses any other. Each Traktor release needs a new address map.

## Build and test

Python 3.13 and [uv](https://docs.astral.sh/uv/):

```
make venv        # .venv with TK's packages (EXTRAS="--extra dev" for tests only)
make check       # lockfile, ruff, pyright (src/core), suppression ratchet, tests
make models      # the CLAP model, exported to ONNX (needs the "build" extra: torch)
```

macOS (Apple Silicon, Xcode command-line tools):

```
make libtk       # traktor-native/build/libtk.dylib, ad hoc signed (pinned frida-gum)
make release     # dist/Track Kommander.dmg: kit + libtk + installer
bash traktor-native/re/make_slim_app.sh <out-dir>   # a development app running this checkout
```

Windows (Visual Studio 2022, x64 tools): `traktor-native\win\build.bat` builds libtk.dll and
`tk_launch.exe`; `tk-embed.conf.example` shows how to point them at a checkout.

## Contributing

Bug reports and feature requests go to the
[issue tracker](https://github.com/w0ts/Track-Kommander/issues). Please attach the logs (see
[Troubleshooting](https://w0ts.github.io/Track-Kommander/install.html#troubleshooting)) and say
which system and Traktor version you are on.

---

## Licence and attribution

Track Kommander is **free software**: you can share and change it under the terms of the
[GNU General Public License, version 3](LICENSE). Copyright © 2026 w0ts.

It is built on open-source components, which keep their own licences: Qt (PySide6), FFmpeg,
libsndfile and soxr under the LGPL, the rest permissive. FFmpeg is an audio-only build free of
GPL components (`scripts/build_ffmpeg_lgpl.sh`). [THIRD_PARTY.md](THIRD_PARTY.md) walks through
the whole audit — every dependency, every licence, and which machine-learning models are bundled.

### Not affiliated with Native Instruments

Track Kommander is an independent project. It is **not affiliated with, endorsed by, or
sponsored by Native Instruments GmbH**. "Traktor", "Traktor Pro" and "Traktor Kontrol" are their
trademarks, used here only to describe the software this tool works with. "Rekordbox" and "CDJ"
are trademarks of AlphaTheta Corporation. Track Kommander uses Discogs' API but is not
affiliated with, sponsored or endorsed by Discogs; "Discogs" is a trademark of Zink Media, LLC.

No Native Instruments file is in this repository or in the releases. Track Kommander works with
the copy of Traktor installed on the user's machine.
