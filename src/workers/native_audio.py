"""Traktor's audio device, chosen per situation (integrated mode).

A *situation* is the set of audio output devices connected right now (the Mac's
speakers, an RME, an S4 MK2 …). When the User picks a device by hand in
Traktor's Preferences, it is remembered for that situation. From then on:

* Traktor's own automatic switches (plugging in a controller with its own
  sound card, a changed system default) are vetoed when they go against what
  the User picked for the situation — and, with nothing remembered yet, never
  onto an S4 MK2 (the User: "do not auto switch to s4 mk2 when i plug it in");
* when the connected set changes to a situation with a remembered device, that
  device is selected — the way a pick in Preferences does it.

libtk (``tk_audio.mm``) classifies every switch Traktor makes (manual, startup,
Kommander's own, automatic) and asks :meth:`AudioSituations.decide` first.
"""

from __future__ import annotations

import ctypes
import json
import logging
from collections.abc import Callable
from pathlib import Path

from workers.native_lib import process_lib

log = logging.getLogger(__name__)

POLL_MS = 2000

Device = tuple[str, int, int]  # (name, input channels, output channels)


def situation_of(devices: list[Device]) -> str:
    """The situation's key: the connected output devices, by name, in a stable order."""
    return "\n".join(sorted({name for name, _ins, outs in devices if outs > 0}))


def is_s4(name: str) -> bool:
    return "kontrol s4" in name.lower()


class AudioSituations:
    """Remembers a device per situation and decides on Traktor's device switches."""

    def __init__(
        self,
        store: Path,
        devices: Callable[[], list[Device]],
        activate: Callable[[str], bool],
        current: Callable[[], str] = lambda: "",
    ) -> None:
        self._store = store
        self._devices = devices
        self._activate = activate
        self.current = current()
        self._memory: dict[str, str] = self._load()
        self._last_situation: str | None = None

    def _load(self) -> dict[str, str]:
        try:
            data = json.loads(self._store.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        situations = data.get("situations") if isinstance(data, dict) else None
        return {str(k): str(v) for k, v in (situations or {}).items()}

    def _save(self) -> None:
        try:
            self._store.parent.mkdir(parents=True, exist_ok=True)
            self._store.write_text(
                json.dumps({"situations": self._memory}, indent=1, sort_keys=True), encoding="utf-8"
            )
        except OSError as exc:
            log.warning("audio situations not saved: %s", exc)

    def remembered(self, situation: str | None = None) -> str | None:
        key = situation_of(self._devices()) if situation is None else situation
        return self._memory.get(key)

    def decide(self, device: str, reason: str) -> bool:
        """Traktor is about to switch to *device* for *reason*: allow it?"""
        situation = situation_of(self._devices())
        if reason == "manual":
            self._memory[situation] = device
            self._save()
            log.info("audio: %r picked by hand; remembered for this setup", device)
            allow = True
        elif reason in ("startup", "kommander"):
            allow = True
        else:
            wanted = self._memory.get(situation)
            if wanted is not None:
                allow = device == wanted
            else:
                allow = not is_s4(device)
            if not allow:
                log.info("audio: Traktor's switch to %r vetoed (keeping %r)", device, self.current)
        if allow:
            self.current = device
        return allow

    def poll(self) -> None:
        """The connected set changed? Then select the device remembered for it, if any."""
        devices = self._devices()
        situation = situation_of(devices)
        if situation == self._last_situation:
            return
        self._last_situation = situation
        wanted = self._memory.get(situation)
        present = {name for name, _ins, outs in devices if outs > 0}
        if wanted and wanted in present and wanted != self.current:
            log.info("audio: this setup was last played on %r: selecting it", wanted)
            if self._activate(wanted):
                self.current = wanted


# ── libtk binding ────────────────────────────────────────────────────────────

_DECIDER = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_char_p, ctypes.c_char_p)


class NativeAudio:
    """AudioSituations wired to libtk inside Traktor. Keep a reference: it owns the callback."""

    def __init__(self, lib: ctypes.CDLL, store: Path) -> None:
        lib.tk_audio_devices.argtypes = [ctypes.c_char_p, ctypes.c_int]
        lib.tk_audio_devices.restype = ctypes.c_int
        lib.tk_audio_activate.argtypes = [ctypes.c_char_p]
        lib.tk_audio_activate.restype = ctypes.c_int
        lib.tk_audio_device_get.argtypes = [ctypes.c_char_p, ctypes.c_int]
        lib.tk_audio_device_get.restype = ctypes.c_int
        lib.tk_audio_set_decider.argtypes = [_DECIDER]
        lib.tk_audio_set_decider.restype = None
        self._lib = lib
        self.situations = AudioSituations(store, self.devices, self.activate, self.device_setting)
        self._c_decider = _DECIDER(self._decide)
        lib.tk_audio_set_decider(self._c_decider)

    @classmethod
    def attach(cls, store: Path) -> NativeAudio | None:
        lib = process_lib()
        if lib is None or not hasattr(lib, "tk_audio_set_decider"):
            return None
        return cls(lib, store)

    def detach(self) -> None:
        self._lib.tk_audio_set_decider(_DECIDER())

    def devices(self) -> list[Device]:
        need = int(self._lib.tk_audio_devices(None, 0))
        buf = ctypes.create_string_buffer(max(need, 0) + 1)
        self._lib.tk_audio_devices(buf, len(buf))
        return parse_devices(buf.value.decode("utf-8", errors="replace"))

    def device_setting(self) -> str:
        buf = ctypes.create_string_buffer(256)
        return buf.value.decode() if self._lib.tk_audio_device_get(buf, 256) >= 0 else ""

    def activate(self, name: str) -> bool:
        return self._lib.tk_audio_activate(name.encode()) == 0

    def _decide(self, device: bytes, reason: bytes) -> int:
        # Called from C on Traktor's main thread: an exception must not escape into libtk.
        try:
            return int(self.situations.decide(device.decode(), reason.decode()))
        except Exception:
            log.exception("audio decision failed: allowing Traktor's switch")
            return 1


def parse_devices(text: str) -> list[Device]:
    """libtk's device lines ("name\\tUID\\tins\\touts") → (name, ins, outs)."""
    out: list[Device] = []
    for line in text.splitlines():
        f = line.split("\t")
        if len(f) == 4 and f[0]:
            try:
                out.append((f[0], int(f[2]), int(f[3])))
            except ValueError:
                continue
    return out
