"""Traktor's audio device per situation: no auto-switch to the S4, remembered manual picks."""

from __future__ import annotations

from pathlib import Path

from workers.native_audio import (
    AudioSituations,
    NativeAudio,
    is_s4,
    parse_devices,
    situation_of,
)

MAC = ("MacBook Pro Speakers", 0, 2)
RME = ("Fireface UFX II (23880588)", 30, 30)
S4 = ("Traktor Kontrol S4 MK2", 2, 4)
MIC = ("MacBook Pro Microphone", 1, 0)


class Rig:
    """The connected devices, and what Kommander asked Traktor to select."""

    def __init__(self, tmp_path: Path, devices: list) -> None:
        self.devices = list(devices)
        self.activated: list[str] = []
        self.audio = AudioSituations(
            tmp_path / "audio.json",
            lambda: self.devices,
            lambda name: self.activated.append(name) or True,
            lambda: "Fireface UFX II (23880588)",
        )


def test_the_situation_is_the_connected_outputs_in_a_stable_order() -> None:
    assert situation_of([RME, MIC, MAC]) == situation_of([MAC, RME])
    assert situation_of([MAC, RME]) != situation_of([MAC, RME, S4])


def test_plugging_in_the_s4_does_not_switch_to_it(tmp_path) -> None:
    rig = Rig(tmp_path, [MAC, RME])
    rig.devices.append(S4)
    assert rig.audio.decide(S4[0], "auto") is False
    assert rig.audio.current == RME[0]


def test_other_automatic_switches_pass_when_nothing_is_remembered(tmp_path) -> None:
    rig = Rig(tmp_path, [MAC, RME])
    assert rig.audio.decide(MAC[0], "auto") is True


def test_a_manual_pick_is_remembered_for_its_situation_and_wins_later(tmp_path) -> None:
    rig = Rig(tmp_path, [MAC, RME, S4])
    assert rig.audio.decide(S4[0], "manual") is True  # the User wants the S4 in this setup
    assert rig.audio.decide(S4[0], "auto") is True
    assert rig.audio.decide(MAC[0], "auto") is False  # Traktor may not move away from it

    # Another session, same setup: the memory was saved.
    again = Rig(tmp_path, [MAC, RME, S4])
    assert again.audio.remembered() == S4[0]


def test_startup_and_kommanders_own_switches_always_pass(tmp_path) -> None:
    rig = Rig(tmp_path, [MAC, RME, S4])
    rig.audio.decide(RME[0], "manual")
    assert rig.audio.decide(S4[0], "startup") is True
    assert rig.audio.decide(MAC[0], "kommander") is True


def test_a_known_setup_gets_its_device_back_when_it_is_connected(tmp_path) -> None:
    rig = Rig(tmp_path, [MAC, RME])
    rig.audio.decide(RME[0], "manual")  # desk setup: the RME
    rig.devices = [MAC]
    rig.audio.poll()
    rig.audio.decide(MAC[0], "manual")  # on the go: the speakers
    rig.devices = [MAC, RME]
    rig.audio.poll()
    assert rig.activated == [RME[0]]  # back at the desk: the RME again
    rig.audio.poll()
    assert rig.activated == [RME[0]]  # only on a change


def test_an_unknown_setup_selects_nothing(tmp_path) -> None:
    rig = Rig(tmp_path, [MAC, RME])
    rig.audio.poll()
    assert rig.activated == []


def test_libtk_device_lines_parse_and_bad_ones_are_skipped() -> None:
    text = "Fireface UFX II (23880588)\tRME-1\t30\t30\nbroken\nMic\tm\tx\t0\n"
    assert parse_devices(text) == [("Fireface UFX II (23880588)", 30, 30)]


def test_s4_detection_and_outside_traktor(tmp_path) -> None:
    assert is_s4("Traktor Kontrol S4 MK2") and not is_s4("Fireface UFX II")
    assert NativeAudio.attach(tmp_path / "a.json") is None


def test_inside_traktor_tk_is_never_taken_for_a_frozen_app(monkeypatch) -> None:
    """Traktor's executable sits in an .app: TK used to take Traktor's bundle for its own."""
    import sys

    from core import runtime

    monkeypatch.setattr(sys, "executable", "/x/TraktorCopy.app/Contents/MacOS/Traktor Pro 4")
    monkeypatch.delenv("TK_EMBED", raising=False)
    assert runtime.is_frozen()  # the old reading
    monkeypatch.setenv("TK_EMBED", "/repo/traktor-native/re/embed_tk.py")
    assert not runtime.is_frozen()
