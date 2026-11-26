#!/usr/bin/env python3
"""Track Kommander — EMBEDDED entry point (Mac-only, injected Traktor).

PRIVATE / branch ``feat/traktor-native-re``. See
``traktor-native/notes/embedded-ui-plan.md``. Runs the thin overlay that lives
beside the injected Traktor, reusing ``core/`` + ``workers/``. The shipped
external entry point (``TrackKommander.py``) is untouched.
"""

import multiprocessing
import os

os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")
multiprocessing.freeze_support()

if __name__ == "__main__":
    from ui.embedded_app import main  # after freeze_support and the env above

    multiprocessing.set_start_method("spawn", force=True)
    main()
