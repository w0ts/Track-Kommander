"""A headless check that audio analysis works on this machine.

``TrackKommander --selftest-analysis "/path/to/track.mp3"`` runs the signal,
melodic and structure analysis on one file and prints what came out, then
exits — no window, no database. For a machine where every scan comes back
empty: the output names the step that fails, with its traceback.
"""

from __future__ import annotations

import json
import sys
import traceback


def run_analysis_selftest(args: list[str]) -> int:
    """Analyse ``args[0]``; print the features as JSON. 0 on success."""
    if not args:
        print("usage: --selftest-analysis <audio file>", file=sys.stderr)
        return 2
    path = args[0]
    from core.runtime import is_frozen

    print(f"frozen: {is_frozen()}")
    try:
        import librosa

        print(f"librosa {librosa.version.version} imported")
        from core.analysis import compute_signal_features

        features, timings = compute_signal_features(
            path, want_signal=True, want_melodic=True, want_shape=True, bpm=128.0
        )
    except Exception:
        traceback.print_exc()
        return 1
    print(
        json.dumps(
            {
                k: v
                for k, v in sorted(features.items())
                if isinstance(v, (int, float, str, type(None)))
            },
            indent=1,
        )
    )
    wanted = ("danceability", "melodic", "sameness", "onset_density")
    missing = [k for k in wanted if features.get(k) is None]
    print("OK" if not missing else f"MISSING: {', '.join(missing)}")
    return 0 if not missing else 1


def run_hotcue_selftest(args: list[str], bpm: float = 128.0) -> int:
    """Detect hotcues on each file in ``args`` through the real worker pool.

    Same path as the Hotcues button: ``detect_one`` in spawned worker
    processes — which, in the compiled app, are fresh copies of the app, so
    this checks the shims reach them too. Prints each file's cues, or why it
    has none. 0 if every file was analysed without an error.
    """
    if not args:
        print("usage: --selftest-hotcues <audio file> [more files…]", file=sys.stderr)
        return 2
    from concurrent.futures import ProcessPoolExecutor, as_completed

    from core.hotcue_job import detect_one

    failed = 0
    with ProcessPoolExecutor(max_workers=min(4, len(args))) as pool:
        futures = {pool.submit(detect_one, path, bpm, 0.0, 7): path for path in args}
        for future in as_completed(futures):
            path = futures[future]
            try:
                _p, cues, reason = future.result()
            except Exception:
                failed += 1
                print(f"FAILED {path}")
                traceback.print_exc()
                continue
            if cues:
                marks = ", ".join(f"{label} @ {ms / 1000:.1f}s" for ms, label, _t in cues)
                print(f"{len(cues)} cue(s)  {path}\n    {marks}")
            else:
                print(f"no cues ({reason})  {path}")
    print("OK" if not failed else f"FAILED: {failed}")
    return 0 if not failed else 1
