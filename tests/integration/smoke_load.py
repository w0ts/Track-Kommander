#!/usr/bin/env python3
"""End-to-end smoke test: load a track, then ask Claude what Traktor shows.

Manual prerequisites:
  • Traktor Pro 4 is open and visible (not minimised).
  • ANTHROPIC_API_KEY is exported.
  • macOS Accessibility + Screen Recording permissions are granted to the
    terminal/IDE running this script.

Run:
    python tests/integration/smoke_load.py /path/to/track.mp3 A

Steps:
  1. Captures Traktor BEFORE the load and asks Claude to describe deck state.
  2. Drops the file onto deck A (or whichever deck is given).
  3. Waits a few seconds, captures AFTER, and asks again.
  4. Asserts the chosen deck transitioned from not-loaded → loaded and prints
     the inferred title/artist.

This is a prototype — failures usually mean Traktor's window moved, the proxy
icon trick missed, or vision misread the deck header. Inspect the saved
screenshots in build/ to debug.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT))

from tests.integration.traktor_driver import (  # noqa: E402
    DriverError,
    drop_file_to_deck,
    is_traktor_running,
)
from tests.integration.traktor_vision import (  # noqa: E402
    VisionError,
    inspect_traktor,
)

_log = logging.getLogger("smoke_load")


def _print_state(label: str, state: dict) -> None:
    print(f"\n── {label} " + "─" * (60 - len(label)))
    print(json.dumps(state, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", type=Path, help="Audio file to load")
    parser.add_argument("deck", choices=("A", "B", "C", "D"), help="Target deck")
    parser.add_argument(
        "--settle", type=float, default=4.0, help="Seconds to wait after drop before re-checking"
    )
    parser.add_argument(
        "--no-load", action="store_true", help="Skip the drop step; only inspect current state"
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if not is_traktor_running():
        print("✗ Traktor is not running — start Traktor Pro and retry.")
        return 2
    if not args.file.exists():
        print(f"✗ file not found: {args.file}")
        return 2

    out_dir = _REPO_ROOT / "build" / "integration"
    deck_key = f"deck_{args.deck.lower()}"

    try:
        before = inspect_traktor(save_screenshot_to=out_dir / "before.png")
    except VisionError as e:
        print(f"✗ vision failed before drop: {e}")
        return 1
    _print_state("BEFORE", before)

    if args.no_load:
        return 0

    try:
        drop_file_to_deck(args.file, args.deck)
    except DriverError as e:
        print(f"✗ drop failed: {e}")
        return 1

    print(f"\n…waiting {args.settle:.1f}s for Traktor to settle…")
    time.sleep(args.settle)

    try:
        after = inspect_traktor(save_screenshot_to=out_dir / "after.png")
    except VisionError as e:
        print(f"✗ vision failed after drop: {e}")
        return 1
    _print_state("AFTER", after)

    before_loaded = bool((before.get(deck_key) or {}).get("loaded"))
    after_loaded = bool((after.get(deck_key) or {}).get("loaded"))
    title = (after.get(deck_key) or {}).get("title")

    print("\n── ASSERTION " + "─" * 56)
    if not before_loaded and after_loaded:
        print(f"✓ deck {args.deck} transitioned to loaded — title: {title!r}")
        return 0
    if before_loaded and after_loaded:
        print(f"⚠ deck {args.deck} was already loaded — vision now sees {title!r}")
        return 0
    print(
        f"✗ deck {args.deck} did not become loaded (before={before_loaded}, after={after_loaded})"
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
