# traktor-native

What puts Track Kommander inside Traktor Pro 4.

- `src/` — **libtk.dylib** (macOS, Apple Silicon). Loaded by "Traktor Pro 4 TK", it hooks
  Traktor's browser with [frida-gum](https://frida.re): the band above the browser, the "Track
  Kommander" tree entry, TK's results in Traktor's list with their columns, the matching cards.
  It starts TK's Python inside Traktor and takes TK's commands on a local port (127.0.0.1:8767).
  Build: `make libtk` from the repo root.
- `win/` — the same for Windows: **libtk.dll** and **tk_launch.exe**, which starts the installed
  Traktor with libtk loaded (Traktor's files are never changed). Build: `win\build.bat`.
- `re/` — `make_kit.sh` (Python, TK's packages and source, trimmed by `trim_kit.py` and checked
  by `smoke_kit.py`), `embed_tk.py` (TK's start inside Traktor), the macOS installer
  (`dmg/Install Track Kommander.command`: makes "Traktor Pro 4 TK.app" on the user's Mac from
  their own Traktor) and `make_release_dmg.sh`; `make_slim_app.sh` makes a development app that
  runs a checkout.

Supported Traktor build: 4.5.1 (build 21). The addresses libtk uses are in
`src/addrs_4_5_1_21.h` and `win/src/addrs_win_*.h`; the installer and the launcher recognise the
build by its executable's SHA-256 (`re/dmg/supported-builds.txt`, `win/src/tk_supported.h`).
