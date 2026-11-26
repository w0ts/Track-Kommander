"""Evaluate a Traktor smart playlist's saved query.

A ``SMARTLIST`` node in ``collection.nml`` stores no tracks — only::

    <SEARCH_EXPRESSION VERSION="1" QUERY='$FILEPATH % "(Hri)"'/>

Traktor runs that query live against the collection, so exporting one means
running it ourselves. The grammar, from real collections::

    expression := term (OR term)*
    term       := factor (AND factor)*
    factor     := NOT factor | "(" expression ")" | condition
    condition  := "$" FIELD operator value
    operator   := % | == | != | <= | >= | < | >
    value      := "quoted" | number | bare word

Design rule
-----------

**Anything not understood exactly raises :class:`UnsupportedQuery`.** An
unknown field, an operator that makes no sense for the field, a query that
will not parse — all refuse rather than approximate. A smart playlist quietly
exported with the wrong tracks is worse than one that is missing: the missing
one is noticed while packing, the wrong one in front of an audience.

``$PLAYED`` is the concrete case. It means "played in this session", which is
Traktor runtime state that never reaches the NML, so it is refused rather than
treated as "never played" — which would export an empty playlist that looks
like it worked.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover — typing only
    from core.rekordbox import Track


class UnsupportedQuery(ValueError):
    """The query cannot be evaluated exactly, so it will not be guessed at."""


#: Traktor stores a rating as 0/51/102/…/255; a query means stars.
RATING_PER_STAR = 51


def _stars(track: Track) -> float:
    return round((track.rating or 0) / RATING_PER_STAR)


#: Field name -> (reader, kind). "text" compares as a case-insensitive string,
#: "number" as a float. A field absent here is refused, not guessed.
FIELDS: dict[str, tuple[Callable[[Track], object], str]] = {
    "FILEPATH": (lambda t: t.path, "text"),
    "TITLE": (lambda t: t.title, "text"),
    "ARTIST": (lambda t: t.artist, "text"),
    "ALBUM": (lambda t: t.album, "text"),
    "GENRE": (lambda t: t.genre, "text"),
    "LABEL": (lambda t: t.label, "text"),
    "COMMENT": (lambda t: t.comment, "text"),
    "IMPORTDATE": (lambda t: t.date_added, "date"),
    "RELEASEDATE": (lambda t: t.release_date, "date"),
    "BPM": (lambda t: t.bpm, "number"),
    "RATING": (_stars, "number"),
    "PLAYCOUNT": (lambda t: t.play_count, "number"),
    "BITRATE": (lambda t: t.bitrate, "number"),
    "LENGTH": (lambda t: t.total_time_s, "number"),
}

#: Recognised but not evaluable, with the reason, so the UI can say why.
UNAVAILABLE = {
    "PLAYED": "whether a track was played this session is Traktor runtime "
    "state and is not stored in the collection",
    "KEY": "Traktor's key field is not carried through the export in a form a query can compare",
}


# ── Parsing ─────────────────────────────────────────────────────────────────

Node = Callable[["Track"], bool]

_TOKEN = re.compile(
    r"""
      \s*(?:
        (?P<reldate>(?:DAYS|WEEKS|MONTHS|YEARS)_AGO\(\d+\))
      | (?P<lparen>\()
      | (?P<rparen>\))
      | (?P<op>==|!=|<=|>=|%|<|>)
      | (?P<field>\$[A-Za-z_][A-Za-z0-9_]*)
      | (?P<string>"[^"]*")
      | (?P<word>[^\s()]+)
      )
""",
    re.VERBOSE,
)


def _tokenise(query: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    pos = 0
    while pos < len(query):
        if query[pos].isspace():
            pos += 1
            continue
        m = _TOKEN.match(query, pos)
        if not m or m.end() == pos:
            raise UnsupportedQuery(f"cannot read {query[pos : pos + 20]!r}")
        kind = m.lastgroup or ""
        out.append((kind, m.group(kind)))
        pos = m.end()
    return out


@dataclass
class _Parser:
    tokens: list[tuple[str, str]]
    pos: int = 0

    def peek(self) -> tuple[str, str] | None:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def take(self) -> tuple[str, str]:
        tok = self.peek()
        if tok is None:
            raise UnsupportedQuery("query ends too early")
        self.pos += 1
        return tok

    #: Traktor writes "and" / "or" as & and | when it saves a smartlist
    #: (seen in 4.5.1: ``$IMPORTDATE > 2026-03-31 & $IMPORTDATE < 2026-07-01``).
    _SYMBOLS = {"AND": "&", "OR": "|"}

    def _is_word(self, word: str) -> bool:
        tok = self.peek()
        return (
            tok is not None
            and tok[0] == "word"
            and tok[1].upper() in (word, self._SYMBOLS.get(word))
        )

    def expression(self) -> Node:
        node = self.term()
        while self._is_word("OR"):
            self.take()
            right = self.term()
            node = (lambda a, b: lambda t: a(t) or b(t))(node, right)
        return node

    def term(self) -> Node:
        node = self.factor()
        while self._is_word("AND"):
            self.take()
            right = self.factor()
            node = (lambda a, b: lambda t: a(t) and b(t))(node, right)
        return node

    def factor(self) -> Node:
        if self._is_word("NOT"):
            self.take()
            inner = self.factor()
            return lambda t: not inner(t)
        tok = self.peek()
        if tok is not None and tok[0] == "lparen":
            self.take()
            node = self.expression()
            closing = self.peek()
            if closing is None or closing[0] != "rparen":
                raise UnsupportedQuery("unbalanced parentheses")
            self.take()
            return node
        return self.condition()

    def condition(self) -> Node:
        kind, text = self.take()
        if kind != "field":
            raise UnsupportedQuery(f"expected a $FIELD, got {text!r}")
        name = text[1:].upper()
        if name in UNAVAILABLE:
            raise UnsupportedQuery(f"${name}: {UNAVAILABLE[name]}")
        if name not in FIELDS:
            raise UnsupportedQuery(f"unknown field ${name}")
        read, kindof = FIELDS[name]

        op_kind, op = self.take()
        if op_kind != "op":
            raise UnsupportedQuery(f"expected an operator, got {op!r}")

        val_kind, raw = self.take()
        if val_kind == "string":
            value: object = raw[1:-1]
        elif val_kind in ("word", "reldate"):
            value = raw
        else:
            raise UnsupportedQuery(f"expected a value, got {raw!r}")

        return _condition(name, read, kindof, op, value)


#: Traktor's "in the last" values, e.g. ``MONTHS_AGO(3)``.
_RELDATE = re.compile(r"^(DAYS|WEEKS|MONTHS|YEARS)_AGO\((\d+)\)$")


def _today() -> date:
    """Today, for "in the last" — a function so tests can pin it."""
    return date.today()


def _months_back(day: date, months: int) -> date:
    """*day* moved back *months* calendar months, clamped to the month end."""
    index = day.year * 12 + (day.month - 1) - months
    year, month = divmod(index, 12)
    month += 1
    next_first = date(year + (month == 12), month % 12 + 1, 1)
    return date(year, month, min(day.day, (next_first - timedelta(days=1)).day))


def _date_value(name: str, value: object) -> date:
    """A query's date operand: ``MONTHS_AGO(3)``, ``2026-01-01`` or ``2026/1/1``."""
    text = str(value).strip()
    rel = _RELDATE.match(text)
    if rel:
        unit, n = rel.group(1), int(rel.group(2))
        today = _today()
        if unit == "DAYS":
            return today - timedelta(days=n)
        if unit == "WEEKS":
            return today - timedelta(weeks=n)
        return _months_back(today, n * (12 if unit == "YEARS" else 1))
    parsed = _parse_date(text)
    if parsed is None:
        raise UnsupportedQuery(f"${name} is a date but {value!r} is not one")
    return parsed


def _parse_date(text: str) -> date | None:
    bits = re.split(r"[-/]", text)
    if len(bits) != 3:
        return None
    try:
        y, m, d = (int(b) for b in bits)
        return date(y, m, d)
    except ValueError:
        return None


def _as_number(value: object) -> float:
    """A field value as a number; an empty one counts as zero."""
    if not value:
        return 0.0
    if isinstance(value, (int, float, str)):
        return float(value)
    return float(str(value))


def _condition(
    name: str, read: Callable[[Track], object], kindof: str, op: str, value: object
) -> Node:
    if kindof == "date" and op != "%":
        wanted_day = _date_value(name, value)
        compare_day = _NUMERIC[op]

        def _on(t: Track) -> bool:
            # A track with no date is in no date range, on either side.
            have = _parse_date(str(read(t) or ""))
            return have is not None and compare_day(have, wanted_day)

        return _on
    if _RELDATE.match(str(value)):
        raise UnsupportedQuery(f"${name} is not a date; {value} needs one")
    if kindof == "number":
        try:
            wanted = float(str(value))
        except ValueError as exc:
            raise UnsupportedQuery(f"${name} is numeric but {value!r} is not a number") from exc
        if op == "%":
            raise UnsupportedQuery(f"${name} is numeric; % needs text")
        compare = _NUMERIC[op]
        return lambda t: compare(_as_number(read(t)), wanted)

    text = str(value)
    if op in ("<", ">", "<=", ">="):
        raise UnsupportedQuery(f"${name} is text; {op} needs a number")
    lowered = text.lower()
    if op == "%":
        return lambda t: lowered in str(read(t) or "").lower()
    if op == "==":
        return lambda t: str(read(t) or "").lower() == lowered
    return lambda t: str(read(t) or "").lower() != lowered


# Compares numbers and dates alike, hence Any operands.
_NUMERIC: dict[str, Callable[[Any, Any], bool]] = {
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
    "<": lambda a, b: a < b,
    ">": lambda a, b: a > b,
    "<=": lambda a, b: a <= b,
    ">=": lambda a, b: a >= b,
}


def parse_query(query: str) -> Node:
    """Compile *query* into a predicate. Raises :class:`UnsupportedQuery`."""
    if not query or not query.strip():
        raise UnsupportedQuery("empty query")
    parser = _Parser(_tokenise(query))
    node = parser.expression()
    trailing = parser.peek()
    if trailing is not None:
        raise UnsupportedQuery(f"trailing {trailing[1]!r} in {query!r}")
    return node


def matches(node: Node, track: Track) -> bool:
    return bool(node(track))


def evaluate(query: str, tracks: list[Track]) -> list[Track]:
    """The tracks a smart playlist would contain, in collection order."""
    node = parse_query(query)
    return [t for t in tracks if node(t)]


def describe(query: str) -> str | None:
    """None if the query can be evaluated, else why it cannot."""
    try:
        parse_query(query)
    except UnsupportedQuery as exc:
        return str(exc)
    return None
