"""Capture Traktor's window and ask Claude what it shows.

The screenshot half reuses ``core.drag.capture_traktor_window`` (Quartz). The
verification half POSTs the PNG + a structured prompt to the Anthropic
Messages API and parses the JSON response.

Auth: set ``ANTHROPIC_API_KEY`` in the environment.

Usage:
    from tests.integration.traktor_vision import inspect_traktor
    state = inspect_traktor()
    assert state["deck_a"]["loaded"]
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

# Make ``src`` importable so we can reuse the existing capture helper.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "src"))

from core.drag import capture_traktor_window  # noqa: E402

_log = logging.getLogger(__name__)

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_MODEL = "claude-haiku-4-5-20251001"

DEFAULT_SCHEMA: dict[str, Any] = {
    "deck_a": {
        "loaded": "bool",
        "title": "string|null",
        "artist": "string|null",
        "bpm": "number|null",
        "playing": "bool",
    },
    "deck_b": {
        "loaded": "bool",
        "title": "string|null",
        "artist": "string|null",
        "bpm": "number|null",
        "playing": "bool",
    },
    "deck_c": {
        "loaded": "bool",
        "title": "string|null",
        "artist": "string|null",
        "bpm": "number|null",
        "playing": "bool",
    },
    "deck_d": {
        "loaded": "bool",
        "title": "string|null",
        "artist": "string|null",
        "bpm": "number|null",
        "playing": "bool",
    },
}

_PROMPT = (
    "You are inspecting a screenshot of Traktor Pro 4. For each deck visible, "
    "report whether it has a track loaded, the title and artist as shown on the "
    "deck header, the displayed BPM, and whether the deck is currently playing "
    "(play button lit / waveform scrolling). If a deck is not visible or cannot "
    "be read, set its fields to null and loaded=false. Decks are labeled A, B, "
    "C, D. Return ONLY a JSON object matching this schema (no prose, no "
    "markdown fence):\n\n{schema}"
)


class VisionError(RuntimeError):
    """Raised when capture or verification fails."""


def capture_png() -> bytes:
    """Capture Traktor's main window and return PNG bytes.

    Raises VisionError if Traktor isn't running or capture fails.
    """
    result = capture_traktor_window()
    if result is None:
        raise VisionError("Traktor window not found — is Traktor Pro running and on screen?")
    qimage, _bounds = result

    from PySide6.QtCore import QBuffer, QIODevice  # local import: GUI optional

    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    if not qimage.save(buf, "PNG"):
        raise VisionError("Failed to encode Traktor window as PNG")
    return bytes(buf.data())


def save_png(out_path: Path) -> Path:
    """Capture and write to ``out_path`` (creates parents). Returns the path."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(capture_png())
    return out_path


def _post_messages(payload: dict, api_key: str, timeout: float) -> dict:
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        ANTHROPIC_URL,
        data=body,
        headers={
            "x-api-key": api_key,
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        raise VisionError(f"Anthropic API HTTP {e.code}: {detail}") from e
    except urllib.error.URLError as e:
        raise VisionError(f"Anthropic API unreachable: {e}") from e


def _extract_json(text: str) -> dict:
    """Pull the first JSON object out of an LLM response.

    Tolerates stray prose or markdown fences; raises if nothing parseable.
    """
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    candidate = fenced.group(1) if fenced else text
    start = candidate.find("{")
    if start < 0:
        raise VisionError(f"No JSON object in model response: {text!r}")
    decoder = json.JSONDecoder()
    obj, _ = decoder.raw_decode(candidate[start:])
    return obj


def inspect_traktor(
    *,
    schema: dict[str, Any] | None = None,
    prompt: str | None = None,
    model: str = DEFAULT_MODEL,
    save_screenshot_to: Path | None = None,
    api_key: str | None = None,
    timeout: float = 30.0,
) -> dict:
    """Capture Traktor and ask Claude to describe its state.

    Returns a parsed JSON object whose shape matches ``schema`` (defaults to
    DEFAULT_SCHEMA — per-deck loaded/title/artist/bpm/playing).
    """
    schema = schema or DEFAULT_SCHEMA
    key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise VisionError("ANTHROPIC_API_KEY not set — export it before running vision tests")

    png_bytes = capture_png()
    if save_screenshot_to is not None:
        save_screenshot_to.parent.mkdir(parents=True, exist_ok=True)
        save_screenshot_to.write_bytes(png_bytes)

    user_text = (prompt or _PROMPT).format(schema=json.dumps(schema, indent=2))
    payload = {
        "model": model,
        "max_tokens": 1024,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": base64.b64encode(png_bytes).decode(),
                        },
                    },
                    {"type": "text", "text": user_text},
                ],
            }
        ],
    }

    response = _post_messages(payload, key, timeout)
    blocks = response.get("content") or []
    text = next((b.get("text", "") for b in blocks if b.get("type") == "text"), "")
    if not text:
        raise VisionError(f"Empty response from model: {response!r}")
    return _extract_json(text)


if __name__ == "__main__":  # pragma: no cover — manual smoke
    logging.basicConfig(level=logging.INFO)
    out = _REPO_ROOT / "build" / "traktor_screenshot.png"
    state = inspect_traktor(save_screenshot_to=out)
    print(f"Screenshot saved → {out}")
    print(json.dumps(state, indent=2))
