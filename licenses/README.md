# Licence texts shipped with the binaries

These files are copied into the distributed app (`.app` bundle / MSI install
folder) under `licenses/`, so every binary carries the full text of every
licence that requires it. See [../THIRD_PARTY.md](../THIRD_PARTY.md) for the
component-by-component audit.

| File | Covers |
|---|---|
| `LICENSE.txt` | Track Kommander itself — GPL-3.0. Built from the repository's root `LICENSE`. |
| `LGPL-3.0.txt` | PySide6 / shiboken6 (Qt), taken under the LGPL-3.0 option of their tri-licence. |
| `GPL-3.0.txt` | Track Kommander's own licence (the same text as `LICENSE.txt`), and the base LGPL-3.0 is drafted on. |
| `LGPL-2.1.txt` | soxr (libsoxr), libsndfile via soundfile, FFmpeg via PyAV (the audio-only LGPL build in `vendor/wheels/`). |

`LICENSE.txt` is not stored here — the build maps the repository's root
`LICENSE` onto that name so the two can never drift. `THIRD_PARTY.md` is copied
in beside them for the same reason. Wiring: `--include-data-*` flags in
[`scripts/_build_app.sh`](../scripts/_build_app.sh) (macOS) and
[`scripts/_build_app.ps1`](../scripts/_build_app.ps1) (Windows); the WiX
harvest in `scripts/wix/track-kommander.wxs` picks the folder up automatically
from the Nuitka output. The MSI's licence page (`scripts/wix/license.rtf`) is
the same text as `LICENSE`, in RTF; a test keeps the two in step.

## The relink obligation — do not break it

Both LGPL versions require that a user can replace the LGPL library with their
own build of it. Nuitka ships Qt, libsoxr, libsndfile and FFmpeg as separate
shared libraries (`.dylib` / `.dll`) next to the executable rather than
statically linked into it, which satisfies that requirement as-is.

**Do not switch to a static Qt link** (or statically link any other LGPL
component) without revisiting this — a static link means the obligation has to
be met another way, by shipping enough object code for the user to relink.
