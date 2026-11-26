#!/usr/bin/env python3
"""Track Kommander — entry point."""

import multiprocessing
import os

# Must be set before TF/Essentia are imported anywhere in this process.
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")

# freeze_support() MUST be the very first call in __main__ for PyInstaller
# on macOS so spawned worker processes don't re-launch the full GUI.
# This is a no-op in development and on non-frozen builds.
multiprocessing.freeze_support()

import sys  # noqa: E402

# Before anything can import librosa: in the compiled app numba cannot
# compile, and librosa's numba functions are declared at import time.
# TK_EMULATE_NUMBA=1 applies the same shims in development, for testing.
from core.runtime import is_frozen  # noqa: E402

if is_frozen() or os.environ.get("TK_EMULATE_NUMBA") == "1":
    from core.frozen_runtime import apply_frozen_runtime_shims

    apply_frozen_runtime_shims()

# Headless checks, for a machine where scans or hotcues fail. Main process
# only: a spawned worker re-runs this module with the same arguments.
# Headless check of the audio analysis:
#   TrackKommander --selftest-analysis "/path/to/track.mp3"
if "--selftest-analysis" in sys.argv and __name__ == "__main__":
    from core.selftest import run_analysis_selftest

    _i = sys.argv.index("--selftest-analysis")
    sys.exit(run_analysis_selftest(sys.argv[_i + 1 : _i + 2]))

#   TrackKommander --selftest-hotcues "/path/a.mp3" "/path/b.mp3" …
if "--selftest-hotcues" in sys.argv and __name__ == "__main__":
    from core.selftest import run_hotcue_selftest

    _i = sys.argv.index("--selftest-hotcues")
    sys.exit(run_hotcue_selftest(sys.argv[_i + 1 :]))

# The elevated helper is this same binary. A frozen build has no separate
# python.exe to hand to UAC, and running our signed executable keeps the
# publisher name ours rather than a bare interpreter's. Handled before Qt is
# imported: the helper is plain file work and must not start a GUI.
if "--patch-d2" in sys.argv:
    from core.d2_patch import helper_main

    sys.exit(helper_main(sys.argv))

from ui.main_window import main  # noqa: E402

if __name__ == "__main__":
    # 'spawn' is required on macOS (default since Python 3.8 on macOS, but set
    # explicitly for clarity and PyInstaller compatibility).  Must be called
    # inside the __main__ guard so it only runs in the parent process — worker
    # processes re-import this module and must NOT call set_start_method again.
    multiprocessing.set_start_method("spawn", force=True)
    main()
