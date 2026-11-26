"""What each setting is called, and what it is when nobody has set it.

Settings live in ``QSettings``, keyed by a string, and come back as whatever
the platform's store made of them — on an ini file, text. Reading one takes
the key, the default and the type, and each of those was written out
wherever a setting was read. This is the one place for them: a
:class:`Pref` per setting, read with :meth:`Pref.get` and written with
:meth:`Pref.set`.

Only settings that are read in more than one place are here. One read in a
single module is fine where it is.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import QSettings


@dataclass(frozen=True)
class Pref[T: (bool, int, str)]:
    """One setting: its key in the store, and its value when unset.

    The default's type is the setting's type. A stored value that cannot be
    read as that type is treated as unset.
    """

    key: str
    default: T

    def get(self, settings: QSettings | None = None) -> T:
        store = QSettings() if settings is None else settings
        kind = type(self.default)
        if kind is bool:
            # Qt does this conversion itself: "false" in an ini file is
            # text, and text that is not empty is true to Python.
            flag: Any = store.value(self.key, self.default, type=bool)
            return flag
        raw: Any = store.value(self.key, self.default)  # Qt's value is untyped
        try:
            return kind(raw)
        except (TypeError, ValueError):
            return self.default

    def set(self, value: T, settings: QSettings | None = None) -> None:
        store = QSettings() if settings is None else settings
        store.setValue(self.key, type(self.default)(value))


#: What loading a track into a deck also does: nothing ("off"), mute and cue
#: it ("play_cue"), or bring it in over a number of bars ("automix", "auto").
AUTOMATION_MODE = Pref("automation_mode", "play_cue")

#: Over how many bars AutoMix raises the incoming deck's volume.
RAMP_BARS = Pref("auto_volume_raise_bars", 4)

#: How far a match's tempo may be from the playing track's, in percent.
#: 0 is no limit.
BPM_WINDOW_PCT = Pref("bpm_restrict_pct", 10)

#: Keep the display from sleeping while a deck is playing.
KEEP_DISPLAY_AWAKE = Pref("keep_display_awake", True)

#: Write the log at debug level.
VERBOSE_LOG = Pref("verbose_log", False)


ALL: tuple[Pref, ...] = (
    AUTOMATION_MODE,
    RAMP_BARS,
    BPM_WINDOW_PCT,
    KEEP_DISPLAY_AWAKE,
    VERBOSE_LOG,
)
