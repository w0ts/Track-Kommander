"""The review dialog shows the track that is selected — also after one is
dealt with. Removing the current row used to leave the pane one track ahead
of the list, so the candidates and the ▶ row belonged to another track."""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication

import core.db as db
from core import discogs_store
from core.discogs_store import Candidate, Lookup

TITLES = ["Escape Lane", "Final Call", "Ascent", "Freefall"]


class _NoNetwork:
    def __init__(self, *_a, **_k) -> None:
        pass

    def release(self, release_id):
        return {"id": release_id, "title": "Life Is A Better Place", "tracklist": []}


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture()
def dialog(tmp_path, monkeypatch, qapp):
    monkeypatch.setattr(db, "_db_path", lambda: tmp_path / "t.db")
    db.init_db()
    for i, title in enumerate(TITLES):
        path = f"/m/{i}.mp3"
        db.upsert_track(path, f"{i}.mp3", 120.0, 1, True, "8A", 300.0)
        db.update_track_metadata(path, "Id.", "Life is a better place", title, "")
        cand = Candidate(9, "I.D. - Life Is A Better Place", 2012, "L", "C", "CD", 0.84)
        discogs_store.save(Lookup(path, "review", 0.84 - i / 100, None, (cand,)))

    from ui import discogs_dialogs

    monkeypatch.setattr(discogs_dialogs, "DiscogsClient", _NoNetwork)
    d = discogs_dialogs.DiscogsReviewDialog("")
    yield d
    d.done(0)


def _shown(d) -> str:
    return d._track_info.text().split("</b>")[0].removeprefix("<b>")


def _selected(d) -> str:
    return d._tracks.currentItem().text().split("  ", 1)[1]


def test_the_surest_track_comes_first(dialog) -> None:
    assert _selected(dialog) == "Id. — Escape Lane"


def test_after_skipping_the_pane_shows_the_newly_selected_track(dialog) -> None:
    dialog._tracks.setCurrentRow(1)
    dialog._skip_track()
    assert _selected(dialog) == "Id. — Ascent"
    assert _shown(dialog) == "Id. — Ascent"


def test_after_the_last_row_goes_the_one_above_is_shown(dialog) -> None:
    dialog._tracks.setCurrentRow(3)
    dialog._skip_track()
    assert _selected(dialog) == _shown(dialog) == "Id. — Ascent"


def test_reviewing_everything_empties_the_pane(dialog) -> None:
    for _ in TITLES:
        dialog._skip_track()
    assert dialog._tracks.count() == 0
    assert dialog._track_info.text() == ""
