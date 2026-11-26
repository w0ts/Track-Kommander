"""Reading the genre labels that go with the CLAP text embeddings.

The embeddings file used to be opened with pickle allowed, because its two
lists of names were saved as object arrays. Unpickling runs whatever the file
says to run, so a file that is only ever numbers and names was being given
the power to execute code.

It is opened without now. A file from an older export still has its names as
object arrays, which cannot be read that way — for those the names come from
the label list the app ships, which is the list the export was made from.
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from core import clap_analysis
from core.clap_analysis import genre_labels_from

LABELS = ["Techno", "House", "Tech House"]


def _embeddings(tmp_path, labels, *, as_objects: bool, genres: int = 3):
    arrays = {f"genre_{i}": np.zeros(4, dtype=np.float32) for i in range(genres)}
    arrays["genre_labels"] = np.array(labels, dtype=object if as_objects else None)
    path = tmp_path / "clap_text_embeddings.npz"
    np.savez_compressed(path, **arrays)
    return path


@pytest.fixture
def shipped(tmp_path, monkeypatch):
    """Stand in for the label list the app ships."""
    path = tmp_path / "clap_genre_labels.json"
    path.write_text(json.dumps(LABELS), encoding="utf-8")
    monkeypatch.setattr(clap_analysis, "_SHIPPED_LABELS", path)
    return path


def test_names_saved_as_text_are_read_from_the_file(tmp_path, shipped) -> None:
    shipped.write_text(json.dumps(["not", "these", "ones"]))
    path = _embeddings(tmp_path, LABELS, as_objects=False)

    with np.load(path, allow_pickle=False) as data:
        assert genre_labels_from(data) == LABELS


def test_names_from_an_older_export_come_from_the_shipped_list(tmp_path, shipped) -> None:
    path = _embeddings(tmp_path, LABELS, as_objects=True)

    with np.load(path, allow_pickle=False) as data:
        assert genre_labels_from(data) == LABELS


def test_a_shipped_list_of_another_length_is_refused(tmp_path, shipped) -> None:
    """Names paired with the wrong embeddings would label every track wrong,
    and look like it was working."""
    path = _embeddings(tmp_path, LABELS + ["Trance"], as_objects=True, genres=4)

    with np.load(path, allow_pickle=False) as data, pytest.raises(RuntimeError, match="export"):
        genre_labels_from(data)


def test_the_list_the_app_ships_is_there() -> None:
    labels = json.loads(clap_analysis._SHIPPED_LABELS.read_text(encoding="utf-8"))
    assert labels and all(isinstance(g, str) for g in labels)


def test_the_backend_does_not_allow_pickle() -> None:
    source = clap_analysis.__file__
    with open(source, encoding="utf-8") as fh:
        assert "allow_pickle=True" not in fh.read()
