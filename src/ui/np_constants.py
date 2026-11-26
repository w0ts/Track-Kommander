"""Constants shared by the Now Playing tab and the mixins split out of it.

They sit in one module so ``np_auto``, ``np_results`` and the others can read
the same paging size, deck letters and automation modes without importing each
other (or the tab, which would be a cycle). ``ui.now_playing`` re-exports the
ones outside callers use, ``NO_TRANSPOSE`` among them.
"""

from __future__ import annotations

import sys

_PAGE = 50  # rows loaded per scroll-triggered batch
#: Load the next page once the view is this many rows from the loaded end.
_PREFETCH_ROWS = 10
#: Quiet time after the last keystroke before the filter runs.
_FILTER_DEBOUNCE_MS = 120
#: The lead text columns the composite-score bar runs behind.
_SCORE_BAR_COLS = ("artist", "album", "title")


PLAYED_THRESHOLD_S = (
    60.0  # seconds of cumulative playtime before a track counts as "played this session"
)

_DECK_LETTERS = ("A", "B", "C", "D")

#: "Leave the Key knob alone" in the load signal — a Qt signal cannot carry
#: None through an int. Only used by callers that have no view of the axis;
#: the panel always sends a number, 0 included, so a deck never inherits the
#: previous track's transpose.
NO_TRANSPOSE = -99


#: The row menu's reveal entry, named after the file manager it opens.
_REVEAL_LABEL = {"darwin": "Show in Finder", "win32": "Show in Explorer"}.get(
    sys.platform, "Show in file manager"
)


#: The side strip's icons are all rendered into this box, so a colour emoji
#: and a plain × come out the same size.
_SIDE_ICON_PX = 22

# Automation modes: (settings_key, button_icon, icon_color, tooltip)
# Cycle order: off → play_cue → automix → off …
_AUTO_MODES = (
    (
        "off",
        "⊘",
        "#555577",
        "Automation: Off\nTracks load without touching the mixer or transport.",
    ),
    (
        "play_cue",
        "🎧",
        "#bdf164",  # T.GREEN
        "Automation: Play + Cue\n"
        "  • Silence channel fader before load\n"
        "  • Switch headphone pre-listen to target deck\n"
        "  • Start playback with beat-sync",
    ),
    (
        "automix",
        "▶",
        "#bdf164",  # T.GREEN
        "Automation: AutoMix\n"
        "  • Silence channel fader before load\n"
        "  • Switch headphone pre-listen to target deck\n"
        "  • Start playback with beat-sync\n"
        "  • Ramp volume to full over the transition length set in Settings",
    ),
    (
        "auto",
        "🪄",
        "#bdf164",  # green when armed; orange when transition in flight
        "Automation: Auto DJ\n"
        "  • Monitors remaining time on the active deck\n"
        "  • Loads the next track automatically before the current one ends\n"
        "  • Picks randomly from the top matches, skipping already-played tracks\n"
        "  • Full AutoMix transition (volume ramp in, bass swap, fade out)\n"
        "  • Right-click to cancel an in-progress transition",
    ),
)

_DEFAULT_LOAD_METHOD = "drag"

#: Stopping a playing deck before loading onto it: how long to let the stop
#: reach Traktor and take effect before the drag lands.
_STOP_BEFORE_LOAD_MS = 250
