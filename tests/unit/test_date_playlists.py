"""Inside Traktor the Kommander date playlists are plain playlists TK keeps through libtk."""

from __future__ import annotations

from core.date_playlists import DateList, plan
from core.playlists import Playlist
from workers.native_playlists import Api, sync

META = {
    "/m/a.mp3": {"import_date": "2026/8/2", "release_date": "2020/1/1"},
    "/m/b.mp3": {"import_date": "2026/7/1", "release_date": ""},
    "/m/c.mp3": {"import_date": "2026/2/14", "release_date": "2026/3/3"},
    "/m/d.mp3": {"import_date": "", "release_date": "1899/1/1"},
}
PREFIX = {"Imported": "imported-", "Released": "released-"}


def test_one_list_per_window_oldest_first_tracks_newest_first() -> None:
    lists = plan(META, {"Imported": 3, "Released": None}, PREFIX)
    assert [d.name for d in lists["Imported"]] == [
        "imported-2026-01 to 03",
        "imported-2026-07 to 09",
    ]
    assert lists["Imported"][1].paths == ("/m/a.mp3", "/m/b.mp3")
    assert lists["Imported"][1].path == "Kommander\\Imported\\imported-2026-07 to 09"
    assert lists["Released"] == []  # off: its lists go


def test_undated_and_pre_1900_tracks_are_left_out() -> None:
    lists = plan(META, {"Imported": None, "Released": 12}, PREFIX)
    assert [(d.name, d.paths) for d in lists["Released"]] == [
        ("released-2020", ("/m/a.mp3",)),
        ("released-2026", ("/m/c.mp3",)),
    ]


class FakeApi:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def api(self) -> Api:
        return Api(
            folder=lambda p: self.calls.append(("folder", p)) or 1,
            delete=lambda p: self.calls.append(("delete", p)) or 1,
            delete_folder=lambda p: self.calls.append(("delete_folder", p)) or 1,
            fill=lambda p, paths: self.calls.append(("fill", p, tuple(paths))) or len(paths),
        )


def _pl(name: str, kind: str, paths=(), smart=False) -> Playlist:
    return Playlist(name=name, path=("Kommander", kind), paths=list(paths), smart=smart)


def test_smartlists_from_the_nml_are_replaced_by_playlists() -> None:
    fake = FakeApi()
    planned = {
        "Imported": [DateList("Imported", "imported-2026-07 to 09", ("/m/a.mp3",))],
        "Released": [],
    }
    existing = [_pl("imported-2026-07 to 09", "Imported", smart=True)]
    result = sync(planned, existing, fake.api())
    assert ("delete", "Kommander\\Imported\\imported-2026-07 to 09") in fake.calls
    assert ("fill", "Kommander\\Imported\\imported-2026-07 to 09", ("/m/a.mp3",)) in fake.calls
    assert result.created == ["Kommander\\Imported\\imported-2026-07 to 09"]


def test_an_unchanged_list_is_left_alone_and_a_stale_one_goes() -> None:
    fake = FakeApi()
    planned = {
        "Imported": [DateList("Imported", "imported-2026-07 to 09", ("/m/a.mp3",))],
        "Released": [],
    }
    existing = [
        _pl("imported-2026-07 to 09", "Imported", ["/m/a.mp3"]),
        _pl("imported-2025-01 to 03", "Imported", ["/m/z.mp3"]),
    ]
    result = sync(planned, existing, fake.api())
    assert result.unchanged == 1
    assert not [c for c in fake.calls if c[0] == "fill"]
    assert result.deleted == ["Kommander\\Imported\\imported-2025-01 to 03"]


def test_a_kind_turned_off_loses_its_lists_and_folder() -> None:
    fake = FakeApi()
    existing = [_pl("released-2020", "Released", ["/m/a.mp3"])]
    sync({"Imported": [], "Released": []}, existing, fake.api())
    assert ("delete", "Kommander\\Released\\released-2020") in fake.calls
    assert ("delete_folder", "Kommander\\Released") in fake.calls


def test_other_lists_under_kommander_are_never_touched() -> None:
    fake = FakeApi()
    existing = [Playlist(name="Results", path=("Kommander",), paths=["/m/a.mp3"])]
    sync({"Imported": [], "Released": []}, existing, fake.api())
    assert all("Results" not in str(c) for c in fake.calls)


def test_outside_traktor_nothing_is_attached(monkeypatch) -> None:
    from workers import native_playlists

    monkeypatch.delenv("TK_EMBED", raising=False)
    assert native_playlists.attach() is None
    assert native_playlists.refresh() is None
