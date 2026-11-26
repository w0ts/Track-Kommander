"""The dialog after an export: the numbers, not a tour of the feature."""

from __future__ import annotations

from dataclasses import dataclass, field

from ui.export_summary import summarise


@dataclass
class _Report:
    on_key: int = 0
    added: int = 0
    replaced: int = 0
    unchanged: int = 0
    kept: int = 0
    copied: int = 0
    missing: list = field(default_factory=list)
    errors: list = field(default_factory=list)


@dataclass
class _Outcome:
    report: _Report
    stopped: bool = False
    orphans_found: int = 0
    orphans_bytes: int = 0
    orphans_removed: int = 0
    no_waveform: list = field(default_factory=list)


# A track whose audio could not be read was exported with an empty waveform
# and nothing said so. The player shows a flat line for it; the person who
# ran the export should hear about it here, not find out at the gig.


def test_tracks_without_a_waveform_are_named() -> None:
    out = _Outcome(_Report(on_key=3, added=3), no_waveform=["broken.mp3", "also broken.mp3"])
    _status, text = summarise(out, playlists=0, dest_name="Key")
    assert (
        "2 tracks have no waveform — their audio could not be read:\nbroken.mp3\nalso broken.mp3"
    ) in text


def test_one_track_without_a_waveform_is_singular() -> None:
    out = _Outcome(_Report(on_key=3, added=3), no_waveform=["broken.mp3"])
    _status, text = summarise(out, playlists=0, dest_name="Key")
    assert "1 track has no waveform — its audio could not be read:" in text


def test_a_long_list_is_cut_short() -> None:
    out = _Outcome(_Report(on_key=30, added=30), no_waveform=[f"t{i}.mp3" for i in range(25)])
    _status, text = summarise(out, playlists=0, dest_name="Key")
    assert "25 tracks have no waveform" in text
    assert "t9.mp3" in text and "t10.mp3" not in text
    assert "and 15 more" in text


def test_the_plain_case_is_two_short_lines() -> None:
    out = _Outcome(_Report(on_key=872, added=0, replaced=725, unchanged=147))
    status, text = summarise(out, playlists=13, dest_name="Key")
    assert status == "Key: 872 tracks, 13 playlists."
    assert text == ("Key: 872 tracks, 13 playlists.\n\nUpdated 725, unchanged 147.")


def test_every_kind_of_change_is_named_once() -> None:
    out = _Outcome(_Report(on_key=10, added=3, replaced=2, unchanged=4, kept=1), orphans_removed=5)
    _status, text = summarise(out, playlists=1, dest_name="Key")
    assert text.splitlines()[-1] == "Added 3, updated 2, unchanged 5, deleted 5."


def test_zero_counts_are_not_mentioned() -> None:
    out = _Outcome(_Report(on_key=3, added=3))
    _status, text = summarise(out, playlists=0, dest_name="Key")
    assert text == "Key: 3 tracks, 0 playlists.\n\nAdded 3."


def test_orphans_left_behind_are_one_line() -> None:
    out = _Outcome(_Report(on_key=3, added=3), orphans_found=11, orphans_bytes=170_000_000)
    _status, text = summarise(out, playlists=0, dest_name="Key")
    assert "11 files on the key are not in its database (0.17 GB)." in text
    assert "Replace" not in text


def test_a_stopped_export_says_so_in_the_status_too() -> None:
    out = _Outcome(_Report(on_key=3, added=3), stopped=True)
    status, text = summarise(out, playlists=0, dest_name="Key")
    assert status.endswith("(stopped early)")
    assert "Stopped early" in text


def test_problems_are_listed() -> None:
    out = _Outcome(_Report(on_key=3, added=3, missing=["a", "b"], errors=["x: boom"]))
    _status, text = summarise(out, playlists=0, dest_name="Key", skipped=2)
    assert "2 missing from disk." in text
    assert "2 playlist entries skipped." in text
    assert text.endswith("1 problem:\nx: boom")
