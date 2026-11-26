"""The collection from Traktor's memory reads exactly like the one from collection.nml."""

from __future__ import annotations

import os
import threading
import unicodedata
from dataclasses import replace
from pathlib import Path

import pytest

import core.collection_source as cs
from core.collection_source import (
    collection_metadata,
    collection_paths,
    parse_native_dump,
    set_native_source,
)
from core.playlists import parse_native_playlists, parse_playlists, set_native_playlists
from workers.native_collection import (
    MAX_AGE_S,
    NativeSnapshot,
    attach_collection,
    attach_playlists,
)

NFD_PATH = unicodedata.normalize("NFD", "/m/GRÜV.mp3")


def row(path: str, **kw) -> str:
    """One line of libtk's dump (tk_collection.mm column order)."""
    f = {
        "key": "7",
        "title": "T",
        "artist": "A",
        "album": "Al",
        "genre": "G",
        "label": "L",
        "comment": "",
        "comment2": "",
        "bpm": "124.5",
        "musical_key": "21",
        "rating": "153",
        "play_count": "3",
        "import_date": str(2023 << 16 | 12 << 8 | 29),
        "release_date": "0",
        "bitrate": "320000",
    } | kw
    return "\t".join(
        [f["key"], path]
        + [f[k] for k in ("title", "artist", "album", "genre", "label", "comment", "comment2")]
        + [f[k] for k in ("bpm", "musical_key", "rating", "play_count")]
        + [f[k] for k in ("import_date", "release_date", "bitrate")]
    )


@pytest.fixture(autouse=True)
def nml_again():
    yield
    set_native_source(None)
    set_native_playlists(None)


def test_a_dump_row_has_the_nml_parsers_shape() -> None:
    from core.collection_source import NATIVE_EXPORT_KEYS

    paths, meta = parse_native_dump(row("/m/a.mp3"))
    assert paths == ["/m/a.mp3"]
    tags = meta["/m/a.mp3"]
    # The NML parser's keys, plus the few only the native USB export reads (no other caller).
    assert set(NATIVE_EXPORT_KEYS) <= set(tags)
    assert {k: v for k, v in tags.items() if k not in NATIVE_EXPORT_KEYS} == {
        "title": "T",
        "artist": "A",
        "album": "Al",
        "genre": "G",
        "label": "L",
        "musical_key": 21,
        "traktor_bpm": 124.5,
        "rating": 3,
        "play_count": 3,
        "import_date": "2023/12/29",
        "release_date": "",
        "bitrate": 320000,
    }


def test_paths_come_out_as_the_nml_writes_them_nfc() -> None:
    paths, meta = parse_native_dump(row(NFD_PATH))
    assert paths == [unicodedata.normalize("NFC", NFD_PATH)]
    assert unicodedata.normalize("NFC", NFD_PATH) in meta


def test_missing_values_read_as_the_nml_reads_them() -> None:
    _, meta = parse_native_dump(
        row("/m/a.mp3", bpm="nan", musical_key="-1", rating="-1", play_count="0", bitrate="0")
    )
    m = meta["/m/a.mp3"]
    assert m["traktor_bpm"] == 0.0 and m["musical_key"] == -1 and m["rating"] == 0
    assert m["play_count"] is None and m["bitrate"] is None


def test_non_audio_is_described_but_not_listed_and_bad_lines_are_skipped() -> None:
    paths, meta = parse_native_dump(row("/m/cover.jpg") + "\nnot\ta\trow\n" + row(""))
    assert paths == [] and list(meta) == ["/m/cover.jpg"]


def test_the_native_source_wins_over_the_nml(tmp_path: Path) -> None:
    set_native_source(lambda: (["/m/a.mp3"], {"/m/a.mp3": {"title": "T"}}))
    assert collection_paths(tmp_path / "missing.nml") == ["/m/a.mp3"]
    assert collection_metadata(None) == {"/m/a.mp3": {"title": "T"}}


def test_no_snapshot_or_a_failing_source_falls_back_to_the_nml(monkeypatch) -> None:
    monkeypatch.setattr(cs, "parse_traktor_nml", lambda _p: ["/nml.mp3"])
    set_native_source(lambda: None)
    assert collection_paths(Path("c.nml")) == ["/nml.mp3"]

    def broken():
        raise RuntimeError("libtk gone")

    set_native_source(broken)
    assert collection_paths(Path("c.nml")) == ["/nml.mp3"]
    assert collection_paths(None) == []


# ── the snapshot provider ────────────────────────────────────────────────────


class FakeDump:
    def __init__(self, rows: list[str], result: int | None = None) -> None:
        self.rows, self.result, self.calls = rows, result, 0

    def __call__(self, path: str) -> int:
        self.calls += 1
        Path(path).write_text("\n".join(self.rows), encoding="utf-8")
        return len(self.rows) if self.result is None else self.result


def provider(tmp_path: Path, rows: list[str], *, main: bool = True):
    now = [100.0]
    dump = FakeDump(rows)
    coll = NativeSnapshot(
        dump, tmp_path / "dump.tsv", parse_native_dump, clock=lambda: now[0], on_main=lambda: main
    )
    return coll, dump, now


def test_the_main_thread_gets_a_fresh_dump_when_the_snapshot_is_old(tmp_path) -> None:
    coll, dump, now = provider(tmp_path, [row("/m/a.mp3")])
    assert coll()[0] == ["/m/a.mp3"] and dump.calls == 1
    coll()
    assert dump.calls == 1  # still fresh
    now[0] += MAX_AGE_S + 1
    coll()
    assert dump.calls == 2


def test_other_threads_never_dump_they_get_the_snapshot_as_it_stands(tmp_path) -> None:
    coll, dump, _ = provider(tmp_path, [row("/m/a.mp3")], main=False)
    assert coll() is None and dump.calls == 0  # nothing yet: the NML is read
    coll.refresh()
    assert coll()[0] == ["/m/a.mp3"] and dump.calls == 1


def test_a_refused_dump_keeps_the_last_snapshot(tmp_path) -> None:
    coll, dump, now = provider(tmp_path, [row("/m/a.mp3")])
    coll()
    dump.result = -1
    now[0] += MAX_AGE_S + 1
    assert coll()[0] == ["/m/a.mp3"]


def test_outside_traktor_there_is_no_collection_dump(tmp_path) -> None:
    assert attach_collection(tmp_path) is None and attach_playlists(tmp_path) is None
    assert threading.current_thread() is threading.main_thread()


# ── the playlist tree ────────────────────────────────────────────────────────

PL_DUMP = "\n".join(
    [
        "L\t2\t\tTop list",
        "E\t/m/a.mp3",
        "E\t" + NFD_PATH,
        "L\t3\tSets\x1f2025\tNew & hot",
        "Q\t$IMPORTDATE > 2025/1/1",
        "L\t2\tSets\tEmpty",
        "E\t",
        "junk line",
        "L\tbroken",
    ]
)


def test_a_playlist_dump_reads_like_the_nml_tree() -> None:
    lists = parse_native_playlists(PL_DUMP)
    assert [p.key for p in lists] == [
        ("Top list",),
        ("Sets", "2025", "New & hot"),
        ("Sets", "Empty"),
    ]
    top, smart, empty = lists
    assert top.paths == ["/m/a.mp3", unicodedata.normalize("NFC", NFD_PATH)] and not top.smart
    assert smart.smart and smart.query == "$IMPORTDATE > 2025/1/1" and smart.paths == []
    assert empty.paths == []


def test_the_native_tree_wins_and_a_failing_source_falls_back(monkeypatch, tmp_path) -> None:
    import core.playlists as pl

    monkeypatch.setattr(pl, "_parse_playlists", lambda _p: ["from the nml"])
    set_native_playlists(lambda: parse_native_playlists(PL_DUMP))
    assert len(parse_playlists(tmp_path / "c.nml")) == 3

    def broken():
        raise RuntimeError("libtk gone")

    set_native_playlists(broken)
    assert parse_playlists(tmp_path / "c.nml") == ["from the nml"]


# ── cue points ───────────────────────────────────────────────────────────────

CUE_DUMP = "\n".join(
    [
        "T\t/m/grid.mp3\t124\t1",
        "C\t4\t-1\t812.5\t0\t-1\tBeat Marker",
        "C\t0\t0\t812.5\t0\t-1\tAutoGrid",
        "T\t/m/hot.mp3\t128\t1",
        "C\t4\t-1\t10\t0\t-1\t",
        "C\t0\t2\t30000\t0\t-1\tDrop",
        "T\t/m/unsure.mp3\t126\t0",
        "C\t4\t-1\t5\t0\t-1\t",
        "T\t/m/nogrid.mp3\t120\t1",
        "T\t/m/cover.jpg\t120\t1",
    ]
)


def test_a_cue_dump_reads_into_tracks_with_cues() -> None:
    from core.cue_points import Cue, parse_native_cues

    tracks = parse_native_cues(CUE_DUMP)
    assert [t.path for t in tracks][:2] == ["/m/grid.mp3", "/m/hot.mp3"]
    assert tracks[0].bpm == 124.0 and tracks[0].cues[0] == Cue(4, -1, 812.5, "Beat Marker")
    assert tracks[1].cues[1].name == "Drop"  # names tell TK's own cues from the user's
    assert tracks[2].bpm == 0.0  # BPM quality 0: Traktor does not trust it, as in the NML


def test_hotcue_eligibility_reads_the_same_from_memory(tmp_path) -> None:
    from core.collection_source import set_native_cues
    from core.cue_points import parse_native_cues
    from core.traktor import hotcue_eligibility

    set_native_cues(lambda: parse_native_cues(CUE_DUMP))
    try:
        candidates, skipped = hotcue_eligibility(tmp_path / "missing.nml")
        forced, _ = hotcue_eligibility(tmp_path / "missing.nml", include_existing=True)
    finally:
        set_native_cues(None)
    assert candidates == [("/m/grid.mp3", 124.0, 812.5)]  # the .jpg is not a track
    assert skipped == {"already has hotcues": 1, "no BPM": 1, "no beat grid": 1}
    assert ("/m/hot.mp3", 128.0, 10.0) in forced


NML_CUES = """<?xml version="1.0" encoding="UTF-8"?>
<NML VERSION="19"><COLLECTION ENTRIES="2">
<ENTRY TITLE="g"><LOCATION DIR="/:m/:" FILE="grid.mp3" VOLUME="Macintosh HD"></LOCATION>
<TEMPO BPM="124" BPM_QUALITY="100"></TEMPO>
<CUE_V2 NAME="Beat Marker" TYPE="4" START="812.5" HOTCUE="-1"></CUE_V2>
<CUE_V2 NAME="AutoGrid" TYPE="0" START="812.5" HOTCUE="0"></CUE_V2></ENTRY>
<ENTRY TITLE="h"><LOCATION DIR="/:m/:" FILE="hot.mp3" VOLUME="Macintosh HD"></LOCATION>
<TEMPO BPM="128" BPM_QUALITY="100"></TEMPO>
<CUE_V2 TYPE="4" START="10" HOTCUE="-1"></CUE_V2>
<CUE_V2 NAME="Drop" TYPE="0" START="30000" HOTCUE="2"></CUE_V2></ENTRY>
</COLLECTION></NML>"""


def test_the_nml_and_memory_give_the_same_tracks(tmp_path) -> None:
    from core.cue_points import parse_native_cues
    from core.traktor import nml_cues

    nml = tmp_path / "collection.nml"
    nml.write_text(NML_CUES, encoding="utf-8")
    # CUE_DUMP is a macOS dump ("/m/grid.mp3"); the NML reader spells its paths the system's way
    # (os.path.normpath: backslashes on Windows), and so does libtk.dll's dump there.
    native = [replace(t, path=os.path.normpath(t.path)) for t in parse_native_cues(CUE_DUMP)[:2]]
    assert nml_cues(nml) == native
