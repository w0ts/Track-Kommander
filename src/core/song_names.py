"""A song's name without its version: what makes two titles the same song, and who remixed one.

Used by the Spotify search (core.spotify_api) to tell the right track from its other versions,
and by add-ons that compare titles.
"""

from __future__ import annotations

import re
import unicodedata

from core import artists

#: Brackets (a version: "(Extended Mix)", "[Basstrick Remix)" — closed by
#: either), a " - …" suffix ("Plastico Fino - Rendher Remix") and a bare
#: " featuring …" ("365 featuring shygirl").
_VERSION = re.compile(
    r"\s*[(\[][^)\]]*[)\]]?|\s+-\s.*$|\s+(?:feat\.?|ft\.?|featuring)\s.*$", re.IGNORECASE
)
#: A version named after the title without brackets ("The Dance Extended
#: Mix"), read off the normalised title's end.
_BARE_VERSION = re.compile(
    r"\s+(extended|original|radio|club|dub|instrumental|vip)(\s+(mix|edit|version))?$"
)
#: How alike two cleaned titles must be to count as one song ("Plactic
#: Fino" for "Plastico Fino": a typo in the file's tags).
SAME_TITLE = 0.85

#: A version that is another recording: "(Toobris Remix)", "- JKS Rework".
#: Extended, original and radio versions are the same recording.
_REMIX = re.compile(
    r"([^()\[\]\-]*?)\b(remix|rework|refix|re-edit|edit|dub|vip|bootleg|flip|cover)\b",
    re.IGNORECASE,
)
_SAME_RECORDING = {"radio", "extended", "original", "club", "album"}


def normalise(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def song_title(title: str, artist: str = "") -> str:
    """The title without its version and without the artist the file put in
    front of it: "Matroda - True G (feat. BRUX) [Basstrick Remix)" -> "true g"."""
    head, sep, rest = title.partition(" - ")
    if sep and artists.key(head) in artists.names(artist):
        title = rest
    return _BARE_VERSION.sub("", normalise(_VERSION.sub("", title)))


def remix_of(title: str) -> str:
    """Who remixed it, and how ("toobris remix"); "" for the original —
    an extended or radio edit included."""
    for m in _REMIX.finditer(title.replace("_", " ")):
        who = normalise(m.group(1))
        if who.split()[-1:] and who.split()[-1] in _SAME_RECORDING:
            continue
        return f"{who} {m.group(2).lower()}".strip()
    return ""
