"""What the bridge's server accepts, as plain functions.

The server listens on loopback only, which keeps the network out but not the
user's own browser: a web page can send requests to 127.0.0.1 too. Two things
a browser cannot avoid give it away.

* **The Host it asks for.** The bridge asks for ``127.0.0.1`` or, after a
  loss on macOS, ``tkN.localhost``. A page that reaches the server by pointing
  its own domain at loopback (DNS rebinding) asks for that domain.
* **The headers it adds.** A browser says where a request comes from —
  ``Origin``, ``Sec-Fetch-Site`` — and a page cannot remove them. Traktor's
  Qt client sends neither.

A shared secret between the app and the bridge would be stronger, and would
also keep out other programs on the same machine. It needs the bridge files
in Traktor to change, so it is a separate step.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

#: The most a post may hold. The largest the bridge sends is the full state
#: of four decks and four channels, a few kilobytes.
MAX_BODY_BYTES = 256 * 1024

#: The decks Traktor has.
DECKS = ("A", "B", "C", "D")

#: The most of a field from a request that goes into one log line.
_LOG_FIELD_CHARS = 120

_LOOPBACK_NAMES = frozenset({"127.0.0.1", "localhost", "::1"})
_PORT = re.compile(r":\d{1,5}$")
_LAYOUT_NAME = re.compile(r"[A-Za-z0-9_]{1,64}")
_DIGITS = re.compile(r"\d+")


def is_loopback_host(host: str | None) -> bool:
    """Is a ``Host`` header's value a name for this machine's loopback?

    No header at all counts: only an HTTP/1.0 client omits it, and a browser
    is never one.
    """
    if not host:
        return True
    name = _PORT.sub("", host.strip().lower())
    if name.startswith("[") and name.endswith("]"):
        name = name[1:-1]
    # Every name under .localhost resolves to loopback (RFC 6761), which is
    # what the bridge's tkN.localhost relies on.
    return name in _LOOPBACK_NAMES or (
        name.endswith(".localhost")
        and re.fullmatch(r"[a-z0-9-]+", name[: -len(".localhost")]) is not None
    )


def sent_by_a_web_page(headers: Mapping[str, str]) -> bool:
    """Do a request's headers say a browser sent it on a page's behalf?

    Any ``Origin`` counts, a loopback one included: the bridge sends none,
    and nothing else has a reason to talk to this server from a page.
    """
    if headers.get("Origin") is not None:
        return True
    site = headers.get("Sec-Fetch-Site")
    return site is not None and site.strip().lower() != "none"


def body_length(raw: str | None) -> int:
    """The body length a ``Content-Length`` header declares.

    Raises :class:`ValueError` if it is not a plain count, and
    :class:`OverflowError` if it is more than :data:`MAX_BODY_BYTES`.
    """
    if raw is None:
        return 0
    text = raw.strip()
    if not _DIGITS.fullmatch(text):
        raise ValueError(f"not a length: {raw!r}")
    length = int(text)
    if length > MAX_BODY_BYTES:
        raise OverflowError(f"{length} bytes is more than {MAX_BODY_BYTES}")
    return length


def is_deck(deck: object) -> bool:
    return isinstance(deck, str) and deck in DECKS


def is_layout_name(name: object) -> bool:
    """Is *name* something Traktor could call a layout — one short word?

    It is written to the settings, so it is not taken as it comes.
    """
    return isinstance(name, str) and _LAYOUT_NAME.fullmatch(name) is not None


def clean_for_log(text: object) -> str:
    """A field from a request, fit to go into one log line.

    Line breaks would let a request write log lines of its own, in a file
    users send in with a bug report.
    """
    if text is None:
        return ""
    flat = "".join(c if c.isprintable() else " " for c in str(text))
    if len(flat) > _LOG_FIELD_CHARS:
        return flat[:_LOG_FIELD_CHARS] + "…"
    return flat
