"""Working out what actually needs rewriting, so an export can skip the rest.

Measured on real tracks: decoding audio for the waveforms costs ~0.75 s each
and copying costs ~41 MB/s, so on a re-export the decoding is both the larger
half and the avoidable one.

Three states matter, and the middle one is the common case — redoing hotcues
in Traktor does not change a single sample of audio:

======== ===================================================================
NEW      not on the key: copy, analyse, write
FULL     the audio changed: same as new
CUES     only cues or metadata changed — **reuse the waveform already on the
         key** rather than decoding the file again
UNCHANGED nothing changed: touch nothing
======== ===================================================================
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from core import anlz, device_export, pdb_seed
from core.device_export import (
    DB_DIR,
    Change,
    DeviceTrack,
    analysis_plan,
    audio_fingerprint,
    export,
    needs_analysis,
    plan_changes,
    read_manifest,
    track_fingerprint,
)

Database = pytest.importorskip("rekordbox_pdb", reason="rekordbox-pdb not installed").Database

SEED_CANDIDATES = (
    Path(__file__).parent / "data" / "export-seed.pdb",
    Path.home() / "Library/Application Support/Track Kommander/export-seed.pdb",
)


def _seed() -> Path:
    """A blank export.pdb. Found on a stick if there is one, generated if not.

    These used to skip wholesale on any machine without a Rekordbox-prepared
    key — which is every CI machine, so the export went untested there.
    """
    for p in SEED_CANDIDATES:
        if p.is_file():
            return p
    out = Path(tempfile.mkdtemp(prefix="pdb-seed-")) / "export.pdb"
    return pdb_seed.write(out)


@pytest.fixture
def audio(tmp_path):
    soundfile = pytest.importorskip("soundfile")
    np = pytest.importorskip("numpy")
    if "MP3" not in soundfile.available_formats():
        pytest.skip("libsndfile has no MP3 support")
    src = tmp_path / "src"
    src.mkdir()
    out = []
    for i in range(3):
        p = src / f"track{i}.mp3"
        soundfile.write(p, np.zeros(44100, dtype="float32"), 44100, format="MP3")
        out.append(p)
    return out


def _track(p: Path, title: str = "T", **kw) -> DeviceTrack:
    return DeviceTrack(
        source=p, title=title, artist="A", bpm=128.0, duration_s=60, bitrate_kbps=320, **kw
    )


# ── Fingerprints ────────────────────────────────────────────────────────────


def test_the_same_track_fingerprints_the_same(audio) -> None:
    assert track_fingerprint(_track(audio[0])) == track_fingerprint(_track(audio[0]))


def test_a_changed_cue_changes_the_fingerprint(audio) -> None:
    a = _track(audio[0])
    b = _track(audio[0], cues=[anlz.Cue(time_ms=1000, hot_cue=1)])
    assert track_fingerprint(a) != track_fingerprint(b)


def test_changed_metadata_changes_the_fingerprint(audio) -> None:
    assert track_fingerprint(_track(audio[0], title="One")) != track_fingerprint(
        _track(audio[0], title="Two")
    )


def test_a_changed_grid_changes_the_fingerprint(audio) -> None:
    assert track_fingerprint(_track(audio[0])) != track_fingerprint(
        _track(audio[0], first_beat_ms=12.5)
    )


def test_cues_do_not_change_the_audio_fingerprint(audio) -> None:
    """This is what lets a cue-only change skip the decode."""
    a = _track(audio[0])
    b = _track(audio[0], cues=[anlz.Cue(time_ms=1000, hot_cue=1)], title="X")
    assert audio_fingerprint(a.source) == audio_fingerprint(b.source)


def test_a_rewritten_file_changes_the_audio_fingerprint(audio, tmp_path) -> None:
    before = audio_fingerprint(audio[0])
    data = audio[0].read_bytes()
    audio[0].write_bytes(data + b"\x00" * 16)
    assert audio_fingerprint(audio[0]) != before


def test_audio_fingerprint_of_a_missing_file_is_stable(tmp_path) -> None:
    gone = tmp_path / "nope.mp3"
    assert audio_fingerprint(gone) == audio_fingerprint(gone)


# ── Planning ────────────────────────────────────────────────────────────────


def test_everything_is_new_on_a_fresh_key(tmp_path, audio) -> None:
    plan = plan_changes(tmp_path / "usb", [_track(p) for p in audio])
    assert set(plan.values()) == {Change.NEW}


def test_an_untouched_track_is_unchanged(tmp_path, audio) -> None:
    dest = tmp_path / "usb"
    tracks = [_track(audio[0])]
    export(tracks, dest, _seed())
    assert plan_changes(dest, [_track(audio[0])]) == {0: Change.UNCHANGED}


def test_a_new_hotcue_is_a_cues_only_change(tmp_path, audio) -> None:
    dest = tmp_path / "usb"
    export([_track(audio[0])], dest, _seed())
    again = _track(audio[0], cues=[anlz.Cue(time_ms=2000, hot_cue=3)])
    assert plan_changes(dest, [again]) == {0: Change.CUES}


def test_changed_audio_is_a_full_rewrite(tmp_path, audio) -> None:
    dest = tmp_path / "usb"
    export([_track(audio[0])], dest, _seed())
    audio[0].write_bytes(audio[0].read_bytes() + b"\x00" * 32)
    assert plan_changes(dest, [_track(audio[0])]) == {0: Change.FULL}


def test_a_mix_is_planned_per_track(tmp_path, audio) -> None:
    dest = tmp_path / "usb"
    export([_track(audio[0]), _track(audio[1])], dest, _seed())
    plan = plan_changes(
        dest,
        [
            _track(audio[0]),  # unchanged
            _track(audio[1], cues=[anlz.Cue(time_ms=1, hot_cue=1)]),  # cues
            _track(audio[2]),  # new
        ],
    )
    assert plan == {0: Change.UNCHANGED, 1: Change.CUES, 2: Change.NEW}


def test_a_key_with_no_manifest_treats_everything_as_new(tmp_path, audio) -> None:
    """A key written by an older version, or by Rekordbox."""
    dest = tmp_path / "usb"
    export([_track(audio[0])], dest, _seed())
    (dest / DB_DIR / "track-kommander.json").unlink()
    assert plan_changes(dest, [_track(audio[0])]) == {0: Change.NEW}


# ── The manifest ────────────────────────────────────────────────────────────


def test_export_writes_a_manifest(tmp_path, audio) -> None:
    dest = tmp_path / "usb"
    export([_track(audio[0])], dest, _seed())
    manifest = read_manifest(dest)
    assert len(manifest) == 1
    assert next(iter(manifest)).startswith("/Contents/")


def test_the_manifest_survives_a_merge(tmp_path, audio) -> None:
    dest = tmp_path / "usb"
    export([_track(audio[0])], dest, _seed())
    export([_track(audio[1])], dest, _seed(), merge=True)
    assert len(read_manifest(dest)) == 2


def test_a_corrupt_manifest_is_ignored(tmp_path, audio) -> None:
    dest = tmp_path / "usb"
    export([_track(audio[0])], dest, _seed())
    (dest / DB_DIR / "track-kommander.json").write_text("{not json")
    assert plan_changes(dest, [_track(audio[0])]) == {0: Change.NEW}


# ── Reusing the waveform already on the key ─────────────────────────────────


def test_waveform_can_be_read_back_from_the_files(tmp_path, audio) -> None:
    """A cue-only change rewrites the analysis, so the waveform has to come
    from somewhere — and it is already sitting on the key, in exactly the
    quantised form it would be written in again."""
    dest = tmp_path / "usb"
    t = _track(audio[0])
    t.analysis = anlz.Waveform(
        preview=tuple(range(31)) * 12 + (0,) * 28,
        detail=(5, 9, 13) * 40,
        colour=((1, 2, 3), (4, 5, 6), (7, 0, 1)) * 40,
    )
    export([t], dest, _seed())

    db = Database.from_file(str(dest / DB_DIR / "export.pdb"))
    dat = dest / next(iter(db.tracks)).analyze_path.lstrip("/")
    got = anlz.waveform_from_files(dat)

    assert got is not None
    assert got.detail == t.analysis.detail
    assert got.colour == t.analysis.colour
    assert got.preview == t.analysis.preview


def test_waveform_from_missing_files_is_none(tmp_path) -> None:
    assert anlz.waveform_from_files(tmp_path / "nope.DAT") is None


def test_a_cues_only_export_does_not_decode(tmp_path, audio, monkeypatch) -> None:
    """The whole point: adding a hotcue must not re-read the audio."""
    dest = tmp_path / "usb"
    export([_track(audio[0])], dest, _seed())

    calls = []
    real = anlz.analyse_waveform
    monkeypatch.setattr(anlz, "analyse_waveform", lambda p: calls.append(p) or real(p))

    again = _track(audio[0], cues=[anlz.Cue(time_ms=2000, hot_cue=3)])
    report = export([again], dest, _seed(), merge=True)
    assert calls == [], "decoded the audio for a cue-only change"
    assert report.reused == 1


def test_a_cues_only_export_still_writes_the_cue(tmp_path, audio) -> None:
    dest = tmp_path / "usb"
    export([_track(audio[0])], dest, _seed())
    again = _track(audio[0], cues=[anlz.Cue(time_ms=2000, hot_cue=3)])
    report = export([again], dest, _seed(), merge=True)

    db = Database.from_file(str(report.db_path))
    ext = (dest / next(iter(db.tracks)).analyze_path.lstrip("/")).with_suffix(".EXT")
    assert b"\x07\xd0" in ext.read_bytes(), "2000 ms should be in a cue"


def test_a_cues_only_export_keeps_the_waveform(tmp_path, audio) -> None:
    dest = tmp_path / "usb"
    t = _track(audio[0])
    t.analysis = anlz.Waveform(preview=(7,) * 400, detail=(11,) * 120, colour=((2, 4, 6),) * 120)
    export([t], dest, _seed())

    db = Database.from_file(str(dest / DB_DIR / "export.pdb"))
    dat = dest / next(iter(db.tracks)).analyze_path.lstrip("/")
    before = anlz.waveform_from_files(dat)

    again = _track(audio[0], cues=[anlz.Cue(time_ms=2000, hot_cue=3)])
    export([again], dest, _seed(), merge=True)
    assert anlz.waveform_from_files(dat) == before


def test_an_unchanged_track_is_skipped_entirely(tmp_path, audio) -> None:
    dest = tmp_path / "usb"
    export([_track(audio[0])], dest, _seed())
    report = export([_track(audio[0])], dest, _seed(), merge=True)
    assert report.unchanged == 1
    assert report.replaced == 0


# ── What the export panel decides to decode ─────────────────────────────────


def test_a_rebuild_decodes_everything(tmp_path, audio) -> None:
    tracks = [_track(p) for p in audio]
    plan = analysis_plan(
        tmp_path / "usb", tracks, merge=False, refresh_existing=True, reanalyse=False
    )
    assert needs_analysis(plan, tracks) == tracks


def test_a_merge_decodes_only_what_changed(tmp_path, audio) -> None:
    dest = tmp_path / "usb"
    tracks = [_track(p) for p in audio]
    export(tracks, dest, _seed())

    same = [_track(p) for p in audio]
    plan = analysis_plan(dest, same, merge=True, refresh_existing=True, reanalyse=False)
    assert needs_analysis(plan, same) == []


def test_a_cues_only_change_is_not_a_decode(tmp_path, audio) -> None:
    """The waveform on the key is still correct, so the audio is left alone."""
    dest = tmp_path / "usb"
    export([_track(audio[0])], dest, _seed())

    recued = [_track(audio[0], cues=[anlz.Cue(time_ms=2000, hot_cue=3)])]
    plan = analysis_plan(dest, recued, merge=True, refresh_existing=True, reanalyse=False)
    assert plan[0] is Change.CUES
    assert needs_analysis(plan, recued) == []


def test_reanalyse_decodes_tracks_a_merge_would_have_skipped(tmp_path, audio) -> None:
    """The point of the option: the track to re-analyse is normally one the
    export would not have touched at all."""
    dest = tmp_path / "usb"
    tracks = [_track(p) for p in audio]
    export(tracks, dest, _seed())

    same = [_track(p) for p in audio]
    plan = analysis_plan(dest, same, merge=True, refresh_existing=True, reanalyse=True)
    assert needs_analysis(plan, same) == same


def test_never_rewrite_beats_reanalyse(tmp_path, audio) -> None:
    """Analysing a track the key is forbidden to rewrite is work thrown away.

    A track the key does *not* have is still new, so it is still decoded.
    """
    dest = tmp_path / "usb"
    export([_track(audio[0])], dest, _seed())

    tracks = [_track(audio[0]), _track(audio[1])]
    plan = analysis_plan(dest, tracks, merge=True, refresh_existing=False, reanalyse=True)
    assert needs_analysis(plan, tracks) == [tracks[1]]


# ── The panel and the export decide alike ───────────────────────────────────
#
# The panel decides what to decode before anything is copied; the export
# decides what to write afterwards. When the two disagreed, a track could be
# left undecoded and then written as new, with a flat waveform, or decoded
# and then thrown away.


def test_a_track_the_database_lost_is_decoded_again(tmp_path, audio) -> None:
    """The manifest still knows it, but there is no row to carry over, so the
    export writes it as new — and a new track needs its waveform."""
    dest = tmp_path / "usb"
    export([_track(audio[0])], dest, _seed())
    (dest / DB_DIR / "export.pdb").unlink()

    tracks = [_track(audio[0])]
    assert plan_changes(dest, tracks) == {0: Change.NEW}
    plan = analysis_plan(dest, tracks, merge=True, refresh_existing=True, reanalyse=False)
    assert needs_analysis(plan, tracks) == tracks


def test_keep_leaves_a_listed_track_the_manifest_does_not_know(tmp_path, audio) -> None:
    """A key written before the manifest: the export keeps the row it lists,
    so decoding that track first would be work thrown away."""
    dest = tmp_path / "usb"
    export([_track(audio[0])], dest, _seed())
    (dest / DB_DIR / "track-kommander.json").unlink()

    tracks = [_track(audio[0])]
    plan = analysis_plan(dest, tracks, merge=True, refresh_existing=False, reanalyse=False)
    assert plan == {0: Change.UNCHANGED}
    report = export(tracks, dest, _seed(), merge=True, refresh_existing=False)
    assert (report.unchanged, report.kept) == (1, 1)


def test_reanalyse_rewrites_a_track_nothing_changed_about(tmp_path, audio) -> None:
    """The panel decodes it again; the export must then write what it got."""
    dest = tmp_path / "usb"
    export([_track(audio[0])], dest, _seed())

    report = export([_track(audio[0])], dest, _seed(), merge=True, reanalyse=True)
    assert (report.unchanged, report.replaced) == (0, 1)


def _key_with_a_mix(dest: Path, audio: list[Path]) -> list[DeviceTrack]:
    """A key holding tracks 0 and 1; returned: 0 as it was, 1 re-cued, 2 new."""
    export([_track(audio[0]), _track(audio[1])], dest, _seed())
    return [
        _track(audio[0]),
        _track(audio[1], cues=[anlz.Cue(time_ms=1, hot_cue=1)]),
        _track(audio[2]),
    ]


@pytest.mark.parametrize("merge", [True, False])
@pytest.mark.parametrize("refresh_existing", [True, False])
@pytest.mark.parametrize("reanalyse", [True, False])
def test_the_panel_and_the_export_decide_the_same(
    tmp_path,
    audio,
    monkeypatch,
    merge,
    refresh_existing,
    reanalyse,
) -> None:
    dest = tmp_path / "usb"
    tracks = _key_with_a_mix(dest, audio)
    flags = dict(merge=merge, refresh_existing=refresh_existing, reanalyse=reanalyse)
    planned = analysis_plan(dest, tracks, **flags)

    seen: list[dict[int, Change]] = []
    real = device_export.decide_changes
    monkeypatch.setattr(
        device_export, "decide_changes", lambda *a, **k: seen.append(real(*a, **k)) or seen[-1]
    )
    export(tracks, dest, _seed(), **flags)
    assert seen == [planned]
